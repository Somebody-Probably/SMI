"""Generic SMI worker manager."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path

from .smi_core import SMIRuntime


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
        tick_interval: float = 2.0,
    ) -> None:
        self.runtime = runtime
        self.run_id = run_id
        self.lane = lane
        self.slots = slots
        self.dry_run = dry_run
        self.agent_command = agent_command or os.environ.get("SMI_AGENT_COMMAND", "claude --print")
        self.tick_interval = tick_interval
        self.slot_ids = [f"{lane}-{index:02d}" for index in range(slots)]

    def register_slots(self) -> None:
        for slot_id in self.slot_ids:
            self.runtime.register_slot(self.run_id, self.lane, slot_id)

    def tick(self) -> dict[str, int]:
        started = 0
        completed = 0
        for slot_id in self.slot_ids:
            assignment = self.runtime.claim_next_task(self.run_id, slot_id)
            if assignment is None:
                continue
            started += 1
            self.runtime.start_attempt(self.run_id, assignment.attempt_id)
            if self._execute_assignment(assignment.as_dict()):
                completed += 1
        return {"started": started, "completed": completed}

    def run(self, *, once: bool = False, max_ticks: int | None = None) -> None:
        self.register_slots()
        ticks = 0
        while True:
            ticks += 1
            summary = self.tick()
            print(
                f"tick={ticks} lane={self.lane} "
                f"started={summary['started']} completed={summary['completed']}"
            )
            if once or (max_ticks is not None and ticks >= max_ticks):
                break
            time.sleep(self.tick_interval)

    def _execute_assignment(self, assignment: dict) -> bool:
        task_id = assignment["task_id"]
        attempt_id = assignment["attempt_id"]
        result_dir = self.runtime.run_dir / "results" / task_id
        result_dir.mkdir(parents=True, exist_ok=True)
        result_path = result_dir / "result.json"

        prompt_path = assignment.get("prompt_path")
        prompt = ""
        if prompt_path:
            prompt = Path(prompt_path).read_text(encoding="utf-8")

        if self.dry_run:
            result = {
                "type": "dry_run",
                "task_id": task_id,
                "prompt_path": prompt_path,
                "message": "Dry run completed without launching an agent.",
            }
            result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
            self.runtime.complete_attempt(self.run_id, attempt_id, result=result)
            return True

        command = shlex.split(self.agent_command)
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                check=False,
            )
            result = {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
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

