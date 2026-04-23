import json
import subprocess
import sys
import time
from pathlib import Path

from research_cluster_smi import smi_cli
from research_cluster_smi import worker as worker_module
from research_cluster_smi.account_gate import AccountGate, account_gate_path
from research_cluster_smi.orders import OrderWatcher
from research_cluster_smi.smi_core import runtime_for
from research_cluster_smi.worker import WorkerManager


def test_smi_dry_run_smoke(tmp_path: Path) -> None:
    run_id = "smoke"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "hello.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("Say hello.", encoding="utf-8")
        runtime.seed_task(
            run_id,
            "fast_local",
            task_id="hello",
            prompt_path=str(prompt),
            write_set=["notes/hello.md"],
        )
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        summary = worker.tick()
        assert summary == {"started": 1, "completed": 1}
        status = runtime.status_summary(run_id)
        assert status["tasks"]["completed"] == 1
        assert status["verification_pending"] == 1
        result_path = runtime.run_dir / "results" / "hello" / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result["type"] == "dry_run"
    finally:
        runtime.close()


def test_status_summary_includes_leases_and_recent_events(tmp_path: Path) -> None:
    run_id = "inspect"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "inspect.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("inspect me", encoding="utf-8")
        runtime.seed_task(run_id, "fast_local", task_id="inspect", prompt_path=str(prompt))
        assignment = runtime.claim_next_task(run_id, "fast_local-00")
        assert assignment is None
        runtime.register_slot(run_id, "fast_local", "fast_local-00")
        assignment = runtime.claim_next_task(run_id, "fast_local-00")
        assert assignment is not None

        summary = runtime.status_summary(run_id)
        events = runtime.recent_events(run_id, limit=2)

        assert summary["leases"] == {"active": 1}
        active_lease = summary["active_leases"][0]
        assert active_lease["task_id"] == "inspect"
        assert isinstance(active_lease["age_seconds"], int)
        assert isinstance(active_lease["heartbeat_age_seconds"], int)
        assert active_lease["expires_in_seconds"] > 0
        assert [event["message_type"] for event in events] == ["slot.registered", "task.claimed"]
    finally:
        runtime.close()


def test_smi_leases_filters_and_gates_active_leases(tmp_path: Path, capsys) -> None:
    run_id = "lease-inspect"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.register_slot(run_id, "fast_local", "fast_local-00")
        runtime.seed_task(run_id, "fast_local", task_id="lease-task")
        assignment = runtime.claim_next_task(run_id, "fast_local-00")
        assert assignment is not None

        with runtime.conn:
            runtime.conn.execute(
                "UPDATE slots SET last_heartbeat_at=? WHERE run_id=? AND slot_id=?",
                ("2000-01-01T00:00:00.000Z", run_id, "fast_local-00"),
            )
            runtime.conn.execute(
                "UPDATE leases SET expires_at=? WHERE run_id=? AND lease_id=?",
                ("2000-01-01T00:00:00.000Z", run_id, assignment.lease_id),
            )
    finally:
        runtime.close()

    rc = smi_cli.main(["--run-root", str(tmp_path), "leases", "--run-id", run_id, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["active_leases"][0]["task_id"] == "lease-task"

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "leases",
            "--run-id",
            run_id,
            "--stale-heartbeat-seconds",
            "60",
            "--expiring-within-seconds",
            "60",
            "--fail-on-match",
            "--json",
        ]
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["active_leases"][0]["expires_in_seconds"] <= 60

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "leases",
            "--run-id",
            run_id,
            "--stale-heartbeat-seconds",
            "999999999",
            "--fail-on-match",
        ]
    )
    assert rc == 0
    assert "No active leases matched." in capsys.readouterr().out


