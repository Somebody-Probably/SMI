#!/usr/bin/env bash
#SBATCH --job-name=cp2k-smoke
#SBATCH --time=00:15:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail

WORKDIR="${CHEM_WORKDIR:-$PWD}"
INPUT_FILE="${CP2K_INPUT_FILE:-$WORKDIR/input/water.inp}"
OUTPUT_DIR="${CP2K_OUTPUT_DIR:-$WORKDIR/outputs}"
LOG_DIR="${CP2K_LOG_DIR:-$WORKDIR/logs}"
RESULT_DIR="${CP2K_RESULT_DIR:-$WORKDIR/results}"
SCRATCH_ROOT="${CHEM_SCRATCH:-${SLURM_TMPDIR:-$WORKDIR/scratch}}"
CP2K_MODULES="${CP2K_MODULES:-}"
CP2K_COMMAND="${CP2K_COMMAND:-cp2k.psmp}"
CP2K_LAUNCHER="${CP2K_LAUNCHER:-srun}"

mkdir -p "$OUTPUT_DIR" "$LOG_DIR" "$RESULT_DIR" "$SCRATCH_ROOT"

if [[ -n "$CP2K_MODULES" ]]; then
  if type module >/dev/null 2>&1; then
    module_list="${CP2K_MODULES//,/ }"
    module_list="${module_list//:/ }"
    for module_name in $module_list; do
      module load "$module_name"
    done
  else
    echo "warning: module command not available; CP2K_MODULES ignored" >&2
  fi
fi

if ! command -v "$CP2K_COMMAND" >/dev/null 2>&1; then
  echo "error=CP2K command not found: $CP2K_COMMAND" >&2
  exit 127
fi

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-${SLURM_CPUS_PER_TASK:-1}}"

input_name="$(basename "$INPUT_FILE")"
stem="${input_name%.inp}"
run_dir="$SCRATCH_ROOT/cp2k-${SLURM_JOB_ID:-manual}-$$"
output_file="$OUTPUT_DIR/${stem}.out"
result_file="$RESULT_DIR/cp2k-${SLURM_JOB_ID:-manual}.txt"
mkdir -p "$run_dir"
cp "$INPUT_FILE" "$run_dir/$input_name"

if [[ -n "${CP2K_DATA_DIR:-}" ]]; then
  ln -sf "$CP2K_DATA_DIR/BASIS_MOLOPT" "$run_dir/BASIS_MOLOPT"
  ln -sf "$CP2K_DATA_DIR/GTH_POTENTIALS" "$run_dir/GTH_POTENTIALS"
fi

{
  echo "SMI CP2K smoke test"
  echo "cluster=${SLURM_CLUSTER_NAME:-unknown}"
  echo "job_id=${SLURM_JOB_ID:-unknown}"
  echo "host=$(hostname)"
  echo "input=$INPUT_FILE"
  echo "omp_num_threads=$OMP_NUM_THREADS"
  echo "started_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} > "$result_file"

cd "$run_dir"
if [[ "$CP2K_LAUNCHER" == "none" ]]; then
  cmd=("$CP2K_COMMAND" "-i" "$input_name" "-o" "${stem}.out")
else
  cmd=("$CP2K_LAUNCHER" "-n" "${SLURM_NTASKS:-1}" "$CP2K_COMMAND" "-i" "$input_name" "-o" "${stem}.out")
fi
echo "command=${cmd[*]}" >> "$result_file"

if "${cmd[@]}" > "$LOG_DIR/${stem}.log" 2>&1; then
  rc=0
else
  rc=$?
fi

cp "$run_dir/${stem}.out" "$output_file" 2>/dev/null || true

if grep -q "PROGRAM ENDED AT" "$output_file"; then
  echo "status=completed" >> "$result_file"
else
  echo "status=failed" >> "$result_file"
fi
echo "exit_code=$rc" >> "$result_file"
echo "completed_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$result_file"

exit "$rc"
