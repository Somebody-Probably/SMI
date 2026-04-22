#!/usr/bin/env bash
#SBATCH --job-name=siesta-smoke
#SBATCH --time=00:15:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail

WORKDIR="${CHEM_WORKDIR:-$PWD}"
INPUT_FILE="${SIESTA_INPUT_FILE:-$WORKDIR/input/water.fdf}"
PSEUDO_DIR="${SIESTA_PSEUDO_DIR:-$WORKDIR/pseudo}"
OUTPUT_DIR="${SIESTA_OUTPUT_DIR:-$WORKDIR/outputs}"
LOG_DIR="${SIESTA_LOG_DIR:-$WORKDIR/logs}"
RESULT_DIR="${SIESTA_RESULT_DIR:-$WORKDIR/results}"
SCRATCH_ROOT="${CHEM_SCRATCH:-${SLURM_TMPDIR:-$WORKDIR/scratch}}"
SIESTA_MODULES="${SIESTA_MODULES:-}"
SIESTA_COMMAND="${SIESTA_COMMAND:-siesta}"
SIESTA_LAUNCHER="${SIESTA_LAUNCHER:-none}"

mkdir -p "$OUTPUT_DIR" "$LOG_DIR" "$RESULT_DIR" "$SCRATCH_ROOT"

if [[ -n "$SIESTA_MODULES" ]]; then
  if type module >/dev/null 2>&1; then
    module_list="${SIESTA_MODULES//,/ }"
    module_list="${module_list//:/ }"
    for module_name in $module_list; do
      module load "$module_name"
    done
  else
    echo "warning: module command not available; SIESTA_MODULES ignored" >&2
  fi
fi

if ! command -v "$SIESTA_COMMAND" >/dev/null 2>&1; then
  echo "error=SIESTA command not found: $SIESTA_COMMAND" >&2
  exit 127
fi

input_name="$(basename "$INPUT_FILE")"
stem="${input_name%.fdf}"
run_dir="$SCRATCH_ROOT/siesta-${SLURM_JOB_ID:-manual}-$$"
output_file="$OUTPUT_DIR/${stem}.out"
result_file="$RESULT_DIR/siesta-${SLURM_JOB_ID:-manual}.txt"
mkdir -p "$run_dir/pseudo"
cp "$INPUT_FILE" "$run_dir/$input_name"
cp "$PSEUDO_DIR"/* "$run_dir/pseudo/"
export SIESTA_PS_PATH="$run_dir/pseudo"

{
  echo "SMI SIESTA smoke test"
  echo "cluster=${SLURM_CLUSTER_NAME:-unknown}"
  echo "job_id=${SLURM_JOB_ID:-unknown}"
  echo "host=$(hostname)"
  echo "input=$INPUT_FILE"
  echo "pseudo_dir=$PSEUDO_DIR"
  echo "started_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} > "$result_file"

cd "$run_dir"
if [[ "$SIESTA_LAUNCHER" == "none" ]]; then
  cmd=("$SIESTA_COMMAND")
else
  cmd=("$SIESTA_LAUNCHER" "-n" "${SLURM_NTASKS:-1}" "$SIESTA_COMMAND")
fi
echo "command=${cmd[*]} < $input_name" >> "$result_file"

if "${cmd[@]}" < "$input_name" > "$stem.out" 2> "$LOG_DIR/${stem}.log"; then
  rc=0
else
  rc=$?
fi

cp "$run_dir/$stem.out" "$output_file" 2>/dev/null || true
cp "$run_dir"/*.{DM,ion,xml,STRUCT_OUT} "$OUTPUT_DIR"/ 2>/dev/null || true

if grep -qi "Job completed" "$output_file"; then
  echo "status=completed" >> "$result_file"
else
  echo "status=failed" >> "$result_file"
fi
echo "exit_code=$rc" >> "$result_file"
echo "completed_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$result_file"

exit "$rc"