def test_smi_expire_leases_cli_requeues_expired_task(tmp_path: Path, capsys) -> None:
    run_id = "lease-expire-cli"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.register_slot(run_id, "fast_local", "fast_local-00")
        runtime.seed_task(run_id, "fast_local", task_id="expired")
        assignment = runtime.claim_next_task(run_id, "fast_local-00")
        assert assignment is not None
        runtime.start_attempt(run_id, assignment.attempt_id)
        with runtime.conn:
            runtime.conn.execute(
                "UPDATE leases SET expires_at=? WHERE run_id=? AND lease_id=?",
                ("2000-01-01T00:00:00.000Z", run_id, assignment.lease_id),
            )
    finally:
        runtime.close()

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "expire-leases",
            "--run-id",
            run_id,
            "--lane",
            "fast_local",
            "--fail-on-expired",
            "--json",
        ]
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["expired_count"] == 1
    assert payload["expired_leases"][0]["task_id"] == "expired"

    runtime = runtime_for(tmp_path, run_id)
    try:
        task = runtime.conn.execute(
            "SELECT status FROM tasks WHERE run_id=? AND task_id=?",
            (run_id, "expired"),
        ).fetchone()
        attempt = runtime.conn.execute(
            "SELECT status, failure_class FROM attempts WHERE run_id=? AND attempt_id=?",
            (run_id, assignment.attempt_id),
        ).fetchone()
        slot = runtime.conn.execute(
            "SELECT status, current_lease_id FROM slots WHERE run_id=? AND slot_id=?",
            (run_id, "fast_local-00"),
        ).fetchone()
        assert task["status"] == "retry_ready"
        assert dict(attempt) == {"status": "failed", "failure_class": "lease_expired"}
        assert dict(slot) == {"status": "idle", "current_lease_id": None}
    finally:
        runtime.close()

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "expire-leases",
            "--run-id",
            run_id,
            "--fail-on-expired",
        ]
    )
    assert rc == 0
    assert "No expired active leases found." in capsys.readouterr().out


def test_smi_cancel_attempt_cli_marks_attempt_canceled(tmp_path: Path, capsys) -> None:
    run_id = "cancel-attempt-cli"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.register_slot(run_id, "fast_local", "fast_local-00")
        runtime.seed_task(run_id, "fast_local", task_id="cancel-me")
        assignment = runtime.claim_next_task(run_id, "fast_local-00")
        assert assignment is not None
        runtime.start_attempt(run_id, assignment.attempt_id)
    finally:
        runtime.close()

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "cancel-attempt",
            "--run-id",
            run_id,
            "--attempt-id",
            assignment.attempt_id,
            "--diagnostics",
            "operator stop",
            "--fail-on-canceled",
            "--json",
        ]
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["canceled"] is True
    assert payload["attempt"]["task_id"] == "cancel-me"
    assert payload["attempt"]["next_task_status"] == "retry_ready"

    runtime = runtime_for(tmp_path, run_id)
    try:
        task = runtime.conn.execute(
            "SELECT status FROM tasks WHERE run_id=? AND task_id=?",
            (run_id, "cancel-me"),
        ).fetchone()
        attempt = runtime.conn.execute(
            "SELECT status, failure_class, diagnostics FROM attempts WHERE run_id=? AND attempt_id=?",
            (run_id, assignment.attempt_id),
        ).fetchone()
        slot = runtime.conn.execute(
            "SELECT status, current_lease_id FROM slots WHERE run_id=? AND slot_id=?",
            (run_id, "fast_local-00"),
        ).fetchone()
        lease = runtime.conn.execute(
            "SELECT status FROM leases WHERE run_id=? AND lease_id=?",
            (run_id, assignment.lease_id),
        ).fetchone()
        assert task["status"] == "retry_ready"
        assert dict(attempt) == {
            "status": "failed",
            "failure_class": "attempt_canceled",
            "diagnostics": "operator stop",
        }
        assert dict(slot) == {"status": "idle", "current_lease_id": None}
        assert lease["status"] == "released"
        assert runtime.conn.execute(
            "SELECT COUNT(*) FROM events WHERE run_id=? AND message_type='attempt.canceled'",
            (run_id,),
        ).fetchone()[0] == 1

        assert runtime.complete_attempt(run_id, assignment.attempt_id, {"returncode": 0}) is False
        still_canceled = runtime.conn.execute(
            "SELECT status, failure_class FROM attempts WHERE run_id=? AND attempt_id=?",
            (run_id, assignment.attempt_id),
        ).fetchone()
        assert dict(still_canceled) == {"status": "failed", "failure_class": "attempt_canceled"}
        ignored_events = runtime.recent_events(run_id, limit=5, message_type="attempt.terminal_update_ignored")
        assert len(ignored_events) == 1
        assert ignored_events[0]["payload"]["requested_status"] == "completed"
        assert ignored_events[0]["payload"]["current_attempt_status"] == "failed"
        assert ignored_events[0]["payload"]["reported_returncode"] == 0

        assert (
            runtime.fail_attempt(
                run_id,
                assignment.attempt_id,
                failure_class="agent_command_failed",
                diagnostics="late failure",
            )
            is False
        )
        ignored_events = runtime.recent_events(run_id, limit=5, message_type="attempt.terminal_update_ignored")
        assert len(ignored_events) == 2
        assert ignored_events[-1]["payload"]["requested_status"] == "failed"
        assert ignored_events[-1]["payload"]["failure_class"] == "agent_command_failed"

        retry_assignment = runtime.claim_next_task(run_id, "fast_local-00")
        assert retry_assignment is not None
        assert retry_assignment.task_id == "cancel-me"
        assert retry_assignment.attempt_id != assignment.attempt_id
    finally:
        runtime.close()

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "cancel-attempt",
            "--run-id",
            run_id,
            "--attempt-id",
            assignment.attempt_id,
            "--fail-on-canceled",
        ]
    )
    assert rc == 0
    assert "No cancelable attempt found." in capsys.readouterr().out


