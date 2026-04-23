"""Project-neutral verification helpers for completed SMI attempts."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .smi_core import SMIRuntime


@dataclass(frozen=True)
class VerificationOutcome:
    task_id: str
    attempt_id: str
    decision: str
    verification_id: str
    diagnostics: str
    evidence: dict[str, Any]


CONTROLLER_HINTS = {
    "accepted": "use_result",
    "rejected": "exclude_result",
    "held": "review_result",
}
HINT_DECISIONS = {value: key for key, value in CONTROLLER_HINTS.items()}


def expected_artifacts(attempt: dict[str, Any]) -> list[str]:
    artifacts = list(attempt.get("write_set") or [])
    metadata = attempt.get("metadata") or {}
    expected = metadata.get("expected_output")
    if expected and expected not in artifacts:
        artifacts.append(str(expected))
    return artifacts


def artifact_path(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def expand_validation_command(command: str, attempt: dict[str, Any], artifact_root: Path) -> str:
    values = {
        "artifact_root": str(artifact_root),
        "run_dir": str(artifact_root),
        "task_id": str(attempt["task_id"]),
        "attempt_id": str(attempt["attempt_id"]),
    }
    expanded = command
    for key, value in values.items():
        expanded = expanded.replace(f"{{{key}}}", value)
    return expanded


def run_validation_command(command: str, attempt: dict[str, Any], artifact_root: Path) -> dict[str, Any]:
    expanded = expand_validation_command(command, attempt, artifact_root)
    env = os.environ.copy()
    env["SMI_VERIFY_TASK_ID"] = str(attempt["task_id"])
    env["SMI_VERIFY_ATTEMPT_ID"] = str(attempt["attempt_id"])
    env["SMI_VERIFY_ARTIFACT_ROOT"] = str(artifact_root)
    completed = subprocess.run(
        expanded,
        shell=True,
        cwd=artifact_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": expanded,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def verify_attempt(
    attempt: dict[str, Any],
    *,
    artifact_root: Path,
    require_artifacts: bool = False,
    validation_command: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    result = attempt.get("result") or {}
    evidence: dict[str, Any] = {
        "completed_at": attempt.get("completed_at"),
        "result_keys": sorted(result.keys()),
    }
    returncode = result.get("returncode")
    if returncode not in (None, 0):
        return "rejected", f"Attempt returned nonzero exit code {returncode}.", evidence

    artifacts = expected_artifacts(attempt)
    evidence["expected_artifacts"] = artifacts
    if require_artifacts:
        missing = [value for value in artifacts if not artifact_path(artifact_root, value).exists()]
        evidence["missing_artifacts"] = missing
        if missing:
            return "held", f"Missing {len(missing)} expected artifact(s).", evidence

    if validation_command:
        validation = run_validation_command(validation_command, attempt, artifact_root)
        evidence["validation_command"] = validation
        if validation["returncode"] != 0:
            return "rejected", f"Validation command failed with exit code {validation['returncode']}.", evidence

    return "accepted", "Completed attempt passed neutral verification checks.", evidence


def verify_completed_attempts(
    runtime: SMIRuntime,
    run_id: str,
    *,
    task_id: str | None = None,
    verifier: str = "neutral",
    artifact_root: str | Path | None = None,
    require_artifacts: bool = False,
    validation_command: str | None = None,
    include_verified: bool = False,
) -> list[VerificationOutcome]:
    root = Path(artifact_root) if artifact_root is not None else runtime.run_dir
    attempts = runtime.latest_completed_attempts(run_id, task_id=task_id, include_verified=include_verified)
    outcomes: list[VerificationOutcome] = []
    for attempt in attempts:
        decision, diagnostics, evidence = verify_attempt(
            attempt,
            artifact_root=root,
            require_artifacts=require_artifacts,
            validation_command=validation_command,
        )
        verification_id = runtime.record_verification(
            run_id,
            task_id=attempt["task_id"],
            attempt_id=attempt["attempt_id"],
            decision=decision,
            verifier=verifier,
            evidence=evidence,
            diagnostics=diagnostics,
        )
        outcomes.append(
            VerificationOutcome(
                task_id=attempt["task_id"],
                attempt_id=attempt["attempt_id"],
                decision=decision,
                verification_id=verification_id,
                diagnostics=diagnostics,
                evidence=evidence,
            )
        )
    return outcomes


def reconciliation_preview(
    runtime: SMIRuntime,
    run_id: str,
    *,
    task_id: str | None = None,
    decision: str | None = None,
    hint: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    if hint is not None:
        if hint not in HINT_DECISIONS:
            raise ValueError(f"Unsupported controller hint: {hint}")
        hint_decision = HINT_DECISIONS[hint]
        if decision is not None and decision != hint_decision:
            raise ValueError(f"Decision {decision} does not match controller hint {hint}.")
        decision = hint_decision
    records = runtime.verification_records(
        run_id,
        task_id=task_id,
        decision=decision,
        latest=True,
        current_attempts=True,
        limit=limit,
    )
    actions = []
    decision_counts: dict[str, int] = {}
    hint_counts: dict[str, int] = {}
    for record in records:
        hint = CONTROLLER_HINTS[record["decision"]]
        decision_counts[record["decision"]] = decision_counts.get(record["decision"], 0) + 1
        hint_counts[hint] = hint_counts.get(hint, 0) + 1
        actions.append(
            {
                "task_id": record["task_id"],
                "attempt_id": record["attempt_id"],
                "verification_id": record["verification_id"],
                "decision": record["decision"],
                "controller_hint": hint,
                "verified_at": record["verified_at"],
                "verifier": record["verifier"],
                "diagnostics": record.get("diagnostics") or "",
            }
        )
    return {
        "run_id": run_id,
        "pending_verification": runtime.pending_verification_count(run_id),
        "current_decisions": decision_counts,
        "controller_hints": hint_counts,
        "actions": actions,
    }
