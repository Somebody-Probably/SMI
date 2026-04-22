# Chemistry Smoke Tests

This repo includes small molecule smoke tests for:

- ORCA: `examples/slurm/orca-smoke.sbatch`
- Quantum ESPRESSO: `examples/slurm/qe-smoke.sbatch`
- CP2K: `examples/slurm/cp2k-smoke.sbatch`
- SIESTA: `examples/slurm/siesta-smoke.sbatch`

The examples are intentionally tiny. They verify that the cluster module,
launcher, scratch handling, and artifact harvest path work; they are not meant
to be production-quality chemistry inputs.

## Alliance Module Recipes

The current DRAC/Alliance renewal systems expose these modules:

```bash
ORCA_MODULES=StdEnv/2023:gcc/12.3:openmpi/4.1.5:orca/6.1.1
QE_MODULES=StdEnv/2023:gcc/12.3:openmpi/4.1.5:quantumespresso/7.5
CP2K_MODULES=StdEnv/2023:gcc/12.3:openmpi/4.1.5:cp2k/2025.2
SIESTA_MODULES=StdEnv/2023:intel/2023.2.1:openmpi/4.1.5:siesta/5.4.0
```

Use colon-separated module lists when passing values through `sbatch
--export`; Slurm uses commas as separators.

ORCA is a special case on Alliance: the module sets `EBROOTORCA`, but does not
put `orca` on `PATH`. The smoke script detects this and runs
`${EBROOTORCA}/orca`.

## Pseudopotentials

QE and SIESTA need pseudopotentials. Do not commit downloaded pseudopotential
sets into this repo by default. Stage them into the run directory instead:

- QE expects UPF files under `pseudo/`.
- SIESTA 5 can read PSML files under `pseudo/` via `SIESTA_PS_PATH`.

The Fir smoke test used PseudoDojo `nc-sr-04_pbe_standard` UPF files for QE and
PSML files for SIESTA.

## GP-Cluster Memory Note

On Fir `cpubase_bycore_b1`, the default memory allocation was too small for
QE, CP2K, and SIESTA. Use an explicit request such as:

```bash
sbatch --account=def-ipaci_cpu --partition=cpubase_bycore_b1 --mem=2G ...
```

ORCA's tiny HF/STO-3G water smoke completed without the extra memory request,
but using a small explicit memory request for all chemistry smoke tests is a
good default on the GP clusters.