def test_smi_attempts_cli_filters_attempt_records(tmp_path: Path, capsys) -> None:
    run_id = "attempt-ledger-cli"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.seed_task(run_id, "fast_local", task_id="completed")
        runtime.seed_task(run_id, "fast_local", task_id="canceled")
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}
        assignment = runtime.claim_next_task(run_id, "fast_local-00")
        assert assignment is not None
        runtime.start_attempt(run_id, assignment.attempt_id)
        canceled = runtime.cancel_attempt(
            run_id,
            assignment.attempt_id,
            diagnostics="ledger test cancel",
        )
        assert canceled is not None
    finally:
        runtime.close()

    rc = smi_cli.main(["--run-root", str(tmp_path), "attempts", "--run-id", run_id, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert {attempt["status"] for attempt in payload["attempts"]} == {"completed", "failed"}

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "attempts",
            "--run-id",
            run_id,
            "--status",
            "failed",
            "--failure-class",
            "attempt_canceled",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["attempts"][0]["task_id"] == "canceled"
    assert payload["attempts"][0]["diagnostics"] == "ledger test cancel"

    rc = smi_cli.main(["--run-root", str(tmp_path), "attempts", "--run-id", run_id, "--task-id", "completed"])
    assert rc == 0
    output = capsys.readouterr().out
    assert "COMPLETED completed" in output
    assert "attempt=" in output


def test_smi_tasks_cli_filters_task_records(tmp_path: Path, capsys) -> None:
    run_id = "task-ledger-cli"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}, "remote_transfer": {"max_slots": 1}})
        runtime.seed_task(
            run_id,
            "fast_local",
            task_id="completed",
            priority=200,
            write_set=["notes/completed.md"],
            metadata={"owner": "codex"},
        )
        runtime.seed_task(
            run_id,
            "remote_transfer",
            task_id="blocked",
            priority=150,
            dependencies=["completed"],
            write_set=["remote/blocked"],
        )
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}
    finally:
        runtime.close()

    rc = smi_cli.main(["--run-root", str(tmp_path), "tasks", "--run-id", run_id, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    by_task = {task["task_id"]: task for task in payload["tasks"]}
    assert by_task["completed"]["status"] == "completed"
    assert by_task["completed"]["write_set"] == ["notes/completed.md"]
    assert by_task["completed"]["metadata"] == {"owner": "codex"}
    assert by_task["completed"]["attempt_count"] == 1
    assert by_task["completed"]["latest_attempt_status"] == "completed"
    assert by_task["blocked"]["status"] == "ready"
    assert by_task["blocked"]["dependencies"] == ["completed"]

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "tasks",
            "--run-id",
            run_id,
            "--lane",
            "remote_transfer",
            "--status",
            "ready",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["tasks"][0]["task_id"] == "blocked"

    rc = smi_cli.main(["--run-root", str(tmp_path), "tasks", "--run-id", run_id, "--task-id", "completed"])
    assert rc == 0
    output = capsys.readouterr().out
    assert "COMPLETED completed" in output
    assert "latest_attempt=" in output


def test_smi_events_cli_renders_recent_events(tmp_path: Path, capsys) -> None:
    run_id = "events-cli"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.seed_task(run_id, "fast_local", task_id="hello")
    finally:
        runtime.close()

    rc = smi_cli.main(["--run-root", str(tmp_path), "events", "--run-id", run_id, "--limit", "5"])

    assert rc == 0
    output = capsys.readouterr().out
    assert "run.initialized" in output
    assert "task.seeded" in output


def test_smi_verify_accepts_completed_dry_run(tmp_path: Path, capsys) -> None:
    run_id = "verify-accept"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "verify.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("verify me", encoding="utf-8")
        runtime.seed_task(run_id, "fast_local", task_id="verify", prompt_path=str(prompt))
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}
        assert runtime.status_summary(run_id)["verification_pending"] == 1
    finally:
        runtime.close()

    rc = smi_cli.main(["--run-root", str(tmp_path), "verify", "--run-id", run_id])

    assert rc == 0
    output = capsys.readouterr().out
    assert "ACCEPTED verify" in output
    runtime = runtime_for(tmp_path, run_id)
    try:
        summary = runtime.status_summary(run_id)
        assert summary["verification_pending"] == 0
        assert summary["verification_current"] == {"accepted": 1}
        assert summary["verifications"] == {"accepted": 1}
        assert runtime.recent_events(run_id, message_type="verification.accepted")[-1]["task_id"] == "verify"
    finally:
        runtime.close()


def test_smi_verify_holds_missing_artifacts_when_required(tmp_path: Path, capsys) -> None:
    run_id = "verify-hold"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "artifact.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("produce artifact", encoding="utf-8")
        runtime.seed_task(
            run_id,
            "fast_local",
            task_id="artifact",
            prompt_path=str(prompt),
            write_set=["reports/artifact.md"],
            metadata={"expected_output": "outputs/artifact.out"},
        )
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}
    finally:
        runtime.close()

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "verify",
            "--run-id",
            run_id,
            "--require-artifacts",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["decision"] == "held"
    assert sorted(payload[0]["evidence"]["missing_artifacts"]) == [
        "outputs/artifact.out",
        "reports/artifact.md",
    ]
    runtime = runtime_for(tmp_path, run_id)
    try:
        assert runtime.status_summary(run_id)["verifications"] == {"held": 1}
    finally:
        runtime.close()


