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
DEFAULT_SOCKET_DIR = Path(os.environ.get("RESEARCH_CLUSTER_SOCKET_DIR", "~/.ssh/sockets"))


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
    with path.open("r", encoding="utf-8-sig") as handle:
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


def sbatch_defaults_for(cluster: dict[str, Any], profile: str | None = None) -> list[str]:
    profiles = cluster.get("sbatch_profiles") or {}
    selected = profile or cluster.get("default_sbatch_profile")
    if selected:
        if selected not in profiles:
            known = ", ".join(sorted(profiles)) or "<none>"
            raise ConfigError(f"Unknown sbatch profile {selected!r}. Known profiles: {known}")
        defaults = profiles[selected].get("sbatch_defaults", [])
    else:
        defaults = cluster.get("sbatch_defaults", [])
    if not isinstance(defaults, list) or not all(isinstance(item, str) for item in defaults):
        raise ConfigError("sbatch defaults must be a list of strings.")
    return defaults


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


def quote_local_path(path: str | Path) -> str:
    value = str(path)
    if os.name == "nt":
        return value
    return shlex.quote(value)


def ssh_control_command(cluster: dict[str, Any], operation: str) -> list[str]:
    return ["ssh", "-O", operation, cluster["ssh_alias"]]


def control_check(cluster: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ssh_control_command(cluster, "check"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def completed_record(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "command": completed.args,
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }


def load_selected_cluster(args: argparse.Namespace) -> tuple[dict[str, Any], str, dict[str, Any]]:
    config = load_config(Path(args.config))
    name, cluster = choose_cluster(config, args.cluster)
    return config, name, cluster


def connect_cluster(cluster: dict[str, Any], socket_dir: str | Path, *, dry_run: bool = False) -> int:
    expanded_socket_dir = Path(socket_dir).expanduser()
    if dry_run:
        print(f"mkdir -p {quote_local_path(expanded_socket_dir)}")
    else:
        expanded_socket_dir.mkdir(parents=True, exist_ok=True)
    return run_command(["ssh", "-f", "-N", cluster["ssh_alias"]], dry_run)


def connect_hint(args: argparse.Namespace) -> str:
    command = ["research-cluster", "--config", str(args.config)]
    if args.cluster:
        command.extend(["--cluster", args.cluster])
    command.append("connect")
    return shlex.join(command)


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
        profiles = sorted((cluster.get("sbatch_profiles") or {}).keys())
        if profiles:
            print(f"  sbatch_profiles: {', '.join(profiles)}")
    except ConfigError as exc:
        print(f"Config warning: {exc}", file=sys.stderr)
        return 2 if missing else 1
    return 1 if missing else 0


def cmd_connect(args: argparse.Namespace) -> int:
    _, cluster_name, cluster = load_selected_cluster(args)
    if not args.dry_run:
        check = control_check(cluster)
        if check.returncode == 0:
            print(f"ControlMaster session already active for {cluster['ssh_alias']}.")
            return 0
        print(f"No active ControlMaster session found for {cluster_name}. Starting one now; MFA may prompt.")

    rc = connect_cluster(cluster, args.socket_dir, dry_run=args.dry_run)
    if rc != 0:
        return rc
    sanity_cmd = ssh_base(cluster) + ["hostname && pwd"]
    return run_command(sanity_cmd, args.dry_run)


def cmd_session_status(args: argparse.Namespace) -> int:
    _, cluster_name, cluster = load_selected_cluster(args)
    check_command = ssh_control_command(cluster, "check")
    if args.dry_run:
        print(shlex.join(check_command))
        if args.remote_check:
            print(shlex.join(ssh_base(cluster) + [args.remote_command]))
        return 0

    check = control_check(cluster)
    active = check.returncode == 0
    remote = None
    if active and args.remote_check:
        remote = subprocess.run(
            ssh_base(cluster) + [args.remote_command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    if args.json:
        payload = {
            "cluster": cluster_name,
            "ssh_alias": cluster["ssh_alias"],
            "active": active,
            "control_check": completed_record(check),
            "remote_check": completed_record(remote) if remote else None,
        }
        print(json.dumps(payload, indent=2))
    else:
        status = "active" if active else "inactive"
        print(f"Cluster session: {cluster_name} ({cluster['ssh_alias']})")
        print(f"Status: {status}")
        details = check.stdout.strip() or check.stderr.strip()
        if details:
            print(details)
        if remote:
            remote_details = remote.stdout.strip() or remote.stderr.strip()
            print(f"Remote check: {'ok' if remote.returncode == 0 else 'failed'}")
            if remote_details:
                print(remote_details)
        if not active:
            print(f"Open with: {connect_hint(args)}")

    if not active:
        return 1
    if remote and remote.returncode != 0:
        return remote.returncode
    return 0


def cmd_session_touch(args: argparse.Namespace) -> int:
    _, cluster_name, cluster = load_selected_cluster(args)
    touch_command = ssh_base(cluster) + [args.command]
    if args.dry_run:
        print(shlex.join(ssh_control_command(cluster, "check")))
        if args.open_if_missing:
            print(f"mkdir -p {quote_local_path(Path(args.socket_dir).expanduser())}")
            print(shlex.join(["ssh", "-f", "-N", cluster["ssh_alias"]]))
        print(shlex.join(touch_command))
        return 0

    check = control_check(cluster)
    if check.returncode != 0:
        if not args.open_if_missing:
            print(f"No active ControlMaster session for {cluster_name}.")
            print(f"Open with: {connect_hint(args)}")
            return 1
        print(f"No active ControlMaster session for {cluster_name}. Starting one now; MFA may prompt.")
        rc = connect_cluster(cluster, args.socket_dir)
        if rc != 0:
            return rc

    rc = run_command(touch_command)
    if rc == 0:
        print(f"Session touched for {cluster_name}.")
    return rc


def cmd_session_close(args: argparse.Namespace) -> int:
    _, cluster_name, cluster = load_selected_cluster(args)
    exit_command = ssh_control_command(cluster, "exit")
    if args.dry_run:
        print(shlex.join(ssh_control_command(cluster, "check")))
        print(shlex.join(exit_command))
        return 0

    check = control_check(cluster)
    if check.returncode != 0:
        print(f"No active ControlMaster session for {cluster_name}.")
        return 0
    return run_command(exit_command)


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

    sbatch_defaults = [expand(flag, cluster) for flag in sbatch_defaults_for(cluster, args.profile)]
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
    p.add_argument("--socket-dir", default=str(DEFAULT_SOCKET_DIR))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_connect)

    p = sub.add_parser("session", help="Inspect and manage the SSH ControlMaster session.")
    session_sub = p.add_subparsers(dest="session_command", required=True)

    p_status = session_sub.add_parser("status", help="Check whether the ControlMaster session is active.")
    p_status.add_argument("--remote-check", action="store_true", help="Also run a tiny remote command.")
    p_status.add_argument("--remote-command", default="hostname && pwd")
    p_status.add_argument("--json", action="store_true")
    p_status.add_argument("--dry-run", action="store_true")
    p_status.set_defaults(func=cmd_session_status)

    p_touch = session_sub.add_parser("touch", help="Refresh an active ControlMaster session.")
    p_touch.add_argument("--command", default="true", help="Remote command used to touch the session.")
    p_touch.add_argument(
        "--open-if-missing",
        action="store_true",
        help="Open a new ControlMaster session first if none is active. MFA may prompt.",
    )
    p_touch.add_argument("--socket-dir", default=str(DEFAULT_SOCKET_DIR))
    p_touch.add_argument("--dry-run", action="store_true")
    p_touch.set_defaults(func=cmd_session_touch)

    p_close = session_sub.add_parser("close", help="Close the active ControlMaster session.")
    p_close.add_argument("--dry-run", action="store_true")
    p_close.set_defaults(func=cmd_session_close)

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
    p.add_argument("--profile", help="Named sbatch profile from the selected cluster config.")
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
