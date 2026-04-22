import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from research_cluster_smi import cluster_cli
from research_cluster_smi.cluster_cli import ConfigError, expand, sbatch_defaults_for, ssh_control_command


def test_sbatch_profile_expansion() -> None:
    cluster = {
        "name": "trillium",
        "account": "rrg-ipaci-ab",
        "user": "fouquet",
        "default_sbatch_profile": "debug",
        "sbatch_profiles": {
            "debug": {
                "sbatch_defaults": [
                    "--account={account}",
                    "--nodes=1",
                    "--ntasks=1",
                    "--cpus-per-task=8",
                    "--mem=8G",
                ]
            }
        },
    }

    defaults = [expand(flag, cluster) for flag in sbatch_defaults_for(cluster)]

    assert defaults == [
        "--account=rrg-ipaci-ab",
        "--nodes=1",
        "--ntasks=1",
        "--cpus-per-task=8",
        "--mem=8G",
    ]


def test_unknown_sbatch_profile_fails() -> None:
    cluster = {"sbatch_profiles": {"account-only": {"sbatch_defaults": []}}}
    with pytest.raises(ConfigError):
        sbatch_defaults_for(cluster, "missing")


def test_example_config_has_current_alliance_clusters() -> None:
    config = json.loads(Path("config/clusters.example.json").read_text(encoding="utf-8"))

    assert {"trillium", "fir", "nibi", "narval", "rorqual"} <= set(config["clusters"])
    assert config["legacy_aliases"]["niagara"] == "trillium"
    assert config["legacy_aliases"]["cedar"] == "fir"
    assert config["clusters"]["trillium"]["sbatch_profiles"]["qff-trillium-debug"]["sbatch_defaults"] == [
        "--account={account}",
        "--time=00:05:00",
        "--nodes=1",
        "--ntasks=1",
        "--cpus-per-task=8",
        "--mem=8G",
        "--partition=debug",
    ]


def write_cluster_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "clusters.json"
    config_path.write_text(
        json.dumps(
            {
                "default_cluster": "trillium",
                "clusters": {
                    "trillium": {
                        "ssh_alias": "trillium",
                        "host": "trillium.alliancecan.ca",
                        "user": "alice",
                        "account": "def-alice",
                        "remote_project_dir": "/scratch/{user}/research-workspace",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path


def test_ssh_control_command() -> None:
    assert ssh_control_command({"ssh_alias": "trillium"}, "check") == ["ssh", "-O", "check", "trillium"]


def test_session_status_active_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = write_cluster_config(tmp_path)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv == ["ssh", "-O", "check", "trillium"]:
            return subprocess.CompletedProcess(argv, 0, "Master running\n", "")
        if argv == ["ssh", "trillium", "hostname && pwd"]:
            return subprocess.CompletedProcess(argv, 0, "trillium-login\n/scratch/alice\n", "")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(cluster_cli.subprocess, "run", fake_run)
    args = Namespace(
        config=str(config_path),
        cluster=None,
        remote_check=True,
        remote_command="hostname && pwd",
        json=True,
        dry_run=False,
    )

    assert cluster_cli.cmd_session_status(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["active"] is True
    assert payload["remote_check"]["stdout"] == "trillium-login\n/scratch/alice"
    assert calls == [["ssh", "-O", "check", "trillium"], ["ssh", "trillium", "hostname && pwd"]]


def test_session_status_inactive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = write_cluster_config(tmp_path)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 255, "", "No ControlMaster running\n")

    monkeypatch.setattr(cluster_cli.subprocess, "run", fake_run)
    args = Namespace(
        config=str(config_path),
        cluster=None,
        remote_check=True,
        remote_command="hostname && pwd",
        json=False,
        dry_run=False,
    )

    assert cluster_cli.cmd_session_status(args) == 1
    output = capsys.readouterr().out
    assert "Status: inactive" in output
    assert "research-cluster --config" in output


def test_session_touch_requires_active_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = write_cluster_config(tmp_path)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 255, "", "No ControlMaster running\n")

    monkeypatch.setattr(cluster_cli.subprocess, "run", fake_run)
    args = Namespace(
        config=str(config_path),
        cluster=None,
        command="true",
        open_if_missing=False,
        socket_dir="~/.ssh/sockets",
        dry_run=False,
    )

    assert cluster_cli.cmd_session_touch(args) == 1
    assert "Open with:" in capsys.readouterr().out


def test_session_touch_dry_run_with_open_if_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = write_cluster_config(tmp_path)
    socket_dir = tmp_path / "sockets"
    args = Namespace(
        config=str(config_path),
        cluster=None,
        command="true",
        open_if_missing=True,
        socket_dir=str(socket_dir),
        dry_run=True,
    )

    assert cluster_cli.cmd_session_touch(args) == 0
    assert capsys.readouterr().out.splitlines() == [
        "ssh -O check trillium",
        f"mkdir -p {socket_dir}",
        "ssh -f -N trillium",
        "ssh trillium true",
    ]