def test_smi_verify_runs_validation_command(tmp_path: Path, capsys) -> None:
    run_id = "verify-command"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "command.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("validate me", encoding="utf-8")
        runtime.seed_task(run_id, "fast_local", task_id="command", prompt_path=str(prompt))
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}
    finally:
        runtime.close()

    validation_command = (
        f'"{sys.executable}" -c "import os, pathlib; '
        "pathlib.Path('validation.txt').write_text('{task_id}:' + os.environ['SMI_VERIFY_TASK_ID'])\""
    )
    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "verify",
            "--run-id",
            run_id,
            "--validation-command",
            validation_command,
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["decision"] == "accepted"
    assert payload[0]["evidence"]["validation_command"]["returncode"] == 0
    assert "{task_id}" not in payload[0]["evidence"]["validation_command"]["command"]
    assert (tmp_path / run_id / "validation.txt").read_text(encoding="utf-8") == "command:command"


def test_smi_verify_rejects_failed_validation_command(tmp_path: Path, capsys) -> None:
    run_id = "verify-command-fails"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.seed_task(run_id, "fast_local", task_id="bad-command")
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}
    finally:
        runtime.close()

    validation_command = f'"{sys.executable}" -c "import sys; sys.exit(7)"'
    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "verify",
            "--run-id",
            run_id,
            "--validation-command",
            validation_command,
            "--json",
        ]
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["decision"] == "rejected"
    assert payload[0]["diagnostics"] == "Validation command failed with exit code 7."
    assert payload[0]["evidence"]["validation_command"]["returncode"] == 7


def test_smi_verifications_lists_records(tmp_path: Path, capsys) -> None:
    run_id = "verification-ledger"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.seed_task(run_id, "fast_local", task_id="ledger")
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}
    finally:
        runtime.close()

    rc = smi_cli.main(["--run-root", str(tmp_path), "verify", "--run-id", run_id, "--json"])
    assert rc == 0
    verification_payload = json.loads(capsys.readouterr().out)

    rc = smi_cli.main(["--run-root", str(tmp_path), "verifications", "--run-id", run_id, "--json"])
    assert rc == 0
    records = json.loads(capsys.readouterr().out)
    assert len(records) == 1
    assert records[0]["verification_id"] == verification_payload[0]["verification_id"]
    assert records[0]["decision"] == "accepted"
    assert records[0]["task_id"] == "ledger"
    assert records[0]["evidence"]["expected_artifacts"] == []

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "verifications",
            "--run-id",
            run_id,
            "--decision",
            "rejected",
            "--json",
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []

    rc = smi_cli.main(["--run-root", str(tmp_path), "verifications", "--run-id", run_id, "--task-id", "ledger"])
    assert rc == 0
    output = capsys.readouterr().out
    assert "ACCEPTED ledger" in output
    assert "verifier=neutral" in output


