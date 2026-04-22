"""Output parsers and retry policy for computational chemistry jobs."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


QE_DONE = "JOB DONE"
ORCA_DONE = "ORCA TERMINATED NORMALLY"


@dataclass
class RetryDecision:
    retryable: bool
    policy: str
    reason: str
    prompt_hint: str


@dataclass
class ChemistryResult:
    program: str
    path: str
    completed: bool
    converged: bool | None
    final_energy: float | None
    final_energy_unit: str | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    retry: RetryDecision = field(
        default_factory=lambda: RetryDecision(False, "none", "No retry needed.", "No follow-up action needed.")
    )

    def to_dict(self) -> dict:
        return asdict(self)


def detect_program(path: str | Path, text: str) -> str:
    suffix = Path(path).suffix.lower()
    lowered = text.lower()
    if ORCA_DONE.lower() in lowered or "final single point energy" in lowered:
        return "orca"
    if QE_DONE.lower() in lowered or "!    total energy" in lowered or "pwscf" in lowered:
        return "qe"
    if suffix == ".out":
        return "qe"
    raise ValueError("Could not detect chemistry output program; pass --program.")


def parse_output(path: str | Path, program: str | None = None) -> ChemistryResult:
    output_path = Path(path)
    text = output_path.read_text(encoding="utf-8", errors="replace")
    profile = (program or detect_program(output_path, text)).lower()
    if profile == "qe":
        return parse_qe_output(output_path, text)
    if profile == "orca":
        return parse_orca_output(output_path, text)
    raise ValueError(f"Unsupported program: {program}")


def parse_qe_output(path: Path, text: str) -> ChemistryResult:
    completed = QE_DONE in text
    errors: list[str] = []
    warnings: list[str] = []
    lowered = text.lower()

    if "convergence not achieved" in lowered or "convergence has not been achieved" in lowered:
        warnings.append("SCF convergence was not achieved.")
    if "too many bands are not converged" in lowered:
        warnings.append("Bands did not converge.")
    if "error in routine" in lowered:
        for match in re.finditer(r"(?i)error in routine[^\n]+", text):
            errors.append(match.group(0).strip())
    if "not found" in lowered and ".upf" in lowered:
        errors.append("Possible missing pseudopotential file.")
    if "walltime" in lowered or "time limit" in lowered or "killed" in lowered:
        errors.append("Possible walltime or scheduler termination.")

    energy = None
    for match in re.finditer(r"!\s+total energy\s+=\s+([-+]?\d+(?:\.\d+)?)\s+Ry", text):
        energy = float(match.group(1))

    converged = completed and not any("convergence" in item.lower() for item in warnings)
    retry = qe_retry_policy(completed, errors, warnings)
    return ChemistryResult(
        program="qe",
        path=str(path),
        completed=completed,
        converged=converged,
        final_energy=energy,
        final_energy_unit="Ry" if energy is not None else None,
        errors=errors,
        warnings=warnings,
        retry=retry,
    )


def parse_orca_output(path: Path, text: str) -> ChemistryResult:
    completed = ORCA_DONE in text
    lowered = text.lower()
    errors: list[str] = []
    warnings: list[str] = []

    if "scf not converged" in lowered or "scf did not converge" in lowered:
        warnings.append("SCF convergence was not achieved.")
    if "error termination" in lowered:
        errors.append("ORCA ended with error termination.")
    if "not enough memory" in lowered or "out of memory" in lowered:
        errors.append("Memory limit appears too low.")
    if "basis" in lowered and ("not found" in lowered or "unknown" in lowered):
        errors.append("Possible basis set or auxiliary basis issue.")

    energy = None
    for match in re.finditer(r"FINAL SINGLE POINT ENERGY\s+([-+]?\d+(?:\.\d+)?)", text):
        energy = float(match.group(1))

    converged = completed and not any("convergence" in item.lower() for item in warnings)
    retry = orca_retry_policy(completed, errors, warnings)
    return ChemistryResult(
        program="orca",
        path=str(path),
        completed=completed,
        converged=converged,
        final_energy=energy,
        final_energy_unit="Eh" if energy is not None else None,
        errors=errors,
        warnings=warnings,
        retry=retry,
    )


def qe_retry_policy(completed: bool, errors: list[str], warnings: list[str]) -> RetryDecision:
    joined = " ".join(errors + warnings).lower()
    if completed and not warnings:
        return RetryDecision(False, "none", "QE output completed cleanly.", "No retry needed.")
    if ".upf" in joined or "pseudopotential" in joined:
        return RetryDecision(
            False,
            "fix_missing_pseudopotential",
            "QE appears to be missing a pseudopotential.",
            "Locate the missing UPF file, stage it under pseudo_dir, and resubmit after input validation.",
        )
    if "walltime" in joined or "time limit" in joined or "killed" in joined:
        return RetryDecision(
            True,
            "resubmit_with_more_time",
            "Calculation may have been stopped by scheduler limits.",
            "Increase walltime or reduce workload per job, then resubmit from the latest restart artifacts if available.",
        )
    if "convergence" in joined:
        return RetryDecision(
            True,
            "retry_relaxed_scf_settings",
            "QE SCF convergence was not achieved.",
            "Try a gentler mixing_beta, larger electron_maxstep, adjusted diagonalization, or continuation from restart files.",
        )
    return RetryDecision(
        not completed,
        "inspect_or_resubmit",
        "QE output is incomplete or contains an unclassified issue.",
        "Inspect the tail of the output and scheduler log; resubmit only if inputs and resource limits look sound.",
    )


def orca_retry_policy(completed: bool, errors: list[str], warnings: list[str]) -> RetryDecision:
    joined = " ".join(errors + warnings).lower()
    if completed and not warnings:
        return RetryDecision(False, "none", "ORCA output terminated normally.", "No retry needed.")
    if "basis" in joined:
        return RetryDecision(
            False,
            "fix_basis_or_input",
            "ORCA appears to have a basis or input definition problem.",
            "Fix the input deck or basis definition before resubmission.",
        )
    if "memory" in joined:
        return RetryDecision(
            True,
            "resubmit_with_more_memory",
            "ORCA appears memory limited.",
            "Increase SLURM memory and align ORCA %maxcore with requested resources.",
        )
    if "convergence" in joined:
        return RetryDecision(
            True,
            "retry_scf_settings",
            "ORCA SCF convergence was not achieved.",
            "Try SlowConv, SOSCF, level shifting, adjusted grid/settings, or a better initial geometry.",
        )
    return RetryDecision(
        not completed,
        "inspect_or_resubmit",
        "ORCA output is incomplete or contains an unclassified issue.",
        "Inspect output and scheduler logs before deciding whether to resubmit.",
    )


def retry_order_from_result(result: ChemistryResult, *, task_id: str, input_file: str, lane: str = "remote_cluster") -> dict:
    retry = result.retry
    return {
        "order_type": "seed",
        "payload": {
            "tasks": [
                {
                    "id": task_id,
                    "lane": lane,
                    "priority": 160 if retry.retryable else 80,
                    "write_set": [result.path, f"reports/{Path(result.path).stem}.retry.md"],
                    "metadata": {
                        "domain": "computational_chemistry",
                        "program": result.program,
                        "input_file": input_file,
                        "source_output": result.path,
                        "retry_policy": retry.policy,
                        "retryable": retry.retryable,
                    },
                    "prompt": (
                        f"Review {result.program.upper()} output {result.path} for task {task_id}.\n"
                        f"Retry policy: {retry.policy}\n"
                        f"Reason: {retry.reason}\n"
                        f"Recommendation: {retry.prompt_hint}\n"
                        "Do not invent missing chemistry results. Use only observed files and logs."
                    ),
                }
            ]
        },
    }


def result_to_json(result: ChemistryResult) -> str:
    return json.dumps(result.to_dict(), indent=2)

