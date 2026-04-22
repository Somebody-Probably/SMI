import json
from pathlib import Path

from research_cluster_smi.chem_cli import discover_inputs, make_spec


class Args:
    program = "qe"
    pattern = None
    lane = "remote_cluster"
    priority = 100
    output_dir = "outputs"
    log_dir = "logs"
    report_dir = "reports"

    def __init__(self, input_dir: Path, output: Path) -> None:
        self.input_dir = str(input_dir)
        self.output = str(output)


def test_discover_inputs_sorted(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "b.in").write_text("&control\n/\n", encoding="utf-8")
    (input_dir / "a.in").write_text("&control\n/\n", encoding="utf-8")
    assert [path.name for path in discover_inputs(input_dir, "*.in")] == ["a.in", "b.in"]


def test_make_qe_spec(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "si.scf.in").write_text("&control\n/\n", encoding="utf-8")
    output = tmp_path / "spec.json"
    rc = make_spec(Args(input_dir, output))
    assert rc == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["metadata"]["program"] == "qe"
    assert payload["tasks"][0]["id"] == "qe-si"
    assert payload["tasks"][0]["metadata"]["program"] == "qe"