def test_smi_verifications_latest_collapses_rechecks(tmp_path: Path, capsys) -> None:
    run_id = "verification-latest"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.seed_task(run_id, "fast_local", task_id="rechecked")
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}
    finally:
        runtime.close()

    rc = smi_cli.main(["--run-root", str(tmp_path), "verify", "--run-id", run_id, "--json"])
    assert rc == 0
    capsys.readouterr()

    validation_command = f'"{sys.executable}" -c "import sys; sys.exit(7)"'
    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "verify",
            "--run-id",
            run_id,
            "--include-verified",
            "--validation-command",
            validation_command,
            "--json",
        ]
    )
    assert rc == 1
    capsys.readouterr()

    rc = smi_cli.main(["--run-root", str(tmp_path), "verifications", "--run-id", run_id, "--json"])
    assert rc == 0
    records = json.loads(capsys.readouterr().out)
    assert [record["decision"] for record in records] == ["rejected", "accepted"]

    rc = smi_cli.main(["--run-root", str(tmp_path), "verifications", "--run-id", run_id, "--latest", "--json"])
    assert rc == 0
    latest_records = json.loads(capsys.readouterr().out)
    assert len(latest_records) == 1
    assert latest_records[0]["task_id"] == "rechecked"
    assert latest_records[0]["decision"] == "rejected"

    rc = smi_cli.main(["--run-root", str(tmp_path), "status", "--run-id", run_id, "--json"])
    assert rc == 0
    status = json.loads(capsys.readouterr().out)
    assert status["verification_current"] == {"rejected": 1}
    assert status["verifications"] == {"accepted": 1, "rejected": 1}

    rc = smi_cli.main(["--run-root", str(tmp_path), "status", "--run-id", run_id])
    assert rc == 0
    output = capsys.readouterr().out
    assert "current:" in output
    assert "records:" in output


def test_smi_reconcile_previews_current_verifier_actions(tmp_path: Path, capsys) -> None:
    run_id = "verification-reconcile"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.seed_task(run_id, "fast_local", task_id="reconcile-me")
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}
    finally:
        runtime.close()

    rc = smi_cli.main(["--run-root", str(tmp_path), "verify", "--run-id", run_id, "--json"])
    assert rc == 0
    capsys.readouterr()

    validation_command = f'"{sys.executable}" -c "import sys; sys.exit(7)"'
    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "verify",
            "--run-id",
            run_id,
            "--include-verified",
            "--validation-command",
            validation_command,
            "--json",
        ]
    )
    assert rc == 1
    capsys.readouterr()

    rc = smi_cli.main(["--run-root", str(tmp_path), "reconcile", "--run-id", run_id, "--json"])
    assert rc == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["pending_verification"] == 0
    assert preview["current_decisions"] == {"rejected": 1}
    assert preview["controller_hints"] == {"exclude_result": 1}
    assert len(preview["actions"]) == 1
    assert preview["actions"][0]["task_id"] == "reconcile-me"
    assert preview["actions"][0]["controller_hint"] == "exclude_result"

    rc = smi_cli.main(["--run-root", str(tmp_path), "reconcile", "--run-id", run_id, "--hint", "use_result", "--json"])
    assert rc == 0
    filtered = json.loads(capsys.readouterr().out)
    assert filtered["current_decisions"] == {}
    assert filtered["controller_hints"] == {}
    assert filtered["actions"] == []

    rc = smi_cli.main(["--run-root", str(tmp_path), "reconcile", "--run-id", run_id, "--hint", "exclude_result", "--json"])
    assert rc == 0
    filtered = json.loads(capsys.readouterr().out)
    assert filtered["current_decisions"] == {"rejected": 1}
    assert filtered["controller_hints"] == {"exclude_result": 1}
    assert filtered["actions"][0]["task_id"] == "reconcile-me"

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "reconcile",
            "--run-id",
            run_id,
            "--fail-on-hint",
            "exclude_result",
            "--json",
        ]
    )
    assert rc == 1
    gated = json.loads(capsys.readouterr().out)
    assert gated["controller_hints"] == {"exclude_result": 1}

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "reconcile",
            "--run-id",
            run_id,
            "--fail-on-hint",
            "use_result",
            "--json",
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["controller_hints"] == {"exclude_result": 1}

    rc = smi_cli.main(["--run-root", str(tmp_path), "reconcile", "--run-id", run_id, "--task-id", "missing", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["actions"] == []

    rc = smi_cli.main(["--run-root", str(tmp_path), "reconcile", "--run-id", run_id])
    assert rc == 0
    output = capsys.readouterr().out
    assert "REJECTED reconcile-me" in output
    assert "hint=exclude_result" in output


def test_smi_reconcile_can_gate_pending_verification(tmp_path: Path, capsys) -> None:
    run_id = "verification-pending-gate"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.seed_task(run_id, "fast_local", task_id="pending-gate")
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}
    finally:
        runtime.close()

    rc = smi_cli.main(["--run-root", str(tmp_path), "reconcile", "--run-id", run_id, "--json"])
    assert rc == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["pending_verification"] == 1
    assert preview["actions"] == []

    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "reconcile",
            "--run-id",
            run_id,
            "--fail-on-pending",
            "--json",
        ]
    )
    assert rc == 1
    preview = json.loads(capsys.readouterr().out)
    assert preview["pending_verification"] == 1
    assert preview["actions"] == []

    rc = smi_cli.main(["--run-root", str(tmp_path), "reconcile", "--run-id", run_id, "--fail-on-pending"])
    assert rc == 1
    assert "Pending verification: 1" in capsys.readouterr().out


