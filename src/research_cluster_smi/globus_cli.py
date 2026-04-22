"""Globus CLI automation helpers.

The implementation shells out to the official `globus` command instead of
requiring a Python SDK dependency. This keeps the package small while still
letting researchers authenticate and manage endpoints with normal Globus tools.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = "config/globus.json"
DEFAULT_RECORD = "runs/globus-tasks.jsonl"


class GlobusConfigError(RuntimeError):
    """Raised when a Globus config or transfer profile is invalid."""


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise GlobusConfigError(
            f"Config not found: {config_path}. Copy config/globus.example.json "
            "to config/globus.json and fill in endpoint IDs."
        )
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if "endpoints" not in config or "transfers" not in config:
        raise GlobusConfigError("Config must contain 'endpoints' and 'transfers'.")
    return config


def normalize_path(root: str, child: str) -> str:
    if not root.endswith("/"):
        root += "/"
    return root + child.lstrip("/")


def transfer_plan(config: dict[str, Any], name: str | None) -> dict[str, Any]:
    transfer_name = name or config.get("default_transfer")
    if not transfer_name:
        raise GlobusConfigError("No transfer name provided and no default_transfer configured.")
    transfers = config.get("transfers", {})
    if transfer_name not in transfers:
        raise GlobusConfigError(f"Unknown transfer {transfer_name!r}.")
    transfer = dict(transfers[transfer_name])
    endpoints = config.get("endpoints", {})
    source_name = transfer.get("source")
    destination_name = transfer.get("destination")
    if source_name not in endpoints:
        raise GlobusConfigError(f"Unknown source endpoint {source_name!r}.")
    if destination_name not in endpoints:
        raise GlobusConfigError(f"Unknown destination endpoint {destination_name!r}.")
    source = endpoints[source_name]
    destination = endpoints[destination_name]
    return {
        "name": transfer_name,
        "source_name": source_name,
        "destination_name": destination_name,
        "source_endpoint": source["id"],
        "destination_endpoint": destination["id"],
        "source_path": normalize_path(source.get("root", ""), transfer.get("source_path", "")),
        "destination_path": normalize_path(destination.get("root", ""), transfer.get("destination_path", "")),
        "recursive": bool(transfer.get("recursive", True)),
        "label": transfer.get("label") or f"SMI transfer {transfer_name}",
    }


def globus_available() -> bool:
    return shutil.which("globus") is not None


def build_transfer_command(plan: dict[str, Any], *, label: str | None = None, recursive: bool | None = None) -> list[str]:
    command = [
        "globus",
        "transfer",
        f"{plan['source_endpoint']}:{plan['source_path']}",
        f"{plan['destination_endpoint']}:{plan['destination_path']}",
        "--format",
        "json",
        "--label",
        label or plan["label"],
    ]
    use_recursive = plan["recursive"] if recursive is None else recursive
    if use_recursive:
        command.append("--recursive")
    return command


def run_json_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"Command failed: {command}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"stdout": completed.stdout.strip()}


def append_record(path: str | Path, record: dict[str, Any]) -> None:
    record_path = Path(path)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    with record_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def cmd_doctor(args: argparse.Namespace) -> int:
    print("Globus doctor")
    print(f"globus: {shutil.which('globus') or 'missing'}")
    if not globus_available():
        print("Install with: python -m pip install globus-cli", file=sys.stderr)
        return 1
    if args.check_login:
        result = subprocess.run(["globus", "whoami"], text=True, capture_output=True, check=False)
        if result.returncode == 0:
            print(f"whoami: {result.stdout.strip()}")
            return 0
        print(result.stderr.strip() or "globus whoami failed", file=sys.stderr)
        return result.returncode
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    plan = transfer_plan(config, args.transfer)
    print(json.dumps(plan, indent=2))
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    plan = transfer_plan(config, args.transfer)
    command = build_transfer_command(plan, label=args.label, recursive=args.recursive)
    if args.dry_run:
        print(" ".join(command))
        return 0
    if not globus_available():
        raise RuntimeError("globus CLI is not installed.")
    response = run_json_command(command)
    record = {"plan": plan, "response": response}
    append_record(args.record, record)
    print(json.dumps(response, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    command = ["globus", "task", "show", args.task_id, "--format", "json"]
    if args.dry_run:
        print(" ".join(command))
        return 0
    response = run_json_command(command)
    print(json.dumps(response, indent=2))
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    command = ["globus", "task", "wait", args.task_id, "--timeout", str(args.timeout)]
    if args.dry_run:
        print(" ".join(command))
        return 0
    completed = subprocess.run(command, text=True, check=False)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-globus")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="Check Globus CLI availability.")
    p.add_argument("--check-login", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("plan", help="Render a configured transfer plan.")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--transfer")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("submit", help="Submit a configured transfer.")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--transfer")
    p.add_argument("--label")
    p.add_argument("--record", default=DEFAULT_RECORD)
    p.add_argument("--recursive", action="store_true", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("status", help="Show a Globus task.")
    p.add_argument("task_id")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("wait", help="Wait for a Globus task.")
    p.add_argument("task_id")
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_wait)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

