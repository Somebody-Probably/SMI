#!/usr/bin/env bash
#SBATCH --job-name=qe-smoke
#SBATCH --time=00:15:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail

WORKDIR="${CHEM_WORKDIR:-$PWD}"
INPUT_FILE="${QE_INPUT_FILE:-$WORKDIR/input/water.scf.in}"
PSEUDO_DIR="${QE_PSEUDO_DIR:-$WORKDIR/pseudo}"
OUTPUT_DIR="${QE_OUTPUT_DIR:-$WORKDIR/outputs}"
LOG_DIR="${QE_LOG_DIR:-$WORKDIR/logs}"
RESULT_DIR="${QE_RESULT_DIR:-$WORKDIR/results}"
SCRATCH_ROOT="${CHEM_SCRATCH:-${SLURM_TMPDIR:-$WORKDIR/scratch}}"
QE_MODULES="${QE_MODULES:-}"
QE_COMMAND="${QE_COMMAND:-pw.x}"
QE_LAUNCHER="${QE_LAUNCHER:-srun}"

mkdir -p "$OUTPUT_DIR" "$LOG_DIR" "$RESULT_DIR" "$SCRATCH_ROOT"

if [[ -n "$QE_MODULES" ]]; then
  if type module >/dev/null 2>&1; then
    module_list="${QE_MODULES//,/ }"
    module_list="${module_list//:/ }"
    for module_name in $module_list; do
      module load "$module_name"
    done
  else
    echo "warning: module command not available; QE_MODULES ignored" >&2
  fi
fi

if ! command -v "$QE_COMMAND" >/dev/null 2>&1; then
  echo "error=QE command not found: $QE_COMMAND" >&2
  exit 127
fi

input_name="$(basename "$INPUT_FILE")"
stem="${input_name%.in}"
run_dir="$SCRATCH_ROOT/qe-${SLURM_JOB_ID:-manual}-$$"
output_file="$OUTPUT_DIR/${stem}.out"
result_file="$RESULT_DIR/qe-${SLURM_JOB_ID:-manual}.txt"
mkdir -p "$run_dir/pseudo" "$run_dir/tmp"
cp "$INPUT_FILE" "$run_dir/$input_name"
cp "$PSEUDO_DIR"/* "$run_dir/pseudo/"
export ESPRESSO_TMPDIR="$run_dir/tmp"

{
  echo "SMI Quantum ESPRESSO smoke test"
  echo "cluster=${SLURM_CLUSTER_NAME:-unknown}"
  echo "job_id=${SLURM_JOB_ID:-unknown}"
  echo "host=$(hostname)"
  echo "input=$INPUT_FILE"
  echo "pseudo_dir=$PSEUDO_DIR"
  echo "started_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} > "$result_file"

cd "$run_dir"
if [[ "$QE_LAUNCHER" == "none" ]]; then
  cmd=("$QE_COMMAND" "-in" "$input_name")
else
  cmd=("$QE_LAUNCHER" "-n" "${SLURM_NTASKS:-1}" "$QE_COMMAND" "-in" "$input_name")
fi
echo "command=${cmd[*]}" >> "$result_file"

if "${cmd[@]}" > "$run_dir/${stem}.out" 2> "$LOG_DIR/${stem}.log"; then
  rc=0
else
  rc=$?
fi

cp "$run_dir/${stem}.out" "$output_file" 2>/dev/null || true

if grep -q "JOB DONE" "$output_file"; then
  echo "status=completed" >> "$result_file"
else
  echo "status=failed" >> "$result_file"
fi
echo "exit_code=$rc" >> "$result_file"
echo "completed_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$result_file"

exit "$rc"