def test_smi_reconcile_ignores_stale_completed_attempts(tmp_path: Path, capsys) -> None:
    run_id = "verification-current-attempt"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.register_slot(run_id, "fast_local", "fast_local-00")
        runtime.seed_task(run_id, "fast_local", task_id="retried")
        first = runtime.claim_next_task(run_id, "fast_local-00")
        assert first is not None
        runtime.start_attempt(run_id, first.attempt_id)
        runtime.complete_attempt(run_id, first.attempt_id, {"returncode": 0})
    finally:
        runtime.close()

    rc = smi_cli.main(["--run-root", str(tmp_path), "verify", "--run-id", run_id, "--json"])
    assert rc == 0
    capsys.readouterr()

    runtime = runtime_for(tmp_path, run_id)
    try:
        with runtime.conn:
            runtime.conn.execute(
                "UPDATE tasks SET status='retry_ready', updated_at=? WHERE run_id=? AND task_id=?",
                ("2099-01-01T00:00:00.000Z", run_id, "retried"),
            )
        second = runtime.claim_next_task(run_id, "fast_local-00")
        assert second is not None
        assert second.attempt_id != first.attempt_id
        runtime.start_attempt(run_id, second.attempt_id)
        runtime.complete_attempt(run_id, second.attempt_id, {"returncode": 0})
    finally:
        runtime.close()

    validation_command = f'"{sys.executable}" -c "import sys; sys.exit(7)"'
    rc = smi_cli.main(
        [
            "--run-root",
            str(tmp_path),
            "verify",
            "--run-id",
            run_id,
            "--validation-command",
            validation_command,
            "--json",
        ]
    )
    assert rc == 1
    capsys.readouterr()

    rc = smi_cli.main(["--run-root", str(tmp_path), "reconcile", "--run-id", run_id, "--json"])
    assert rc == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["current_decisions"] == {"rejected": 1}
    assert preview["controller_hints"] == {"exclude_result": 1}
    assert len(preview["actions"]) == 1
    assert preview["actions"][0]["attempt_id"] == second.attempt_id

    rc = smi_cli.main(["--run-root", str(tmp_path), "status", "--run-id", run_id, "--json"])
    assert rc == 0
    status = json.loads(capsys.readouterr().out)
    assert status["verification_current"] == {"rejected": 1}
    assert status["verifications"] == {"accepted": 1, "rejected": 1}


def test_smi_releases_blocked_dependencies(tmp_path: Path) -> None:
    run_id = "deps"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"remote_transfer": {"max_slots": 1}})
        prompt_dir = runtime.run_dir / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        stage_prompt = prompt_dir / "stage.md"
        submit_prompt = prompt_dir / "submit.md"
        stage_prompt.write_text("Stage files.", encoding="utf-8")
        submit_prompt.write_text("Submit job.", encoding="utf-8")
        runtime.seed_task(
            run_id,
            "remote_transfer",
            task_id="stage",
            prompt_path=str(stage_prompt),
            write_set=["remote/input"],
        )
        runtime.seed_task(
            run_id,
            "remote_transfer",
            task_id="submit",
            dependencies=["stage"],
            prompt_path=str(submit_prompt),
            write_set=["remote/job"],
        )

        status = runtime.status_summary(run_id)
        assert status["tasks"]["ready"] == 1
        assert status["tasks"]["blocked"] == 1

        worker = WorkerManager(runtime, run_id, "remote_transfer", slots=1, dry_run=True)
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}

        status = runtime.status_summary(run_id)
        assert status["tasks"]["completed"] == 1
        assert status["tasks"]["ready"] == 1

        assert worker.tick() == {"started": 1, "completed": 1}
        status = runtime.status_summary(run_id)
        assert status["tasks"]["completed"] == 2
    finally:
        runtime.close()


def test_order_watcher_accepts_utf8_bom_seed_order(tmp_path: Path) -> None:
    run_id = "orders-bom"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        orders_dir = runtime.run_dir / "orders"
        orders_dir.mkdir(parents=True, exist_ok=True)
        order = {
            "order_type": "seed",
            "payload": {
                "tasks": [
                    {
                        "id": "hello-bom",
                        "lane": "fast_local",
                        "priority": 150,
                        "write_set": ["notes/hello-bom.md"],
                        "prompt": "Say hello from a BOM-authored order.",
                    }
                ]
            },
        }
        (orders_dir / "seed-bom.json").write_text(
            "\ufeff" + json.dumps(order),
            encoding="utf-8",
        )

        results = OrderWatcher(runtime, run_id, orders_dir).poll()

        assert len(results) == 1
        assert results[0].success is True
        assert runtime.status_summary(run_id)["tasks"]["ready"] == 1
        assert (runtime.run_dir / "prompts" / "hello-bom.md").exists()
    finally:
        runtime.close()


