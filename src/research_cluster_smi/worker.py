"""Generic SMI worker manager."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .account_gate import AccountGate, AccountPermit, DEFAULT_ACCOUNT_RESOURCES
from .agents import resolve_agent_command
from .smi_core import SMIRuntime
from .worktree import WorktreeManager, find_repo_root


@dataclass
class ActiveProcess:
    assignment: dict
    process: subprocess.Popen[str]
    command: list[str] | str
    result_path: Path
    stdout_path: Path
    stderr_path: Path
    worktree_info: dict | None
    account_permit: AccountPermit | None


def agent_subprocess_command(command: str) -> tuple[list[str] | str, bool]:
    """Return a subprocess command and whether it needs shell execution."""
    if os.name == "nt":
        return command, True
    return shlex.split(command), False


class WorkerManager:
    """Claim tasks for one lane and execute them."""

    def __init__(
        self,
        runtime: SMIRuntime,
        run_id: str,
        lane: str,
        *,
        slots: int = 1,
        dry_run: bool = False,
        agent_command: str | None = None,
        agent_preset: str | None = None,
        codex_model: str | None = None,
        codex_sandbox: str | None = None,
        codex_profile: str | None = None,
        codex_extra_args: str | None = None,
        tick_interval: float = 2.0,
        use_worktrees: bool = False,
        repo_root: str | None = None,
        worktree_root: str | None = None,
        account_gate: AccountGate | None = None,
        account_gated_lanes: set[str] | None = None,
        account_gate_ttl_sec: int = 14_400,
        parallel: bool = False,
    ) -> None:
        self.runtime = runtime
        self.run_id = run_id
        self.lane = lane
        self.slots = slots
        self.dry_run = dry_run
        self.agent_command = resolve_agent_command(
            agent_command=agent_command,
            agent_preset=agent_preset,
            codex_model=codex_model,
            codex_sandbox=codex_sandbox,
            codex_profile=codex_profile,
            codex_extra_args=codex_extra_args,
        )
        self.tick_interval = tick_interval
        self.slot_ids = [f"{lane}-{index:02d}" for index in range(slots)]
        self.account_gate = account_gate
        self.account_gated_lanes = (
            account_gated_lanes if account_gated_lanes is not None else set(DEFAULT_ACCOUNT_RESOURCES)
        )
        self.account_gate_ttl_sec = account_gate_ttl_sec
        self.parallel = parallel
        self.active: dict[str, ActiveProcess] = {}
        self.worktree_manager = None
        if use_worktrees:
            root = Path(repo_root).resolve() if repo_root else find_repo_root()
            self.worktree_manager = WorktreeManager(root, worktree_root)

    def register_slots(self) -> None:
        for slot_id in self.slot_ids:
            self.runtime.register_slot(self.run_id, self.lane, slot_id)

    def tick(self, *, start_new: bool = True) -> dict[str, int]:
        started = 0
        completed = self._poll_active()
        if not start_new:
            return {"started": started, "completed": completed}
        for slot_id in self.slot_ids:
            if slot_id in self.active:
                continue
            account_permit = self._acquire_account_permit(slot_id)
            if self._needs_account_permit() and account_permit is None:
                continue
            assignment = self.runtime.claim_next_task(self.run_id, slot_id)
            if assignment is None:
                self._release_account_permit(account_permit)
                continue
            started += 1
            self.runtime.start_attempt(self.run_id, assignment.attempt_id)
            if self.parallel and not self.dry_run:
                if not self._start_assignment(assignment.as_dict(), account_permit=account_permit):
                    started -= 1
                continue
            try:
                if self._execute_assignment(
                    assignment.as_dict(),
                    account_permit=account_permit.as_dict() if account_permit else None,
                ):
                    completed += 1
            finally:
                self._release_account_permit(account_permit)
        return {"started": started, "completed": completed}

    def run(self, *, once: bool = False, max_ticks: int | None = None, exit_when_idle: bool = False) -> None:
        self.register_slots()
        ticks = 0
        while True:
            ticks += 1
            summary = self.tick()
            print(
                f"tick={ticks} lane={self.lane} "
                f"started={summary['started']} completed={summary['completed']}"
            )
            if once:
                while self.active:
                    time.sleep(self.tick_interval)
                    ticks += 1
                    summary = self.tick(start_new=False)
                    print(
                        f"tick={ticks} lane={self.lane} "
                        f"started={summary['started']} completed={summary['completed']}"
                    )
                break
            if exit_when_idle and self.is_idle():
                break
            if max_ticks is not None and ticks >= max_ticks:
                break
            time.sleep(self.tick_interval)

    def _needs_account_permit(self) -> bool:
        return self.account_gate is not None and self.lane in self.account_gated_lanes

    def _acquire_account_permit(self, slot_id: str) -> AccountPermit | None:
        if not self._needs_account_permit():
            return None
        assert self.account_gate is not None
        return self.account_gate.acquire(
            self.lane,
            holder=f"{self.run_id}:{slot_id}",
            run_id=self.run_id,
            slot_id=slot_id,
            ttl_sec=self.account_gate_ttl_sec,
        )

    def _release_account_permit(self, permit: AccountPermit | None) -> None:
        if permit is None or self.account_gate is None:
            return
        self.account_gate.release(permit.permit_id)

    def _renew_account_permit(self, permit: AccountPermit | None) -> None:
        if permit is None or self.account_gate is None:
            return
        self.account_gate.renew(permit.permit_id, ttl_sec=self.account_gate_ttl_sec)

    def _agent_env(self, assignment: dict, account_permit: dict | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["SMI_RUN_ID"] = self.run_id
        env["SMI_LANE"] = assignment["lane"]
        env["SMI_SLOT_ID"] = assignment["slot_id"]
        env["SMI_TASK_ID"] = assignment["task_id"]
        env["SMI_ATTEMPT_ID"] = assignment["attempt_id"]
        metadata = assignment.get("metadata") or {}
        if metadata:
            env["SMI_TASK_METADATA_JSON"] = json.dumps(metadata, sort_keys=True)
        if account_permit:
            env["SMI_ACCOUNT_PERMIT_JSON"] = json.dumps(account_permit, sort_keys=True)
        return env

    def _prepare_execution(self, assignment: dict) -> tuple[Path, Path | None, dict | None, str]:
        task_id = assignment["task_id"]
        result_dir = self.runtime.run_dir / "results" / task_id
        result_dir.mkdir(parents=True, exist_ok=True)
        worker_cwd = None
        worktree_info = None
        if self.worktree_manager is not None:
            worker_cwd = self.worktree_manager.ensure_worktree(self.run_id, assignment["slot_id"])
            worktree_info = {
                "branch": self.worktree_manager.branch_name(self.run_id, assignment["slot_id"]),
                "path": str(worker_cwd),
            }

        prompt_path = assignment.get("prompt_path")
        prompt = ""
        if prompt_path:
            prompt = Path(prompt_path).read_text(encoding="utf-8")
        return result_dir, worker_cwd, worktree_info, prompt

    def _start_assignment(self, assignment: dict, *, account_permit: AccountPermit | None = None) -> bool:
        task_id = assignment["task_id"]
        attempt_id = assignment["attempt_id"]
        account_permit_dict = account_permit.as_dict() if account_permit else None
        try:
            result_dir, worker_cwd, worktree_info, prompt = self._prepare_execution(assignment)
            result_path = result_dir / "result.json"
            stdout_path = result_dir / "stdout.txt"
            stderr_path = result_dir / "stderr.txt"
            command, use_shell = agent_subprocess_command(self.agent_command)
            with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
                "w", encoding="utf-8"
            ) as stderr_handle:
                process = subprocess.Popen(
                    command,
                    shell=use_shell,
                    stdin=subprocess.PIPE,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                    cwd=worker_cwd,
                    env=self._agent_env(assignment, account_permit_dict),
                )
                if process.stdin:
                    process.stdin.write(prompt)
                    process.stdin.close()
            self.active[assignment["slot_id"]] = ActiveProcess(
                assignment=assignment,
                process=process,
                command=command,
                result_path=result_path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                worktree_info=worktree_info,
                account_permit=account_permit,
            )
            return True
        except Exception as exc:
            self.runtime.fail_attempt(
                self.run_id,
                attempt_id,
                failure_class="worker_exception",
                diagnostics=str(exc),
                retryable=True,
            )
            self._release_account_permit(account_permit)
            return False

    def _poll_active(self) -> int:
        completed_count = 0
        for slot_id, active in list(self.active.items()):
            returncode = active.process.poll()
            if returncode is None:
                self._renew_account_permit(active.account_permit)
                continue
            assignment = active.assignment
            task_id = assignment["task_id"]
            attempt_id = assignment["attempt_id"]
            stdout = active.stdout_path.read_text(encoding="utf-8") if active.stdout_path.exists() else ""
            stderr = active.stderr_path.read_text(encoding="utf-8") if active.stderr_path.exists() else ""
            preserved = None
            if self.worktree_manager is not None:
                preserved = self.worktree_manager.preserve_changes(self.run_id, slot_id, task_id).__dict__
            result = {
                "command": active.command,
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
                "worktree": active.worktree_info,
                "account_permit": active.account_permit.as_dict() if active.account_permit else None,
                "preserved_changes": preserved,
                "stdout_path": str(active.stdout_path),
                "stderr_path": str(active.stderr_path),
            }
            active.result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            if returncode == 0:
                self.runtime.complete_attempt(self.run_id, attempt_id, result=result)
                completed_count += 1
            else:
                self.runtime.fail_attempt(
                    self.run_id,
                    attempt_id,
                    failure_class="agent_command_failed",
                    diagnostics=stderr or stdout,
                    retryable=True,
                )
            self._release_account_permit(active.account_permit)
            del self.active[slot_id]
        return completed_count

    def is_idle(self) -> bool:
        if self.active:
            return False
        row = self.runtime.conn.execute(
            """
            SELECT COUNT(*) FROM tasks
            WHERE run_id=? AND lane=? AND status IN ('ready', 'retry_ready', 'leased', 'running')
            """,
            (self.run_id, self.lane),
        ).fetchone()
        return int(row[0]) == 0

    def _execute_assignment(self, assignment: dict, *, account_permit: dict | None = None) -> bool:
        task_id = assignment["task_id"]
        attempt_id = assignment["attempt_id"]
        result_dir, worker_cwd, worktree_info, prompt = self._prepare_execution(assignment)
        result_path = result_dir / "result.json"
        prompt_path = assignment.get("prompt_path")

        if self.dry_run:
            result = {
                "type": "dry_run",
                "task_id": task_id,
                "prompt_path": prompt_path,
                "worktree": worktree_info,
                "account_permit": account_permit,
                "message": "Dry run completed without launching an agent.",
            }
            result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            self.runtime.complete_attempt(self.run_id, attempt_id, result=result)
            return True

        command, use_shell = agent_subprocess_command(self.agent_command)
        try:
            completed = subprocess.run(
                command,
                shell=use_shell,
                input=prompt,
                text=True,
                capture_output=True,
                check=False,
                cwd=worker_cwd,
                env=self._agent_env(assignment, account_permit),
            )
            preserved = None
            if self.worktree_manager is not None:
                preserved = self.worktree_manager.preserve_changes(self.run_id, assignment["slot_id"], task_id).__dict__
            result = {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "worktree": worktree_info,
                "account_permit": account_permit,
                "preserved_changes": preserved,
            }
            result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            if completed.returncode == 0:
                self.runtime.complete_attempt(self.run_id, attempt_id, result=result)
                return True
            self.runtime.fail_attempt(
                self.run_id,
                attempt_id,
                failure_class="agent_command_failed",
                diagnostics=completed.stderr or completed.stdout,
                retryable=True,
            )
            return False
        except Exception as exc:
            self.runtime.fail_attempt(
                self.run_id,
                attempt_id,
                failure_class="worker_exception",
                diagnostics=str(exc),
                retryable=True,
            )
            return False
