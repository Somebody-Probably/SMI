#!/usr/bin/env bash
#SBATCH --job-name=orca-manifest
#SBATCH --output=logs/%x-%j.out
#SBATCH --time=02:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=8
#SBATCH --mem=16G

set -euo pipefail

WORKDIR="${QCHEM_WORKDIR:-$PWD}"
INPUT_DIR="${ORCA_INPUT_DIR:-$WORKDIR/input}"
OUTPUT_DIR="${ORCA_OUTPUT_DIR:-$WORKDIR/outputs}"
LOG_DIR="${ORCA_LOG_DIR:-$WORKDIR/logs}"
QUEUE_FILE="${ORCA_QUEUE_FILE:-$WORKDIR/orca.jobs}"
LOCKDIR="${ORCA_LOCKDIR:-$WORKDIR/orca.jobs.lock}"
SCRATCH_ROOT="${QCHEM_SCRATCH:-${SLURM_TMPDIR:-$WORKDIR/scratch}}"
INPUT_PATTERN="${ORCA_INPUT_PATTERN:-*.inp}"
ORCA_MODULES="${ORCA_MODULES:-}"
ORCA_COMMAND="${ORCA_COMMAND:-orca}"

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR" "$LOG_DIR" "$SCRATCH_ROOT"

if [[ -n "$ORCA_MODULES" ]]; then
  if type module >/dev/null 2>&1; then
    for module_name in $ORCA_MODULES; do
      module load "$module_name"
    done
  else
    echo "warning: module command not available; ORCA_MODULES ignored" >&2
  fi
fi

if [[ ! -f "$QUEUE_FILE" ]]; then
  find "$INPUT_DIR" -maxdepth 1 -type f -name "$INPUT_PATTERN" -exec basename {} \; | sort > "$QUEUE_FILE"
fi

claim_input() {
  local next_input=""
  while ! mkdir "$LOCKDIR" 2>/dev/null; do
    sleep 1
  done
  if [[ -s "$QUEUE_FILE" ]]; then
    next_input="$(head -n 1 "$QUEUE_FILE")"
    tail -n +2 "$QUEUE_FILE" > "$QUEUE_FILE.tmp.$$"
    mv "$QUEUE_FILE.tmp.$$" "$QUEUE_FILE"
  fi
  rmdir "$LOCKDIR"
  printf '%s' "$next_input"
}

run_orca_input() {
  local input_name="$1"
  local stem="${input_name%.inp}"
  local output_file="$OUTPUT_DIR/${stem}.out"
  local log_file="$LOG_DIR/${stem}.log"

  if [[ -f "$output_file" ]] && grep -q "ORCA TERMINATED NORMALLY" "$output_file"; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) skip completed $input_name" >> "$LOG_DIR/completed.log"
    return 0
  fi

  local run_dir="$SCRATCH_ROOT/orca-${stem}-${SLURM_JOB_ID:-manual}-$$"
  mkdir -p "$run_dir"
  cp "$INPUT_DIR/$input_name" "$run_dir/$input_name"
  cp "$INPUT_DIR"/* "$run_dir"/ 2>/dev/null || true

  {
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "host=$(hostname)"
    echo "input=$input_name"
    echo "command=$ORCA_COMMAND $run_dir/$input_name"
  } > "$log_file"

  if "$ORCA_COMMAND" "$run_dir/$input_name" > "$run_dir/${stem}.out" 2>> "$log_file"; then
    cp "$run_dir/${stem}.out" "$output_file"
  else
    cp "$run_dir/${stem}.out" "$output_file" 2>/dev/null || true
  fi
  cp "$run_dir"/*.{gbw,xyz,trj,engrad,hess} "$OUTPUT_DIR"/ 2>/dev/null || true

  if grep -q "ORCA TERMINATED NORMALLY" "$output_file"; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) completed $input_name" >> "$LOG_DIR/completed.log"
    rm -rf "$run_dir"
  else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) failed $input_name" >> "$LOG_DIR/failed.log"
  fi
}

while true; do
  next="$(claim_input)"
  if [[ -z "$next" ]]; then
    echo "Queue empty." >> "$LOG_DIR/worker.log"
    break
  fi
  run_orca_input "$next"
done