def test_smi_worker_can_launch_agent_command(tmp_path: Path) -> None:
    run_id = "agent-command"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "hello.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("Say hello.", encoding="utf-8")
        agent = tmp_path / "agent.py"
        agent.write_text(
            "import sys\n"
            "prompt = sys.stdin.read()\n"
            "print('received:' + prompt)\n",
            encoding="utf-8",
        )
        runtime.seed_task(run_id, "fast_local", task_id="hello", prompt_path=str(prompt))

        worker = WorkerManager(
            runtime,
            run_id,
            "fast_local",
            slots=1,
            agent_command=f"{sys.executable} {agent}",
        )
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 1}

        result_path = runtime.run_dir / "results" / "hello" / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result["returncode"] == 0
        assert result["stdout"] == "received:Say hello.\n"
    finally:
        runtime.close()


def test_smi_worker_respects_busy_account_gate(tmp_path: Path) -> None:
    run_id = "project-a"
    runtime = runtime_for(tmp_path, run_id)
    gate = AccountGate("drac-fouquet", account_gate_path(tmp_path, "drac-fouquet"))
    try:
        gate.configure_resources({"remote_transfer": {"max_slots": 1}})
        held = gate.acquire("remote_transfer", holder="project-b")
        assert held is not None

        runtime.initialize_run(run_id, lanes={"remote_transfer": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "stage.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("Stage files.", encoding="utf-8")
        runtime.seed_task(run_id, "remote_transfer", task_id="stage", prompt_path=str(prompt))

        worker = WorkerManager(
            runtime,
            run_id,
            "remote_transfer",
            slots=1,
            dry_run=True,
            account_gate=gate,
            account_gated_lanes={"remote_transfer"},
        )
        worker.register_slots()
        assert worker.tick() == {"started": 0, "completed": 0}
        assert runtime.status_summary(run_id)["tasks"]["ready"] == 1

        assert gate.release(held.permit_id)
        assert worker.tick() == {"started": 1, "completed": 1}
        result_path = runtime.run_dir / "results" / "stage" / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result["account_permit"]["resource"] == "remote_transfer"
    finally:
        gate.close()
        runtime.close()


def test_smi_parallel_worker_supervises_multiple_agents(tmp_path: Path) -> None:
    run_id = "parallel-agent-command"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 2}})
        prompt_dir = runtime.run_dir / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        agent = tmp_path / "agent.py"
        agent.write_text(
            "import os, sys, time\n"
            "prompt = sys.stdin.read()\n"
            "time.sleep(0.2)\n"
            "print(os.environ['SMI_TASK_ID'] + ':' + prompt)\n",
            encoding="utf-8",
        )
        for task_id in ("a", "b"):
            prompt = prompt_dir / f"{task_id}.md"
            prompt.write_text(task_id, encoding="utf-8")
            runtime.seed_task(run_id, "fast_local", task_id=task_id, prompt_path=str(prompt))

        worker = WorkerManager(
            runtime,
            run_id,
            "fast_local",
            slots=2,
            agent_command=f"{sys.executable} {agent}",
            parallel=True,
        )
        worker.register_slots()
        first_tick = worker.tick()
        assert first_tick == {"started": 2, "completed": 0}

        deadline = time.monotonic() + 5
        completed = 0
        while time.monotonic() < deadline and completed < 2:
            time.sleep(0.05)
            completed += worker.tick()["completed"]

        assert completed == 2
        status = runtime.status_summary(run_id)
        assert status["tasks"]["completed"] == 2
    finally:
        runtime.close()


