import json
from pathlib import Path

from research_cluster_smi import package_cli


def test_find_placeholders_reports_nested_paths() -> None:
    payload = {"clusters": {"trillium": {"user": "YOUR_USER", "paths": ["/scratch/{user}", "ready"]}}}

    assert package_cli.find_placeholders(payload) == ["$.clusters.trillium.user"]


def test_package_doctor_warns_for_unedited_cluster_config(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    for relative in ("README.md", "config/clusters.example.json"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")
    monkeypatch.setattr(package_cli, "REQUIRED_PACKAGE_FILES", ("README.md", "config/clusters.example.json"))
    monkeypatch.setattr(package_cli, "LOCAL_CONFIG_HINTS", {})
    monkeypatch.setattr(package_cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(package_cli, "globus_command", lambda: "/usr/bin/globus")

    config = {
        "default_cluster": "trillium",
        "clusters": {
            "trillium": {
                "ssh_alias": "trillium",
                "user": "YOUR_ALLIANCE_USERNAME",
                "account": "YOUR_SLURM_ACCOUNT",
                "remote_project_dir": "/scratch/{user}/research-workspace",
            }
        },
    }
    config_path = tmp_path / "config" / "clusters.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    rc = package_cli.main(["--repo-root", str(tmp_path), "--cluster-config", str(config_path), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    warnings = [result for result in payload["results"] if result["status"] == "WARN"]
    assert any(result["name"] == "cluster config values" for result in warnings)


def test_package_doctor_fails_when_required_tool_missing(monkeypatch, tmp_path: Path, capsys) -> None:
    (tmp_path / "README.md").write_text("placeholder\n", encoding="utf-8")
    monkeypatch.setattr(package_cli, "REQUIRED_PACKAGE_FILES", ("README.md",))
    monkeypatch.setattr(package_cli, "LOCAL_CONFIG_HINTS", {})
    monkeypatch.setattr(package_cli.shutil, "which", lambda name: None if name == "ssh" else f"/usr/bin/{name}")
    monkeypatch.setattr(package_cli, "globus_command", lambda: "/usr/bin/globus")

    rc = package_cli.main(["--repo-root", str(tmp_path), "--json"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert {
        "status": "FAIL",
        "name": "ssh",
        "detail": "missing; needed for cluster sessions",
    } in payload["results"]
