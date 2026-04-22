"""Mac-friendly cluster workflow CLI.

This module keeps the cluster connection workflow small and inspectable:

1. keep an SSH ControlMaster session warm,
2. sync code or data with rsync,
3. submit SLURM jobs,
4. monitor jobs,
5. pull artifacts back.

All site-specific values live in a JSON config file.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path(os.environ.get("RESEARCH_CLUSTER_CONFIG", "config/clusters.json"))


class ConfigError(RuntimeError):
    """Raised when the cluster config is missing or malformed."""


class SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(
            f"Config not found: {path}. Copy config/clusters.example.json to "
            "config/clusters.json and edit it for your account."
        )
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if "clusters" not in config or not isinstance(config["clusters"], dict):
        raise ConfigError("Config must contain a 'clusters' object.")
    return config


def choose_cluster(config: dict[str, Any], name: str | None) -> tuple[str, dict[str, Any]]:
    cluster_name = name or config.get("default_cluster")
    if not cluster_name:
        raise ConfigError("No cluster selected and no default_cluster configured.")
    clusters = config.get("clusters", {})
    if cluster_name not in clusters:
        raise ConfigError(f"Unknown cluster {cluster_name!r}. Known: {', '.join(sorted(clusters))}")
    cluster = dict(clusters[cluster_name])
    cluster.setdefault("name", cluster_name)
    cluster.setdefault("ssh_alias", cluster_name)
    return cluster_name, cluster


def expand(value: str | None, cluster: dict[str, Any]) -> str:
    if value is None:
        return ""
    user = cluster.get("user") or os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    mapping = SafeDict(cluster)
    mapping.update(
        {
            "user": user,
            "cluster": cluster.get("name", ""),
            "scratch": cluster.get("scratch_dir", ""),
            "project": cluster.get("project_dir", ""),
            "remote_project_dir": cluster.get("remote_project_dir", ""),
        }
    )
    return str(value).format_map(mapping)


def run_command(argv: list[str], dry_run: bool = False) -> int:
    printable = shlex.join(argv)
    if dry_run:
        print(printable)
        return 0
    print(f"+ {printable}")
    completed = subprocess.run(argv, check=False)
    return completed.returncode


def ssh_base(cluster: dict[str, Any]) -> list[str]:
    return ["ssh", cluster["ssh_alias"]]


def cmd_doctor(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    print("Research cluster doctor")
    print(f"Config: {config_path}")
    missing = []
    for tool in ("ssh", "rsync", "scp"):
        found = shutil.which(tool)
        status = found or "missing"
        print(f"  {tool}: {status}")
        if found is None:
            missing.append(tool)
    try:
        config = load_config(config_path)
        name, cluster = choose_cluster(config, args.cluster)
        print(f"Selected cluster: {name}")
        print(f"  ssh_alias: {cluster.get('ssh_alias')}")
        print(f"  remote_project_dir: {expand(cluster.get('remote_project_dir'), cluster)}")
        print(f"  account: {cluster.get('account', '<unset>')}")
    except ConfigError as exc:
        print(f"Config warning: {exc}", file=sys.stderr)
        return 2 if missing else 1
    return 1 if missing else 0


def cmd_connect(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    _, cluster = choose_cluster(config, args.cluster)
    socket_dir = Path(args.socket_dir).expanduser()
    if args.dry_run:
        print(f"mkdir -p {shlex.quote(str(socket_dir))}")
    else:
        socket_dir.mkdir(parents=True, exist_ok=True)

    check_cmd = ["ssh", "-O", "check", cluster["ssh_alias"]]
    if not args.dry_run:
        check = subprocess.run(check_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if check.returncode == 0:
            print(f"ControlMaster session already active for {cluster['ssh_alias']}.")
            return 0
        print("No active ControlMaster session found. Starting one now; MFA may prompt.")

    connect_cmd = ["ssh", "-f", "-N", cluster["ssh_alias"]]
    rc = run_command(connect_cmd, args.dry_run)
    if rc != 0:
        return rc
    sanity_cmd = ssh_base(cluster) + ["hostname && pwd"]
    return run_command(sanity_cmd, args.dry_run)


def cmd_remote(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    _, cluster = choose_cluster(config, args.cluster)
    return run_command(ssh_base(cluster) + [args.command], args.dry_run)


def cmd_sync_up(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    _, cluster = choose_cluster(config, args.cluster)
    local_path = args.local_path
    remote_path = expand(args.remote_path or cluster.get("remote_project_dir"), cluster)
    if not remote_path:
        raise ConfigError("No remote path provided and remote_project_dir is unset.")
    remote = f"{cluster['ssh_alias']}:{remote_path}"
    rsync_flags = args.rsync_flags.split()
    return run_command(["rsync", *rsync_flags, local_path, remote], args.dry_run)


def cmd_sync_down(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    _, cluster = choose_cluster(config, args.cluster)
    remote_path = expand(args.remote_path, cluster)
    local_path = args.local_path
    remote = f"{cluster['ssh_alias']}:{remote_path}"
    rsync_flags = args.rsync_flags.split()
    return run_command(["rsync", *rsync_flags, remote, local_path], args.dry_run)


def cmd_submit(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    _, cluster = choose_cluster(config, args.cluster)
    remote_dir = expand(args.remote_dir or cluster.get("remote_project_dir"), cluster)
    if not remote_dir:
        raise ConfigError("No remote_dir provided and remote_project_dir is unset.")

    script = Path(args.script)
    remote_script = script.name
    if args.upload:
        mkdir_rc = run_command(ssh_base(cluster) + [f"mkdir -p {shlex.quote(remote_dir)}"], args.dry_run)
        if mkdir_rc != 0:
            return mkdir_rc
        scp_rc = run_command(["scp", str(script), f"{cluster['ssh_alias']}:{remote_dir}/{remote_script}"], args.dry_run)
        if scp_rc != 0:
            return scp_rc
    else:
        remote_script = args.script

    sbatch_defaults = [expand(flag, cluster) for flag in cluster.get("sbatch_defaults", [])]
    extra = args.sbatch_args or []
    remote_cmd = " ".join(
        [
            "cd",
            shlex.quote(remote_dir),
            "&&",
            "sbatch",
            *[shlex.quote(part) for part in sbatch_defaults],
            *[shlex.quote(part) for part in extra],
            shlex.quote(remote_script),
        ]
    )
    return run_command(ssh_base(cluster) + [remote_cmd], args.dry_run)


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    _, cluster = choose_cluster(config, args.cluster)
    user = cluster.get("user") or os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if args.job_id:
        remote_cmd = (
            f"scontrol show job {shlex.quote(args.job_id)} || true; "
            f"sacct -j {shlex.quote(args.job_id)} --format=JobID,JobName,State,Elapsed,MaxRSS,ExitCode || true"
        )
    else:
        remote_cmd = (
            f"squeue -u {shlex.quote(user)} || true; "
            f"sacct -u {shlex.quote(user)} --starttime=now-2days "
            "--format=JobID,JobName,State,Elapsed,ExitCode | tail -40 || true"
        )
    return run_command(ssh_base(cluster) + [remote_cmd], args.dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-cluster")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to clusters JSON config.")
    parser.add_argument("--cluster", help="Cluster name from config.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="Check local tools and cluster config.")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("connect", help="Open or reuse an SSH ControlMaster session.")
    p.add_argument("--socket-dir", default="~/.ssh/sockets")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("remote", help="Run a remote shell command through SSH.")
    p.add_argument("command")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_remote)

    p = sub.add_parser("sync-up", help="rsync local files to the selected cluster.")
    p.add_argument("local_path")
    p.add_argument("remote_path", nargs="?")
    p.add_argument("--rsync-flags", default="-rlptvz --progress")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_sync_up)

    p = sub.add_parser("sync-down", help="rsync files from the selected cluster.")
    p.add_argument("remote_path")
    p.add_argument("local_path")
    p.add_argument("--rsync-flags", default="-rlptvz --progress")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_sync_down)

    p = sub.add_parser("submit", help="Upload and submit a SLURM script.")
    p.add_argument("script")
    p.add_argument("--remote-dir")
    p.add_argument("--upload", action="store_true", help="Upload local script before sbatch.")
    p.add_argument("--sbatch-args", nargs="*", help="Extra sbatch flags after config defaults.")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("status", help="Show squeue/sacct status.")
    p.add_argument("--job-id")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
