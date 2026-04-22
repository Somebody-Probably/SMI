from pathlib import Path

from research_cluster_smi.account_gate import AccountGate, account_gate_path


def test_account_gate_limits_parallel_permits(tmp_path: Path) -> None:
    gate = AccountGate("drac-fouquet", account_gate_path(tmp_path, "drac-fouquet"))
    try:
        gate.configure_resources({"remote_submit": {"max_slots": 1}})
        first = gate.acquire("remote_submit", holder="project-a")
        assert first is not None

        blocked = gate.acquire("remote_submit", holder="project-b")
        assert blocked is None

        assert gate.release(first.permit_id)
        second = gate.acquire("remote_submit", holder="project-b")
        assert second is not None
    finally:
        gate.close()
