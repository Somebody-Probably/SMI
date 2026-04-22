# Computational Chemistry Workflow

This repo is now aimed at computational chemistry groups that need the same cluster workflow across laptops, CLI agents, and app agents.

The first supported profiles are:

- Quantum ESPRESSO (`qe`) for `pw.x` style input files.
- ORCA (`orca`) for `.inp` calculations.

## Chemistry Loop

1. Prepare input files locally.
2. Generate a manifest or an SMI task spec.
3. Stage inputs, pseudopotentials, basis files, and helper scripts to the cluster.
4. Submit a manifest worker or one-off SLURM script.
5. Monitor queue state and output health.
6. Harvest outputs, logs, restart files, and a short human-readable report.
7. Seed follow-up tasks for failed, unconverged, or completed calculations.

## CLI Helpers

Create a QE manifest:

```bash
research-chem make-manifest \
  --program qe \
  --input-dir examples/qe/input \
  --output qe.jobs
```

Create SMI tasks for an app or CLI agent:

```bash
research-chem make-spec \
  --program qe \
  --input-dir examples/qe/input \
  --output examples/chemistry/generated-qe-spec.json
```

Seed and dry-run the SMI manager:

```bash
research-smi init --run-id qe-demo --spec examples/chemistry/qe-batch-task-spec.json
research-smi worker --run-id qe-demo --lane remote_cluster --slots 1 --dry-run --once
research-smi status --run-id qe-demo
```

## Agent Contract

Agents should treat each SMI task as a bounded calculation-management packet:

- `metadata.domain` should be `computational_chemistry`.
- `metadata.program` should be `qe`, `orca`, or another supported engine.
- `metadata.input_file` points to the calculation input.
- `metadata.slurm_template` points to the suggested cluster template.
- `write_set` names expected outputs, logs, and reports.

The agent should not invent chemistry results. It should report observed output, convergence state, errors, missing files, and recommended next actions.

## Cluster-FRUC Adaptation

The QE manifest worker borrows the useful control pattern from [Cluster-FRUC](https://github.com/jgibb-s/Cluster-FRUC):

- create a queue from many QE input files;
- let multiple workers pop jobs under a lock;
- skip calculations whose output already contains a success sentinel;
- keep completion and failure logs;
- allow failed QE jobs to be converted into retry inputs by a recovery hook.

Changes made for this repo:

- no hard-coded `$HOME/cluster_jobs`;
- no blanket deletion of scratch directories;
- no bundled pseudopotential library;
- all paths are configured through environment variables;
- completion, failure, and recovery are explicit and auditable.

