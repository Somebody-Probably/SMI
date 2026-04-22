# Mac Setup

## Requirements

- macOS with Python 3.10 or newer.
- OpenSSH client.
- rsync.
- An Alliance or institutional cluster account.
- Optional: Homebrew rsync if the system rsync is too old for your preferred flags.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

## SSH ControlMaster

Create a socket directory:

```bash
mkdir -p ~/.ssh/sockets
chmod 700 ~/.ssh ~/.ssh/sockets
```

Copy the relevant block from `config/ssh_config.example` into `~/.ssh/config`, then edit `User`.

Check the alias:

```bash
ssh trillium hostname
```

Open a persistent session:

```bash
research-cluster --config config/clusters.json connect
```

This keeps one MFA-authenticated master connection alive so later `ssh`, `scp`, and `rsync` commands can reuse it.

## First Local SMI Smoke Test

```bash
research-smi init --run-id smoke-001 --spec examples/hello-task-spec.json
research-smi worker --run-id smoke-001 --lane fast_local --slots 1 --dry-run --once
research-smi status --run-id smoke-001
```

The run state is written under `runs/smoke-001/`. That directory is intentionally ignored by git.

