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
research-cluster --config config/clusters.json sync-up ./ /scratch/YOUR_USER/research-workspace/
research-cluster --config config/clusters.json submit examples/slurm/generic-python-job.sbatch --upload
research-cluster --config config/clusters.json submit examples/slurm/generic-python-job.sbatch --upload --profile qff-trillium-debug
research-cluster --config config/clusters.json status
research-cluster --config config/clusters.json sync-down /scratch/YOUR_USER/research-workspace/outputs/ ./outputs/
```

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