def test_parallel_worker_heartbeats_active_lease(tmp_path: Path) -> None:
    run_id = "heartbeat-agent-command"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "heartbeat.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("stay alive", encoding="utf-8")
        agent = tmp_path / "slow_agent.py"
        agent.write_text(
            "import sys, time\n"
            "prompt = sys.stdin.read()\n"
            "time.sleep(1.0)\n"
            "print('done:' + prompt)\n",
            encoding="utf-8",
        )
        runtime.seed_task(run_id, "fast_local", task_id="heartbeat", prompt_path=str(prompt))

        worker = WorkerManager(
            runtime,
            run_id,
            "fast_local",
            slots=1,
            agent_command=f"{sys.executable} {agent}",
            parallel=True,
        )
        worker.register_slots()
        assert worker.tick() == {"started": 1, "completed": 0}
        lease_before = runtime.conn.execute(
            "SELECT expires_at FROM leases WHERE run_id=? AND task_id=?",
            (run_id, "heartbeat"),
        ).fetchone()["expires_at"]

        time.sleep(0.1)
        assert worker.tick(start_new=False) == {"started": 0, "completed": 0}
        lease_after = runtime.conn.execute(
            "SELECT expires_at FROM leases WHERE run_id=? AND task_id=?",
            (run_id, "heartbeat"),
        ).fetchone()["expires_at"]
        assert lease_after > lease_before
        heartbeat_events = runtime.conn.execute(
            "SELECT COUNT(*) FROM events WHERE run_id=? AND message_type='lease.heartbeat'",
            (run_id,),
        ).fetchone()[0]
        assert heartbeat_events >= 1

        deadline = time.monotonic() + 5
        completed = 0
        while time.monotonic() < deadline and completed < 1:
            time.sleep(0.05)
            completed += worker.tick(start_new=False)["completed"]
        assert completed == 1
        assert runtime.status_summary(run_id)["tasks"]["completed"] == 1
    finally:
        runtime.close()


def test_worker_records_terminated_agent_failure_class(tmp_path: Path, monkeypatch) -> None:
    run_id = "agent-terminated"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        prompt = runtime.run_dir / "prompts" / "terminated.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("terminate me", encoding="utf-8")
        runtime.seed_task(run_id, "fast_local", task_id="terminated", prompt_path=str(prompt))

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=-9, stdout="", stderr="")

        monkeypatch.setattr(worker_module.subprocess, "run", fake_run)
        worker = WorkerManager(runtime, run_id, "fast_local", slots=1, agent_command="fake-agent")
        worker.register_slots()

        assert worker.tick() == {"started": 1, "completed": 0}
        attempt = runtime.conn.execute(
            """
            SELECT status, failure_class, diagnostics
            FROM attempts
            WHERE run_id=? AND task_id=?
            """,
            (run_id, "terminated"),
        ).fetchone()
        assert attempt["status"] == "failed"
        assert attempt["failure_class"] == "agent_process_terminated"
        assert "signal 9" in attempt["diagnostics"]
    finally:
        runtime.close()


def test_expired_lease_requeues_task_and_frees_slot(tmp_path: Path) -> None:
    run_id = "expired-lease"
    runtime = runtime_for(tmp_path, run_id)
    try:
        runtime.initialize_run(run_id, lanes={"fast_local": {"max_slots": 1}})
        runtime.register_slot(run_id, "fast_local", "fast_local-00", heartbeat_timeout_sec=1)
        prompt = runtime.run_dir / "prompts" / "expired.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("expire me", encoding="utf-8")
        runtime.seed_task(run_id, "fast_local", task_id="expired", prompt_path=str(prompt))
        assignment = runtime.claim_next_task(run_id, "fast_local-00")
        assert assignment is not None
        runtime.start_attempt(run_id, assignment.attempt_id)
        with runtime.conn:
            runtime.conn.execute(
                "UPDATE leases SET expires_at=? WHERE run_id=? AND lease_id=?",
                ("2000-01-01T00:00:00.000Z", run_id, assignment.lease_id),
            )

        expired = runtime.expire_stale_leases(run_id)

        assert [row["task_id"] for row in expired] == ["expired"]
        task = runtime.conn.execute(
            "SELECT status FROM tasks WHERE run_id=? AND task_id=?",
            (run_id, "expired"),
        ).fetchone()
        attempt = runtime.conn.execute(
            "SELECT status, failure_class FROM attempts WHERE run_id=? AND attempt_id=?",
            (run_id, assignment.attempt_id),
        ).fetchone()
        slot = runtime.conn.execute(
            "SELECT status, current_lease_id FROM slots WHERE run_id=? AND slot_id=?",
            (run_id, "fast_local-00"),
        ).fetchone()
        lease = runtime.conn.execute(
            "SELECT status FROM leases WHERE run_id=? AND lease_id=?",
            (run_id, assignment.lease_id),
        ).fetchone()
        assert task["status"] == "retry_ready"
        assert dict(attempt) == {"status": "failed", "failure_class": "lease_expired"}
        assert dict(slot) == {"status": "idle", "current_lease_id": None}
        assert lease["status"] == "expired"
        assert runtime.conn.execute(
            "SELECT COUNT(*) FROM events WHERE run_id=? AND message_type='lease.expired'",
            (run_id,),
        ).fetchone()[0] == 1

        retry_assignment = runtime.claim_next_task(run_id, "fast_local-00")
        assert retry_assignment is not None
        assert retry_assignment.task_id == "expired"
        assert retry_assignment.attempt_id != assignment.attempt_id
    finally:
        runtime.close()
