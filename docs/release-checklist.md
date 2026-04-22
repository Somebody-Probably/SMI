# Release Checklist

Use this before handing a checkout to a new research group member.

## Package Health

```bash
source .venv/bin/activate
python -m pip install -e .
research-smi-doctor --strict
research-globus doctor
python -m pytest
```

If `research-smi-doctor` reports `codex` as informationally missing, decide whether that user needs agent swarms.

## Fresh Clone Test

```bash
cd /tmp
git clone <repo-url> smi-handoff-test
cd smi-handoff-test
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
research-smi-doctor
research-globus doctor
research-smi init --run-id smoke-001 --spec examples/hello-task-spec.json
research-smi worker --run-id smoke-001 --lane fast_local --slots 1 --dry-run --once
research-smi status --run-id smoke-001
```

## Cluster Readiness

1. Copy `config/clusters.example.json` to `config/clusters.json`.
2. Replace `YOUR_ALLIANCE_USERNAME` and `YOUR_SLURM_ACCOUNT`.
3. Copy the relevant `config/ssh_config.example` blocks into `~/.ssh/config`.
4. Run `research-cluster --config config/clusters.json doctor`.
5. Run `research-cluster --config config/clusters.json connect` while the user can complete MFA.
6. Run `research-cluster --config config/clusters.json session status --remote-check`.

## Globus Readiness

1. Copy `config/globus.example.json` to `config/globus.json`.
2. Replace local and remote endpoint IDs.
3. Replace local, scratch, and project transfer roots.
4. Run `globus login`.
5. Run `research-globus doctor --check-login`.
6. Run `research-globus plan --config config/globus.json --transfer mac-to-cluster`.
7. Run `research-globus plan --config config/globus.json --transfer cluster-to-mac`.

## Account SMI Readiness

```bash
research-smi account init \
  --account-id drac-<username> \
  --resources-json config/lanes.cluster-account.example.json

research-smi account watch \
  --account-id drac-<username> \
  --resources-json config/lanes.cluster-account.example.json \
  --touch-sessions \
  --queue-snapshot \
  --once
```

Confirm that these files are produced:

```text
runs/SMI_account_drac-<username>/account_gate.sqlite
runs/SMI_account_drac-<username>/account_state.json
runs/SMI_account_drac-<username>/account_state.md
```

## Build Artifact

```bash
python -m pip install --upgrade build
python -m build
```

Confirm the source distribution contains `config/`, `docs/`, `examples/`, and `bin/`.
