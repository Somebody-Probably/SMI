#!/usr/bin/env bash
#SBATCH --job-name=orca-smoke
#SBATCH --time=00:15:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail

WORKDIR="${CHEM_WORKDIR:-$PWD}"
INPUT_FILE="${ORCA_INPUT_FILE:-$WORKDIR/input/water-smoke.inp}"
OUTPUT_DIR="${ORCA_OUTPUT_DIR:-$WORKDIR/outputs}"
LOG_DIR="${ORCA_LOG_DIR:-$WORKDIR/logs}"
RESULT_DIR="${ORCA_RESULT_DIR:-$WORKDIR/results}"
SCRATCH_ROOT="${CHEM_SCRATCH:-${SLURM_TMPDIR:-$WORKDIR/scratch}}"
ORCA_MODULES="${ORCA_MODULES:-}"

mkdir -p "$OUTPUT_DIR" "$LOG_DIR" "$RESULT_DIR" "$SCRATCH_ROOT"

if [[ -n "$ORCA_MODULES" ]]; then
  if type module >/dev/null 2>&1; then
    module_list="${ORCA_MODULES//,/ }"
    module_list="${module_list//:/ }"
    for module_name in $module_list; do
      module load "$module_name"
    done
  else
    echo "warning: module command not available; ORCA_MODULES ignored" >&2
  fi
fi

ORCA_COMMAND="${ORCA_COMMAND:-}"
if [[ -z "$ORCA_COMMAND" ]]; then
  if [[ -n "${EBROOTORCA:-}" && -x "${EBROOTORCA}/orca" ]]; then
    ORCA_COMMAND="${EBROOTORCA}/orca"
  else
    ORCA_COMMAND="orca"
  fi
fi

if ! command -v "$ORCA_COMMAND" >/dev/null 2>&1 && [[ ! -x "$ORCA_COMMAND" ]]; then
  echo "error=ORCA command not found: $ORCA_COMMAND" >&2
  exit 127
fi

input_name="$(basename "$INPUT_FILE")"
stem="${input_name%.inp}"
run_dir="$SCRATCH_ROOT/orca-${SLURM_JOB_ID:-manual}-$$"
output_file="$OUTPUT_DIR/${stem}.out"
result_file="$RESULT_DIR/orca-${SLURM_JOB_ID:-manual}.txt"
mkdir -p "$run_dir"
cp "$INPUT_FILE" "$run_dir/$input_name"

{
  echo "SMI ORCA smoke test"
  echo "cluster=${SLURM_CLUSTER_NAME:-unknown}"
  echo "job_id=${SLURM_JOB_ID:-unknown}"
  echo "host=$(hostname)"
  echo "input=$INPUT_FILE"
  echo "command=$ORCA_COMMAND $run_dir/$input_name"
  echo "started_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} > "$result_file"

if "$ORCA_COMMAND" "$run_dir/$input_name" > "$run_dir/${stem}.out" 2> "$LOG_DIR/${stem}.log"; then
  rc=0
else
  rc=$?
fi

cp "$run_dir/${stem}.out" "$output_file" 2>/dev/null || true
cp "$run_dir"/*.{gbw,xyz,trj,engrad,hess} "$OUTPUT_DIR"/ 2>/dev/null || true

if grep -q "ORCA TERMINATED NORMALLY" "$output_file"; then
  echo "status=completed" >> "$result_file"
else
  echo "status=failed" >> "$result_file"
fi
echo "exit_code=$rc" >> "$result_file"
echo "completed_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$result_file"

exit "$rc"
