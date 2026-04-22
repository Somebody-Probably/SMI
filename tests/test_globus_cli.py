from pathlib import Path

from research_cluster_smi import globus_cli
from research_cluster_smi.globus_cli import build_transfer_command, transfer_plan


def test_transfer_plan_and_command() -> None:
    config = {
        "default_transfer": "stage",
        "endpoints": {
            "local": {"id": "local-id", "root": "/Users/example/research"},
            "remote": {"id": "remote-id", "root": "/scratch/example/work"},
        },
        "transfers": {
            "stage": {
                "source": "local",
                "destination": "remote",
                "source_path": "inputs/",
                "destination_path": "inputs/",
                "recursive": True,
                "label": "stage inputs",
            }
        },
    }
    plan = transfer_plan(config, None)
    assert plan["source_path"] == "/Users/example/research/inputs/"
    assert plan["destination_path"] == "/scratch/example/work/inputs/"
    command = build_transfer_command(plan)
    assert command[:2] == ["globus", "transfer"]
    assert "--recursive" in command


def test_globus_command_checks_interpreter_bin(monkeypatch, tmp_path: Path) -> None:
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    globus = bin_dir / "globus"
    globus.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(globus_cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(globus_cli.sys, "executable", str(bin_dir / "python"))

    assert globus_cli.globus_command() == str(globus)
