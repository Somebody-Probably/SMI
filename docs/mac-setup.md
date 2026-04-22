# Mac Setup

## Requirements

- macOS with Python 3.10 or newer.
- OpenSSH client.
- rsync.
- Globus CLI, installed by this package.
- An Alliance or institutional cluster account.
- Optional: Homebrew rsync if the system rsync is too old for your preferred flags.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Confirm the package and transfer tools:

```bash
research-smi-doctor
research-globus doctor
```

Create local configs:

```bash
cp config/clusters.example.json config/clusters.json
cp config/globus.example.json config/globus.json
cp config/lanes.local.example.json config/lanes.local.json
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

The example SSH config includes the current Alliance renewal systems used by
this repo: `trillium`, `fir`, `nibi`, `narval`, and `rorqual`.

Open a persistent session:

```bash
research-cluster --config config/clusters.json connect
```

This keeps one MFA-authenticated master connection alive so later `ssh`, `scp`, and `rsync` commands can reuse it.
The example SSH config uses `ControlPersist 4h`; your institution's policy
and SSH config determine the actual lifetime.

Check whether the session is still active:

```bash
research-cluster --config config/clusters.json session status
```

Refresh the idle timer during an active work block:

```bash
research-cluster --config config/clusters.json session touch
```

Close the session when finished:

```bash
research-cluster --config config/clusters.json session close
```

`session touch` only refreshes an already-active connection by default. If you
are present and ready to complete MFA, use `--open-if-missing` to open a new
session first.

## First Local SMI Smoke Test

```bash
research-smi init --run-id smoke-001 --spec examples/hello-task-spec.json
research-smi worker --run-id smoke-001 --lane fast_local --slots 1 --dry-run --once
research-smi status --run-id smoke-001
```

The run state is written under `runs/smoke-001/`. That directory is intentionally ignored by git.

## First Globus Check

Authenticate and validate the CLI:

```bash
globus login
research-globus doctor --check-login
```

Edit `config/globus.json` with local and remote endpoint IDs, then render the
two default transfer plans:

```bash
research-globus plan --config config/globus.json --transfer mac-to-cluster
research-globus plan --config config/globus.json --transfer cluster-to-mac
```
