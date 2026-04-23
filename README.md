# Research Cluster SMI

Portable MacBook-friendly starter repo for computational chemistry workflows:

1. a generic cluster connection loop based on SSH ControlMaster, rsync, SLURM, and artifact harvest;
2. a small generic SMI manager runtime for lanes, slots, tasks, leases, hot-folder orders, and dry-run worker dispatch;
3. chemistry-focused helpers and templates for Quantum ESPRESSO, ORCA, CP2K, and SIESTA smoke tests.

This repo is the extracted baseline from the QFF cluster workflow and the generic SMI manager protocol. It is intentionally small so researchers can audit it before connecting it to real cluster accounts or agent tools.

## Quick Start On A Mac

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

cp config/clusters.example.json config/clusters.json
cp config/globus.example.json config/globus.json
cp config/lanes.local.example.json config/lanes.local.json
mkdir -p ~/.ssh/sockets
research-smi-doctor
```

## Quick Start On Windows 10

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
$env:SMI_RUN_ROOT = "$PWD\runs-windows"
research-smi-doctor --local
```

Then run the local harness in `docs/windows-setup.md`. Windows is used for
cluster-free compatibility testing; MacBook remains the primary path for SSH,
Globus, and real cluster validation.

Edit `config/clusters.json` and add your Alliance username, account, and remote paths. Edit `config/globus.json` with your Globus endpoint IDs and transfer roots. Add the relevant blocks from `config/ssh_config.example` to `~/.ssh/config`. The example config tracks the current Alliance renewal names: Trillium, Fir, Nibi, Narval, and Rorqual, with legacy aliases documented for Niagara, Cedar, Graham, and Beluga.

Check the local setup:

```bash
research-smi-doctor --strict
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
research-cluster --config config/clusters.json session status --dry-run
research-cluster --config config/clusters.json session touch --dry-run
research-cluster --config config/clusters.json sync-up ./ research-workspace --dry-run
research-cluster --config config/clusters.json submit examples/slurm/generic-python-job.sbatch --upload --dry-run
research-cluster --config config/clusters.json submit examples/slurm/generic-python-job.sbatch --upload --profile qff-trillium-debug --dry-run
research-cluster --config config/clusters.json status --dry-run
```

Remove `--dry-run` only after your SSH config and cluster paths are correct.

Transfer, isolation, and retry tools:

```bash
research-globus doctor
globus login
research-globus plan --config config/globus.json --transfer mac-to-cluster
research-smi worker --run-id smoke-001 --lane fast_local --worktrees --repo-root . --dry-run --once
research-smi worker --run-id smoke-001 --lane fast_local --agent codex --worktrees --repo-root . --once
research-smi router --repo-root . list --run-id smoke-001
research-chem parse-output --program qe --output-file outputs/si.scf.out
```

For cluster use, keep one account gate as the always-on coordination layer.
Project SMI runs are optional and useful when a project needs a parallel agent
swarm:

```bash
research-smi account init --account-id drac-fouquet --resources-json config/lanes.cluster-account.example.json
research-smi account watch --account-id drac-fouquet --resources-json config/lanes.cluster-account.example.json --touch-sessions --queue-snapshot
research-smi init --run-id SMI_project_1 --lanes-json config/lanes.project-swarm.example.json
research-smi run --run-id SMI_project_1 --lanes fast_local,heavy_local,verify_local --agent codex --worktrees --repo-root .
```

## Repository Layout

- `MANUAL.md`: complete new-researcher onboarding and operating manual.
- `docs/index.md`: map of all operator, chemistry, transfer, and test docs.
- `docs/windows-setup.md`: Windows 10 local harness for SMI smoke tests.
- `docs/smi-general-harness-plan.md`: cross-platform harness roadmap and private agent-market boundary.
- `src/research_cluster_smi/cluster_cli.py`: cluster connection, sync, submit, and status CLI.
- `src/research_cluster_smi/package_cli.py`: package-level doctor for setup validation.
- `src/research_cluster_smi/chem_cli.py`: computational chemistry manifest and SMI spec helpers.
- `src/research_cluster_smi/globus_cli.py`: Globus transfer planning and submission helpers.
- `src/research_cluster_smi/smi_core.py`: SQLite-backed SMI run state.
- `src/research_cluster_smi/agents.py`: Claude/Codex/custom CLI worker presets.
- `src/research_cluster_smi/account_gate.py`: account-scoped permits shared across project runs.
- `src/research_cluster_smi/account_cluster.py`: account-gated session touch, queue snapshot, SLURM smoke, and harvest operations.
- `src/research_cluster_smi/orders.py`: JSON hot-folder order protocol.
- `src/research_cluster_smi/worker.py`: simple local worker manager, with dry-run support.
- `src/research_cluster_smi/worktree.py` and `router.py`: isolated agent worktrees and reviewed merge flow.
- `config/`: researcher-editable templates.
- `examples/`: smoke task specs, orders, and SLURM job templates, including
  DRAC CPU/GPU smoke tests.
- `docs/`: operator notes and protocol documentation.

## Current Scope

Included:

- Mac-compatible Python CLI and bash wrappers.
- Windows 10 local harness documentation for cluster-free compatibility tests.
- Config-driven SSH aliases, remote paths, and sbatch defaults.
- First-class Globus CLI support for staged input transfer and artifact harvest.
- Current Alliance cluster examples with conservative account-only defaults and opt-in QFF-derived profiles.
- SMI lanes, slots, tasks, attempts, leases, events, and JSONL event log.
- Always-on `SMI_account_*` gate for throttling cluster transfer, submit, and monitor work across all projects on one account.
- Optional `SMI_project_*` runs for projects that need parallel CLI-agent swarms and project-local task graphs.
- Codex CLI worker preset using `codex exec ... -` for prompt-on-stdin spawning.
- Hot-folder orders for seed, cancel, reprioritize, pause, resume, drain, and lane resizing.
- Dry-run worker path that writes result artifacts without launching an agent.
- QE and ORCA manifest-worker SLURM templates inspired by Cluster-FRUC-style batch management.
- Simple molecule SLURM smoke tests for ORCA, Quantum ESPRESSO, CP2K, and SIESTA.
- Globus transfer plans for input staging and artifact harvest.
- Worktree isolation with router-mediated cherry-pick merging.
- QE and ORCA output parsers with retry recommendations.

Deferred:

- Full QFF physics workflows.
- Cluster-side SMI worker daemons.
- Richer engine-specific parsers for Gaussian, CP2K, SIESTA, xTB, and custom lab pipelines.
