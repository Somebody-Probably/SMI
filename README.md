# Research Cluster SMI

Portable MacBook-friendly starter repo for computational chemistry workflows:

1. a generic cluster connection loop based on SSH ControlMaster, rsync, SLURM, and artifact harvest;
2. a small generic SMI manager runtime for lanes, slots, tasks, leases, hot-folder orders, and dry-run worker dispatch;
3. chemistry-focused helpers and templates for Quantum ESPRESSO and ORCA.

This repo is the extracted baseline from the QFF cluster workflow and the generic SMI manager protocol. It is intentionally small so researchers can audit it before connecting it to real cluster accounts or agent tools.

## Quick Start On A Mac

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

cp config/clusters.example.json config/clusters.json
cp config/lanes.local.example.json config/lanes.local.json
mkdir -p ~/.ssh/sockets
```

Edit `config/clusters.json` and add your Alliance username, account, and remote paths. Add the relevant blocks from `config/ssh_config.example` to `~/.ssh/config`.

Check the local setup:

```bash
research-cluster --config config/clusters.json doctor
```

Try the SMI dry-run path:

```bash
research-smi init --run-id smoke-001 --spec examples/hello-task-spec.json
research-smi worker --run-id smoke-001 --lane fast_local --slots 1 --dry-run --once
research-smi status --run-id smoke-001
```

Try a chemistry task dry-run:

```bash
research-chem make-spec --program qe --input-dir examples/qe/input --output examples/chemistry/generated-qe-spec.json
research-smi init --run-id qe-demo --spec examples/chemistry/generated-qe-spec.json
research-smi worker --run-id qe-demo --lane remote_cluster --slots 1 --dry-run --once
research-smi status --run-id qe-demo
```

Try the cluster dry-run path:

```bash
research-cluster --config config/clusters.json connect --dry-run
research-cluster --config config/clusters.json sync-up ./ research-workspace --dry-run
research-cluster --config config/clusters.json submit examples/slurm/generic-python-job.sbatch --upload --dry-run
research-cluster --config config/clusters.json status --dry-run
```

Remove `--dry-run` only after your SSH config and cluster paths are correct.

## Repository Layout

- `src/research_cluster_smi/cluster_cli.py`: cluster connection, sync, submit, and status CLI.
- `src/research_cluster_smi/chem_cli.py`: computational chemistry manifest and SMI spec helpers.
- `src/research_cluster_smi/smi_core.py`: SQLite-backed SMI run state.
- `src/research_cluster_smi/orders.py`: JSON hot-folder order protocol.
- `src/research_cluster_smi/worker.py`: simple local worker manager, with dry-run support.
- `config/`: researcher-editable templates.
- `examples/`: smoke task specs, orders, and SLURM job templates.
- `docs/`: operator notes and protocol documentation.

## Current Scope

Included:

- Mac-compatible Python CLI and bash wrappers.
- Config-driven SSH aliases, remote paths, and sbatch defaults.
- SMI lanes, slots, tasks, attempts, leases, events, and JSONL event log.
- Hot-folder orders for seed, cancel, reprioritize, pause, resume, drain, and lane resizing.
- Dry-run worker path that writes result artifacts without launching an agent.
- QE and ORCA manifest-worker SLURM templates inspired by Cluster-FRUC-style batch management.

Deferred:

- Full QFF physics workflows.
- Globus transfer automation.
- Worktree isolation and router-mediated merging.
- Cluster-side SMI worker daemons.
- Rich chemistry output parsers and automatic retry policies.
