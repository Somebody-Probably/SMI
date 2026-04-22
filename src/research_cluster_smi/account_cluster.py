"""Cluster operations routed through the account SMI gate."""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .account_gate import AccountGate, AccountPermit
from .cluster_cli import (
    DEFAULT_SOCKET_DIR,
    choose_cluster,
    control_check,
    expand,
    load_config,
    sbatch_defaults_for,
    ssh_base,
)


TERMINAL_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "COMPLETED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REQUEUED",
    "REVOKED",
    "SPECIAL_EXIT",
    "TIMEOUT",
}


def command_record(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "command": completed.args,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def parse_job_id(stdout: str) -> str:
    first = stdout.strip().splitlines()[0] if stdout.strip() else ""
    match = re.search(r"\d+", first)
    if not match:
        raise RuntimeError(f"Could not parse sbatch job id from output: {stdout!r}")
    return match.group(0)


def base_state(state: str) -> str:
    return state.strip().split()[0].split("+")[0]


class AccountClusterRunner:
    """Run small cluster workflows while acquiring account-level permits."""

    def __init__(
        self,
        gate: AccountGate,
        *,
        cluster_config: str | Path = "config/clusters.json",
        permit_timeout_sec: float = 300.0,
        permit_poll_interval: float = 2.0,
    ) -> None:
        self.gate = gate
        self.cluster_config = Path(cluster_config)
        self.config = load_config(self.cluster_config)
        self.permit_timeout_sec = permit_timeout_sec
        self.permit_poll_interval = permit_poll_interval

    @contextmanager
    def permit(
        self,
        resource: str,
        *,
        holder: str,
        run_id: str | None = None,
        task_id: str | None = None,
        slot_id: str | None = None,
        timeout_sec: float | None = None,
    ) -> Iterator[AccountPermit]:
        deadline = time.monotonic() + (self.permit_timeout_sec if timeout_sec is None else timeout_sec)
        permit = None
        while permit is None:
            permit = self.gate.acquire(
                resource,
                holder=holder,
                run_id=run_id,
                task_id=task_id,
                slot_id=slot_id,
            )
            if permit is not None:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for account permit {resource!r} held by {holder!r}")
            time.sleep(self.permit_poll_interval)
        try:
            yield permit
        finally:
            self.gate.release(permit.permit_id)

    def cluster(self, cluster_name: str) -> dict[str, Any]:
        _, cluster = choose_cluster(self.config, cluster_name)
        return cluster

    def run_capture(self, argv: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
        )

    def ensure_session(
        self,
        cluster_name: str,
        *,
        open_if_missing: bool = False,
        touch_command: str = "true",
    ) -> dict[str, Any]:
        cluster = self.cluster(cluster_name)
        records: list[dict[str, Any]] = []
        check = control_check(cluster)
        records.append({"phase": "control_check", **command_record(check)})
        if check.returncode != 0 and open_if_missing:
            socket_dir = Path(DEFAULT_SOCKET_DIR).expanduser()
            socket_dir.mkdir(parents=True, exist_ok=True)
            opened = self.run_capture(["ssh", "-f", "-N", cluster["ssh_alias"]])
            records.append({"phase": "open_session", **command_record(opened)})
            if opened.returncode != 0:
                return {"ok": False, "cluster": cluster_name, "records": records}
        elif check.returncode != 0:
            return {"ok": False, "cluster": cluster_name, "records": records}

        touched = self.run_capture(ssh_base(cluster) + [touch_command])
        records.append({"phase": "touch", **command_record(touched)})
        return {"ok": touched.returncode == 0, "cluster": cluster_name, "records": records}

    def touch_sessions(
        self,
        cluster_names: list[str],
        *,
        open_if_missing: bool = False,
        touch_command: str = "true",
    ) -> list[dict[str, Any]]:
        results = []
        for cluster_name in cluster_names:
            holder = f"account-watch:{cluster_name}:touch"
            try:
                with self.permit("remote_monitor", holder=holder, timeout_sec=0):
                    results.append(
                        self.ensure_session(
                            cluster_name,
                            open_if_missing=open_if_missing,
                            touch_command=touch_command,
                        )
                    )
            except TimeoutError as exc:
                results.append({"ok": False, "cluster": cluster_name, "skipped": True, "error": str(exc)})
        return results

    def queue_snapshot(self, cluster_names: list[str], *, limit: int = 40) -> list[dict[str, Any]]:
        results = []
        for cluster_name in cluster_names:
            cluster = self.cluster(cluster_name)
            user = cluster.get("user") or ""
            quoted_user = shlex.quote(user)
            remote_cmd = (
                "printf 'SQUEUE\\n'; "
                f"squeue -u {quoted_user} -h -o '%i|%T|%M|%D|%R|%j' 2>/dev/null | head -n {int(limit)} || true; "
                "printf 'SACCT\\n'; "
                f"sacct -u {quoted_user} --starttime=now-12hours -X "
                "--noheader --parsable2 --format=JobIDRaw,JobName,State,Elapsed,ExitCode "
                f"2>/dev/null | tail -n {int(limit)} || true"
            )
            holder = f"account-watch:{cluster_name}:queue"
            try:
                with self.permit("remote_monitor", holder=holder, timeout_sec=0):
                    completed = self.run_capture(ssh_base(cluster) + [remote_cmd])
                results.append(
                    {
                        "ok": completed.returncode == 0,
                        "cluster": cluster_name,
                        "user": user,
                        "record": command_record(completed),
                        "parsed": self.parse_queue_output(completed.stdout),
                    }
                )
            except TimeoutError as exc:
                results.append({"ok": False, "cluster": cluster_name, "skipped": True, "error": str(exc)})
        return results

    @staticmethod
    def parse_queue_output(stdout: str) -> dict[str, list[dict[str, str]]]:
        section = ""
        squeue: list[dict[str, str]] = []
        sacct: list[dict[str, str]] = []
        for line in stdout.splitlines():
            if line == "SQUEUE":
                section = "squeue"
                continue
            if line == "SACCT":
                section = "sacct"
                continue
            if not line.strip():
                continue
            if section == "squeue":
                fields = line.split("|")
                squeue.append(
                    {
                        "job_id": fields[0] if len(fields) > 0 else "",
                        "state": fields[1] if len(fields) > 1 else "",
                        "elapsed": fields[2] if len(fields) > 2 else "",
                        "nodes": fields[3] if len(fields) > 3 else "",
                        "reason": fields[4] if len(fields) > 4 else "",
                        "name": fields[5] if len(fields) > 5 else "",
                    }
                )
            elif section == "sacct":
                fields = line.split("|")
                sacct.append(
                    {
                        "job_id": fields[0] if len(fields) > 0 else "",
                        "name": fields[1] if len(fields) > 1 else "",
                        "state": fields[2] if len(fields) > 2 else "",
                        "elapsed": fields[3] if len(fields) > 3 else "",
                        "exit_code": fields[4] if len(fields) > 4 else "",
                    }
                )
        return {"squeue": squeue, "sacct": sacct}

    def write_smoke_script(self, path: Path, *, label: str, sleep_seconds: int = 2) -> None:
        safe_label = label.replace('"', "'")
        path.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "#SBATCH --job-name=smi-e2e-smoke",
                    "#SBATCH --time=00:03:00",
                    "#SBATCH --nodes=1",
                    "#SBATCH --ntasks=1",
                    "#SBATCH --cpus-per-task=1",
                    "#SBATCH --output=logs/%x-%j.out",
                    "#SBATCH --error=logs/%x-%j.err",
                    "",
                    "set -euo pipefail",
                    "mkdir -p logs results",
                    'result_path="results/smi-e2e-${SLURM_JOB_ID}.txt"',
                    "{",
                    '  echo "SMI account smoke test"',
                    f'  echo "label={safe_label}"',
                    '  echo "cluster=${SLURM_CLUSTER_NAME:-unknown}"',
                    '  echo "job_id=${SLURM_JOB_ID:-unknown}"',
                    '  echo "host=$(hostname)"',
                    '  echo "user=$(whoami)"',
                    '  echo "workdir=$(pwd)"',
                    '  echo "started_utc=$(date -u \'+%Y-%m-%dT%H:%M:%SZ\')"',
                    '} > "${result_path}"',
                    f"sleep {int(sleep_seconds)}",
                    'echo "completed_utc=$(date -u \'+%Y-%m-%dT%H:%M:%SZ\')" >> "${result_path}"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def submit_smoke_job(
        self,
        cluster_name: str,
        *,
        label: str,
        local_work_dir: Path,
        remote_dir: str | None = None,
        profile: str | None = None,
        sbatch_args: list[str] | None = None,
        open_if_missing: bool = False,
    ) -> dict[str, Any]:
        cluster = self.cluster(cluster_name)
        remote_base = expand(remote_dir or cluster.get("remote_project_dir"), cluster)
        if not remote_base:
            raise RuntimeError(f"No remote directory configured for cluster {cluster_name}")
        remote_run_dir = posixpath.join(remote_base, "smi-account-tests", label, cluster_name)
        local_work_dir.mkdir(parents=True, exist_ok=True)
        script_path = local_work_dir / "smi-e2e-smoke.sbatch"
        self.write_smoke_script(script_path, label=label)
        remote_script = posixpath.join(remote_run_dir, script_path.name)

        records: list[dict[str, Any]] = []
        session = self.ensure_session(cluster_name, open_if_missing=open_if_missing)
        records.append({"phase": "session", **session})
        if not session["ok"]:
            raise RuntimeError(f"No usable SSH session for {cluster_name}: {session}")

        mkdir = self.run_capture(ssh_base(cluster) + [f"mkdir -p {shlex.quote(remote_run_dir)}"])
        records.append({"phase": "mkdir", **command_record(mkdir)})
        if mkdir.returncode != 0:
            raise RuntimeError(f"Could not create remote directory on {cluster_name}: {mkdir.stderr}")

        upload = self.run_capture(["scp", str(script_path), f"{cluster['ssh_alias']}:{remote_script}"])
        records.append({"phase": "upload", **command_record(upload)})
        if upload.returncode != 0:
            raise RuntimeError(f"Could not upload smoke script to {cluster_name}: {upload.stderr}")

        defaults = [expand(flag, cluster) for flag in sbatch_defaults_for(cluster, profile)]
        extra = list(sbatch_args or [])
        remote_cmd = " ".join(
            [
                "cd",
                shlex.quote(remote_run_dir),
                "&&",
                "mkdir -p logs results",
                "&&",
                "sbatch",
                "--parsable",
                *[shlex.quote(part) for part in defaults],
                *[shlex.quote(part) for part in extra],
                shlex.quote(script_path.name),
            ]
        )
        submitted = self.run_capture(ssh_base(cluster) + [remote_cmd])
        records.append({"phase": "submit", **command_record(submitted)})
        if submitted.returncode != 0:
            raise RuntimeError(f"Could not submit smoke job to {cluster_name}: {submitted.stderr}")

        job_id = parse_job_id(submitted.stdout)
        return {
            "cluster": cluster_name,
            "job_id": job_id,
            "remote_dir": remote_run_dir,
            "local_script": str(script_path),
            "records": records,
        }

    def query_job(self, cluster_name: str, job_id: str) -> dict[str, Any]:
        cluster = self.cluster(cluster_name)
        quoted_job = shlex.quote(str(job_id))
        remote_cmd = (
            "sacct_line=$(sacct -n -P -j "
            + quoted_job
            + " -X --format=JobIDRaw,State,ExitCode,Elapsed 2>/dev/null | awk 'NF {print; exit}'); "
            "squeue_line=$(squeue -h -j "
            + quoted_job
            + " -o '%T|%M|%R' 2>/dev/null | head -n 1); "
            'printf "sacct=%s\\nsqueue=%s\\n" "$sacct_line" "$squeue_line"'
        )
        completed = self.run_capture(ssh_base(cluster) + [remote_cmd])
        state = "UNKNOWN"
        exit_code = ""
        elapsed = ""
        squeue = ""
        for line in completed.stdout.splitlines():
            if line.startswith("sacct=") and line != "sacct=":
                fields = line.removeprefix("sacct=").split("|")
                if len(fields) >= 2:
                    state = fields[1]
                if len(fields) >= 3:
                    exit_code = fields[2]
                if len(fields) >= 4:
                    elapsed = fields[3]
            elif line.startswith("squeue=") and line != "squeue=":
                squeue = line.removeprefix("squeue=")
                if state == "UNKNOWN":
                    state = squeue.split("|", 1)[0]
        return {
            "cluster": cluster_name,
            "job_id": job_id,
            "state": state,
            "state_base": base_state(state),
            "exit_code": exit_code,
            "elapsed": elapsed,
            "squeue": squeue,
            "terminal": base_state(state) in TERMINAL_STATES,
            "record": command_record(completed),
        }

    def harvest(self, cluster_name: str, remote_dir: str, local_dir: Path) -> dict[str, Any]:
        cluster = self.cluster(cluster_name)
        local_dir.mkdir(parents=True, exist_ok=True)
        completed = self.run_capture(["rsync", "-rlptz", f"{cluster['ssh_alias']}:{remote_dir}/", f"{local_dir}/"])
        return {"cluster": cluster_name, "remote_dir": remote_dir, "local_dir": str(local_dir), **command_record(completed)}

    def run_smoke_job(
        self,
        cluster_name: str,
        *,
        label: str,
        local_output_dir: str | Path,
        remote_dir: str | None = None,
        profile: str | None = None,
        sbatch_args: list[str] | None = None,
        poll_interval: float = 10.0,
        timeout_sec: float = 900.0,
        open_if_missing: bool = False,
        run_id: str | None = None,
        task_id: str | None = None,
        slot_id: str | None = None,
    ) -> dict[str, Any]:
        local_cluster_dir = Path(local_output_dir).expanduser().resolve() / cluster_name
        local_cluster_dir.mkdir(parents=True, exist_ok=True)
        summary_path = local_cluster_dir / "summary.json"
        holder_base = f"{label}:{cluster_name}"

        with self.permit(
            "remote_submit", holder=f"{holder_base}:submit", run_id=run_id, task_id=task_id, slot_id=slot_id
        ):
            submission = self.submit_smoke_job(
                cluster_name,
                label=label,
                local_work_dir=local_cluster_dir,
                remote_dir=remote_dir,
                profile=profile,
                sbatch_args=sbatch_args,
                open_if_missing=open_if_missing,
            )

        job_id = submission["job_id"]
        deadline = time.monotonic() + timeout_sec
        polls: list[dict[str, Any]] = []
        final_state: dict[str, Any] | None = None
        while time.monotonic() <= deadline:
            with self.permit(
                "remote_monitor", holder=f"{holder_base}:monitor", run_id=run_id, task_id=task_id, slot_id=slot_id
            ):
                status = self.query_job(cluster_name, job_id)
            polls.append(status)
            if status["terminal"]:
                final_state = status
                break
            time.sleep(poll_interval)
        if final_state is None:
            final_state = polls[-1] if polls else {"state": "TIMEOUT", "state_base": "TIMEOUT", "terminal": True}

        with self.permit(
            "remote_transfer", holder=f"{holder_base}:harvest", run_id=run_id, task_id=task_id, slot_id=slot_id
        ):
            harvest = self.harvest(cluster_name, submission["remote_dir"], local_cluster_dir)

        result = {
            "ok": final_state.get("state_base") == "COMPLETED" and harvest["returncode"] == 0,
            "cluster": cluster_name,
            "label": label,
            "job_id": job_id,
            "submission": submission,
            "polls": polls,
            "final_state": final_state,
            "harvest": harvest,
            "local_dir": str(local_cluster_dir),
        }
        summary_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        result["summary_path"] = str(summary_path)
        return result
