import pytest

from research_cluster_smi.account_cluster import base_state, parse_job_id


def test_parse_job_id_from_sbatch_parsable_output() -> None:
    assert parse_job_id("1234567;fir\n") == "1234567"


def test_parse_job_id_rejects_missing_id() -> None:
    with pytest.raises(RuntimeError):
        parse_job_id("Submitted but no id\n")


def test_base_state_removes_suffixes() -> None:
    assert base_state("COMPLETED+ something") == "COMPLETED"
