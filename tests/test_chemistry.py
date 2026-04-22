import json
from pathlib import Path

from research_cluster_smi.chemistry import parse_output, retry_order_from_result


def test_parse_qe_success(tmp_path: Path) -> None:
    output = tmp_path / "si.out"
    output.write_text(
        """
!    total energy              =     -15.123456 Ry
JOB DONE.
""",
        encoding="utf-8",
    )
    result = parse_output(output, "qe")
    assert result.completed is True
    assert result.converged is True
    assert result.final_energy == -15.123456
    assert result.retry.retryable is False


def test_parse_qe_missing_pseudopotential(tmp_path: Path) -> None:
    output = tmp_path / "bad.out"
    output.write_text("Error in routine readpp : file Si.UPF not found\n", encoding="utf-8")
    result = parse_output(output, "qe")
    assert result.completed is False
    assert result.retry.policy == "fix_missing_pseudopotential"
    assert result.retry.retryable is False


def test_parse_orca_memory_retry_order(tmp_path: Path) -> None:
    output = tmp_path / "water.out"
    output.write_text("ORCA finished by error termination\nnot enough memory\n", encoding="utf-8")
    result = parse_output(output, "orca")
    assert result.retry.policy == "resubmit_with_more_memory"
    order = retry_order_from_result(result, task_id="orca-water-retry", input_file="water.inp")
    assert order["order_type"] == "seed"
    task = order["payload"]["tasks"][0]
    assert task["metadata"]["retryable"] is True
    json.dumps(order)

