"""Package-level diagnostics for research group onboarding."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from .cluster_cli import ConfigError, choose_cluster, expand, load_config as load_cluster_config
from .globus_cli import GlobusConfigError, globus_command, load_config as load_globus_config


REQUIRED_TOOLS = {
    "ssh": "needed for cluster sessions",
    "scp": "needed for small file staging",
    "rsync": "needed for workspace sync",
    "globus": "needed for supported large transfer staging and harvest",
}
LOCAL_TOOLS = {
    "git": "needed for worktree smoke tests",
}
OPTIONAL_TOOLS = {
    "codex": "needed for Codex CLI swarm workers",
}
REQUIRED_PACKAGE_FILES = (
    "README.md",
    "MANUAL.md",
    "docs/index.md",
    "docs/mac-setup.md",
    "docs/windows-setup.md",
    "docs/cluster-workflow.md",
    "docs/smi-manager-protocol.md",
    "docs/smi-general-harness-plan.md",
    "config/clusters.example.json",
    "config/globus.example.json",
    "config/ssh_config.example",
    "config/lanes.cluster-account.example.json",
    "config/lanes.project-swarm.example.json",
    "examples/hello-task-spec.json",
    "examples/slurm/generic-python-job.sbatch",
)
LOCAL_CONFIG_HINTS = {
    "config/clusters.json": "copy config/clusters.example.json to config/clusters.json and edit user/account fields",
    "config/globus.json": "copy config/globus.example.json to config/globus.json and edit endpoint IDs/roots",
    "config/lanes.local.json": "copy config/lanes.local.example.json to config/lanes.local.json for local dry runs",
}
PLACEHOLDER_MARKERS = ("YOUR_", "CHANGE_ME", "TODO")


def add_result(results: list[dict[str, Any]], status: str, name: str, detail: str) -> None:
    results.append({"status": status, "name": name, "detail": detail})


def find_placeholders(value: Any, prefix: str = "$") -> list[str]:
    """Return JSON-like paths whose string values still contain setup placeholders."""

    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(find_placeholders(child, f"{prefix}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_placeholders(child, f"{prefix}[{index}]"))
    elif isinstance(value, str) and any(marker in value for marker in PLACEHOLDER_MARKERS):
        found.append(prefix)
    return found


def check_python(results: list[dict[str, Any]]) -> None:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= (3, 10):
        add_result(results, "OK", "python", f"{version}")
    else:
        add_result(results, "FAIL", "python", f"{version}; Python 3.10 or newer is required")


def check_tools(results: list[dict[str, Any]], *, local_only: bool = False) -> None:
    required_tools = LOCAL_TOOLS if local_only else REQUIRED_TOOLS
    for tool, note in required_tools.items():
        path = globus_command() if tool == "globus" else shutil.which(tool)
        if path:
            add_result(results, "OK", tool, path)
        else:
            status = "WARN" if local_only else "FAIL"
            add_result(results, status, tool, f"missing; {note}")
    for tool, note in OPTIONAL_TOOLS.items():
        path = shutil.which(tool)
        if path:
            add_result(results, "OK", tool, path)
        else:
            add_result(results, "INFO", tool, f"missing from PATH; {note}")


def check_package_files(repo_root: Path, results: list[dict[str, Any]]) -> None:
    for relative in REQUIRED_PACKAGE_FILES:
        path = repo_root / relative
        if path.exists():
            add_result(results, "OK", relative, "present")
        else:
            add_result(results, "FAIL", relative, "missing from package checkout")


def check_local_configs(repo_root: Path, results: list[dict[str, Any]]) -> None:
    for relative, hint in LOCAL_CONFIG_HINTS.items():
        path = repo_root / relative
        if path.exists():
            add_result(results, "OK", relative, "present")
        else:
            add_result(results, "WARN", relative, hint)


def check_cluster_config(repo_root: Path, config_path: Path, cluster_name: str | None, results: list[dict[str, Any]]) -> None:
    path = config_path if config_path.is_absolute() else repo_root / config_path
    if not path.exists():
        add_result(results, "WARN", "cluster config", f"{path} not found")
        return

    try:
        config = load_cluster_config(path)
        selected_name, cluster = choose_cluster(config, cluster_name)
    except (ConfigError, json.JSONDecodeError) as exc:
        add_result(results, "FAIL", "cluster config", str(exc))
        return

    placeholders = find_placeholders(config)
    if placeholders:
        sample = ", ".join(placeholders[:5])
        more = "" if len(placeholders) <= 5 else f", and {len(placeholders) - 5} more"
        add_result(results, "WARN", "cluster config values", f"placeholders remain at {sample}{more}")
    else:
        add_result(results, "OK", "cluster config values", "no setup placeholders found")

    remote_dir = expand(cluster.get("remote_project_dir"), cluster) or "<unset>"
    account = cluster.get("account") or "<unset>"
    add_result(results, "OK", "selected cluster", f"{selected_name}; account={account}; remote_project_dir={remote_dir}")


def check_globus_config(repo_root: Path, config_path: Path, results: list[dict[str, Any]]) -> None:
    path = config_path if config_path.is_absolute() else repo_root / config_path
    if not path.exists():
        add_result(results, "WARN", "globus config", f"{path} not found")
        return

    try:
        config = load_globus_config(path)
    except (GlobusConfigError, json.JSONDecodeError) as exc:
        add_result(results, "FAIL", "globus config", str(exc))
        return

    placeholders = find_placeholders(config)
    if placeholders:
        sample = ", ".join(placeholders[:5])
        more = "" if len(placeholders) <= 5 else f", and {len(placeholders) - 5} more"
        add_result(results, "WARN", "globus config values", f"placeholders remain at {sample}{more}")
    else:
        transfer_names = ", ".join(sorted(config.get("transfers", {}))) or "<none>"
        add_result(results, "OK", "globus config values", f"configured transfers: {transfer_names}")


def render_text(results: list[dict[str, Any]], repo_root: Path) -> None:
    print("Research SMI package doctor")
    print(f"Repo root: {repo_root}")
    for result in results:
        print(f"[{result['status']}] {result['name']}: {result['detail']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-smi-doctor")
    parser.add_argument("--repo-root", default=".", help="Repository checkout root to inspect.")
    parser.add_argument("--cluster-config", default="config/clusters.json", help="Cluster config path.")
    parser.add_argument("--globus-config", default="config/globus.json", help="Globus config path.")
    parser.add_argument("--cluster", help="Cluster name to validate from the config.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable diagnostics.")
    parser.add_argument("--local", action="store_true", help="Check only local SMI harness readiness.")
    parser.add_argument("--strict", action="store_true", help="Return nonzero when warnings are present.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    results: list[dict[str, Any]] = []

    check_python(results)
    check_tools(results, local_only=args.local)
    check_package_files(repo_root, results)
    check_local_configs(repo_root, results)
    if not args.local:
        check_cluster_config(repo_root, Path(args.cluster_config), args.cluster, results)
        check_globus_config(repo_root, Path(args.globus_config), results)

    payload = {"repo_root": str(repo_root), "results": results}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        render_text(results, repo_root)

    has_failures = any(result["status"] == "FAIL" for result in results)
    has_warnings = any(result["status"] == "WARN" for result in results)
    if has_failures or (args.strict and has_warnings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
