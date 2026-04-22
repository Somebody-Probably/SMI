# Research Cluster SMI Manual

This manual is for a researcher starting from a fresh macOS checkout. It covers
the everyday workflow: install the package, configure SSH, open an MFA-backed
cluster session, run local and remote smoke tests, and use the SMI account layer
when several projects or agent swarms share one cluster account.

## Mental Model

The package has three layers:

1. `research-cluster` handles direct cluster operations: SSH ControlMaster
   sessions, rsync/scp transfer, SLURM submission, status checks, and harvest.
2. `SMI_account_*` is the always-on account gate. It coordinates shared cluster
   resources for one login/account and writes visible state files that swarm
   workers can read without opening their own scheduler connections.
3. `SMI_project_*` is optional. Use it when a single project benefits from
   parallel CLI-agent workers. Project workers can do local work freely and
   acquire account permits only when they need remote transfer, submit, monitor,
   or harvest work.

The account manager should parallelize remote work up to configured limits. It
should serialize only when the configured transfer, submit, or monitor resource
is saturated.

## Install On macOS

```bash
git clone <repo-url> SMI
cd SMI
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Run the package doctor from the repository root:

```bash
research-smi-doctor
```

`globus`, `ssh`, `scp`, and `rsync` should all report `OK`. The `codex`
message is informational unless you need CLI-agent swarms.

## Configure Cluster Access

Create local configs:

```bash
cp config/clusters.example.json config/clusters.json
cp config/globus.example.json config/globus.json
cp config/lanes.local.example.json config/lanes.local.json
mkdir -p ~/.ssh/sockets
chmod 700 ~/.ssh ~/.ssh/sockets
```

Edit `config/clusters.json`:

- replace `YOUR_ALLIANCE_USERNAME`;
- replace `YOUR_SLURM_ACCOUNT`;
- keep `remote_project_dir` on scratch unless your group documents a different
  path;
- keep the default `account-only` SLURM profile for first tests.

Copy the relevant host blocks from `config/ssh_config.example` into
`~/.ssh/config`, then set `User` to your cluster username.

## Configure Globus

Authenticate the Globus CLI:

```bash
research-globus doctor
globus login
research-globus doctor --check-login
```

Edit `config/globus.json`:

- set the local Globus Connect Personal endpoint id;
- set the cluster or group storage endpoint id;
- set `root` values to the local working directory, scratch workspace, and
  optional project storage path;
- keep `mac-to-cluster` and `cluster-to-mac` as the first two transfer names so
  the examples and SMI recipes remain consistent.

Render the transfer plans before submitting anything:

```bash
research-globus plan --config config/globus.json --transfer mac-to-cluster
research-globus plan --config config/globus.json --transfer cluster-to-mac
```

When the paths are correct, submit a transfer:

```bash
research-globus submit --config config/globus.json --transfer mac-to-cluster
```

Submitted task records are appended to `runs/globus-tasks.jsonl`.

Check local readiness:

```bash
research-smi-doctor --strict
research-cluster --config config/clusters.json doctor
```

## First Local Smoke Test

This verifies the SMI runtime without launching agents or touching the cluster:

```bash
research-smi init --run-id smoke-001 --spec examples/hello-task-spec.json
research-smi worker --run-id smoke-001 --lane fast_local --slots 1 --dry-run --once
research-smi status --run-id smoke-001
```

The run state is written under `runs/smoke-001/`.

## Open And Maintain MFA Sessions

Open a persistent SSH master connection while you are present for MFA:

```bash
research-cluster --config config/clusters.json connect
research-cluster --config config/clusters.json session status --remote-check
```

Touch an active session during long work blocks:

```bash
research-cluster --config config/clusters.json session touch
```

Close the session when finished:

```bash
research-cluster --config config/clusters.json session close
```

The example SSH config uses `ControlPersist 4h`, but the real lifetime is set
by your SSH configuration and cluster policy.

## Start The Account SMI

Use one account id per cluster login/account. A clear convention is
`drac-<username>`:

```bash
research-smi account init \
  --account-id drac-<username> \
  --resources-json config/lanes.cluster-account.example.json
```

In a long-running terminal, start the watcher:

```bash
research-smi account watch \
  --account-id drac-<username> \
  --resources-json config/lanes.cluster-account.example.json \
  --touch-sessions \
  --queue-snapshot \
  --interval 60
