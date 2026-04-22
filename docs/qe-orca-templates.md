# QE And ORCA Templates

## Quantum ESPRESSO

Template:

```text
examples/slurm/qe-manifest-worker.sbatch
```

Important environment variables:

- `QCHEM_WORKDIR`: remote calculation workspace.
- `QE_INPUT_DIR`: directory containing QE input files.
- `QE_OUTPUT_DIR`: output directory.
- `QE_LOG_DIR`: log directory.
- `QE_QUEUE_FILE`: manifest file. Created from inputs if missing.
- `QE_MODULES`: space-separated modules to load.
- `QE_COMMAND`: QE executable, usually `pw.x`.
- `QE_MPI_COMMAND`: launcher, usually `srun`, `mpirun`, or `none`.
- `QE_RECOVERY_COMMAND`: optional command that converts failed output plus old input into a retry input.

Submit example:

```bash
research-cluster --config config/clusters.json submit examples/slurm/qe-manifest-worker.sbatch --upload
```

## ORCA

Template:

```text
examples/slurm/orca-manifest-worker.sbatch
```

Important environment variables:

- `QCHEM_WORKDIR`: remote calculation workspace.
- `ORCA_INPUT_DIR`: directory containing `.inp` files.
- `ORCA_OUTPUT_DIR`: output directory.
- `ORCA_LOG_DIR`: log directory.
- `ORCA_QUEUE_FILE`: manifest file. Created from inputs if missing.
- `ORCA_MODULES`: space-separated modules to load.
- `ORCA_COMMAND`: full ORCA executable path or module-provided `orca`.

ORCA parallel runs are usually controlled inside the input with `%pal`; avoid wrapping ORCA in `mpirun` unless your local cluster documentation explicitly requires it.

