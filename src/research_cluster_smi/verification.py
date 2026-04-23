"""Project-neutral verification helpers for completed SMI attempts."""

from __future__ import annotations

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


def verify_attempt(
    attempt: dict[str, Any],
    *,
    artifact_root: Path,
    require_artifacts: bool = False,
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

    return "accepted", "Completed attempt passed neutral verification checks.", evidence


def verify_completed_attempts(
    runtime: SMIRuntime,
    run_id: str,
    *,
    task_id: str | None = None,
    verifier: str = "neutral",
    artifact_root: str | Path | None = None,
    require_artifacts: bool = False,
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