```

The watcher writes:

```text
runs/SMI_account_drac-<username>/account_gate.sqlite
runs/SMI_account_drac-<username>/account_state.json
runs/SMI_account_drac-<username>/account_state.md
```

Swarm agents should read `account_state.json` before deciding whether to submit
or poll. They should still acquire permits for cluster-facing phases.

## Direct Account Cluster Smoke

Run a small SLURM smoke job through the account gate on one or more clusters:

```bash
RUN_ID="account-smoke-$(date +%Y%m%d-%H%M%S)"

research-smi account cluster-smoke \
  --account-id drac-<username> \
  --resources-json config/lanes.cluster-account.example.json \
  --cluster-config config/clusters.json \
  --clusters rorqual,trillium \
  --label "$RUN_ID" \
  --local-output-dir "runs/$RUN_ID/account-direct" \
  --poll-interval 10 \
  --timeout 900 \
  --open-if-missing
```

Expected outputs:

```text
runs/<RUN_ID>/account-direct/<cluster>/summary.json
runs/<RUN_ID>/account-direct/<cluster>/results/
runs/<RUN_ID>/account-direct/<cluster>/logs/
```

By default, cluster workflows run concurrently up to account resource limits.
Use `--serial` only when debugging.

## Optional Project SMI Swarm

Initialize a project run when you want parallel CLI-agent work:

```bash
research-smi init \
  --run-id SMI_project_1 \
  --lanes-json config/lanes.project-swarm.example.json
```

Run local project workers with account gating enabled for remote lanes:

```bash
research-smi run \
  --run-id SMI_project_1 \
  --lanes fast_local,heavy_local,verify_local,remote_transfer,remote_submit,remote_monitor \
  --slots 2 \
  --parallel \
  --agent codex \
  --worktrees \
  --repo-root . \
  --account-gate-id drac-<username> \
  --account-gated-lanes remote_transfer,remote_submit,remote_monitor,remote_cluster
```

Use project SMI only when parallel work is useful. A single active project can
often use the account SMI directly.

## Chemistry Smoke Tests

The repository includes simple molecule inputs and SLURM scripts for:

- ORCA: `examples/orca/input/water-smoke.inp`
- Quantum ESPRESSO: `examples/qe/input/h2.scf.in` and `water.scf.in`
- CP2K: `examples/cp2k/input/water.inp`
- SIESTA: `examples/siesta/input/water.fdf`

Read `docs/chemistry-smoke-tests.md` before running these jobs. Each cluster
may use different module names, CPU partitions, GPU partitions, or software
availability. Keep first tests small and account-only, then move site-specific
resource flags into SLURM scripts or named profiles after validation.

## Daily Workflow

1. Pull the latest repo changes.
2. Activate `.venv`.
3. Run `research-smi-doctor`.
4. Run `research-globus doctor --check-login` before transfer-heavy work.
5. Open cluster sessions with `research-cluster connect` while present for MFA.
6. Start one `research-smi account watch` terminal for the account.
7. Run direct account jobs, Globus transfers, or project SMI swarms.
8. Check `runs/SMI_account_<account-id>/account_state.md` for queue and lane
   state.
9. Harvest outputs into `runs/<run-id>/...`.
10. Close cluster sessions when finished.

## Troubleshooting

If `research-smi-doctor` reports placeholder config values, edit
`config/clusters.json` or `config/globus.json` before real cluster use.

If `research-globus doctor --check-login` fails, run `globus login` again.

If `session status` is inactive, run `research-cluster connect` while you can
complete MFA. Use `session touch --open-if-missing` only when you are present.

If workers appear idle, check:

```bash
research-smi status --run-id <run-id>
research-smi account status --account-id drac-<username>
research-smi account state --account-id drac-<username> --format markdown
```

If a permit is stuck after a killed process, release it manually:

```bash
research-smi account release \
  --account-id drac-<username> \
  --permit-id <permit-id>
```

If cluster jobs remain pending, inspect the scheduler reason rather than
submitting more jobs. The SMI account layer limits request spikes; it does not
override cluster priority, reservations, drained nodes, or allocation limits.

## Maintainer Notes

Before sharing a release, run `docs/release-checklist.md`. Keep examples small,
auditable, and conservative. Prefer account-only default submission profiles so
site-specific resource decisions remain visible in SLURM scripts or explicitly
named profiles.
