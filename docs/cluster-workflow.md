# Cluster Workflow

The portable cluster loop is:

1. configure SSH aliases and persistent sockets;
2. open a ControlMaster session;
3. sync code or data to a remote workspace;
4. run remote setup or submit a SLURM job;
5. monitor the job;
6. sync artifacts back.

## Commands

```bash
research-cluster --config config/clusters.json doctor
research-cluster --config config/clusters.json connect
research-cluster --config config/clusters.json session status
research-cluster --config config/clusters.json session touch
research-cluster --config config/clusters.json sync-up ./ /scratch/YOUR_USER/research-workspace/
research-cluster --config config/clusters.json submit examples/slurm/generic-python-job.sbatch --upload
research-cluster --config config/clusters.json submit examples/slurm/generic-python-job.sbatch --upload --profile qff-trillium-debug
research-cluster --config config/clusters.json status
research-cluster --config config/clusters.json sync-down /scratch/YOUR_USER/research-workspace/outputs/ ./outputs/
research-cluster --config config/clusters.json session close
```

## MFA Session Workflow

The recommended agent workflow is operator-gated:

1. The researcher opens a ControlMaster session with `research-cluster connect`.
   Any MFA prompt happens here, in the user's terminal or institutional flow.
2. Agents and helper commands reuse that already-authenticated SSH alias for
   `remote`, `sync-up`, `submit`, `status`, and `sync-down`.
3. Before cluster work, scripts can run `research-cluster session status`.
   A zero exit code means the session is active; a nonzero exit code means the
   researcher should reopen it.
4. During a long work block, `research-cluster session touch` runs a tiny remote
   command through the existing master connection. This refreshes the
   `ControlPersist` idle timer without storing passwords or MFA material.
5. Use `research-cluster session close` when the authenticated work window is
   finished.

`session touch` does not open a missing login by default. A researcher can pass
`--open-if-missing` when they are present and ready to complete MFA.

## Config Fields

- `ssh_alias`: the local alias in `~/.ssh/config`.
- `user`: cluster username.
- `scratch_dir`: remote scratch location.
- `project_dir`: optional project allocation path.
- `remote_project_dir`: default sync and submit directory.
- `account`: SLURM account.
- `sbatch_defaults`: backward-compatible default flags added by `research-cluster submit`.
- `default_sbatch_profile`: named profile used by `submit` when no `--profile` is supplied.
- `sbatch_profiles`: named sets of default `sbatch` flags.

Values may use placeholders such as `{user}` and `{account}`.

The example Alliance config now uses account-only defaults for normal submits.
That avoids passing stale partition, GPU, CPU, or memory assumptions to clusters
whose policies can change. Lab-specific profiles such as `qff-trillium-debug`
are opt-in.

## Policy Notes

- Do not run heavy compute on login nodes.
- Keep source and small configs in git; keep large data and run outputs out of git.
- Prefer scratch for active runs and project storage for durable shared data.
- For large transfers, use institutional tools such as Globus when available.
- Avoid writing runtime logs to `$HOME` on compute nodes.
- Verify partitions, GPU GRES names, and max walltime on the target cluster with `sinfo` before relying on an optional profile.
- Do not automate MFA entry or store passwords. Keep cluster access bounded to
  the user's active ControlMaster session.

## Alliance Smoke-Test Notes

- Keep generic smoke jobs conservative and pass cluster-specific `sbatch`
  choices from the command line or a profile. Some systems reject otherwise
  normal directives; for example, Trillium rejects `--mem` and requires at
  least 15 minutes for normal `compute` jobs.
- On the renewal GP clusters, direct partition names are sometimes routing or
  policy surfaces rather than universally submit-ready targets. Treat
  `sbatch --test-only` as the first check before teaching users a profile.
- `cpubase_bycore_b*` works well for tiny CPU smoke tests. `cpubase_bynode_b*`
  needs a full-node-style request and may wait in queue even for short tests.
- `cpularge_bycore_b*` can require a genuinely high-memory request before the
  submit filter accepts it. If a direct partition submit is rejected, record the
  scheduler message and prefer the cluster's routed default unless the lab has a
  confirmed account/profile recipe.
- GPU smoke scripts should tolerate vendor differences. On AMD MI300A nodes,
  `nvidia-smi` may exist in the environment but fail; scripts should fall back
  to `rocm-smi`.
