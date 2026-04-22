#!/usr/bin/env bash
#SBATCH --job-name=qe-manifest
#SBATCH --output=logs/%x-%j.out
#SBATCH --time=02:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=8
#SBATCH --mem=16G

set -euo pipefail

# Queue-inspired QE worker, adapted from the Cluster-FRUC design:
# - create a manifest if none exists,
# - pop inputs under a lock so multiple SLURM jobs can cooperate,
# - skip outputs already containing "JOB DONE",
# - keep logs and scratch isolated to this workspace.

WORKDIR="${QCHEM_WORKDIR:-$PWD}"
INPUT_DIR="${QE_INPUT_DIR:-$WORKDIR/input}"
OUTPUT_DIR="${QE_OUTPUT_DIR:-$WORKDIR/outputs}"
LOG_DIR="${QE_LOG_DIR:-$WORKDIR/logs}"
QUEUE_FILE="${QE_QUEUE_FILE:-$WORKDIR/qe.jobs}"
LOCKDIR="${QE_LOCKDIR:-$WORKDIR/qe.jobs.lock}"
SCRATCH_ROOT="${QCHEM_SCRATCH:-${SLURM_TMPDIR:-$WORKDIR/scratch}}"
INPUT_PATTERN="${QE_INPUT_PATTERN:-*.in}"
QE_MODULES="${QE_MODULES:-}"
QE_COMMAND="${QE_COMMAND:-pw.x}"
QE_MPI_COMMAND="${QE_MPI_COMMAND:-srun}"
STOP_BEFORE_SECONDS="${QCHEM_STOP_BEFORE_SECONDS:-600}"
WALLTIME_SECONDS="${QCHEM_WALLTIME_SECONDS:-0}"
START_TIME="$(date +%s)"

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR" "$LOG_DIR" "$SCRATCH_ROOT"

if [[ -n "$QE_MODULES" ]]; then
  if type module >/dev/null 2>&1; then
    for module_name in $QE_MODULES; do
      module load "$module_name"
    done
  else
    echo "warning: module command not available; QE_MODULES ignored" >&2
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

time_to_stop() {
  if [[ "$WALLTIME_SECONDS" -le 0 ]]; then
    return 1
  fi
  local now elapsed remaining
  now="$(date +%s)"
  elapsed=$((now - START_TIME))
  remaining=$((WALLTIME_SECONDS - elapsed))
  [[ "$remaining" -le "$STOP_BEFORE_SECONDS" ]]
}

run_qe_input() {
  local input_name="$1"
  local stem="${input_name%.in}"
  local output_file="$OUTPUT_DIR/${stem}.out"
  local log_file="$LOG_DIR/${stem}.log"

  if [[ -f "$output_file" ]] && grep -q "JOB DONE" "$output_file"; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) skip completed $input_name" >> "$LOG_DIR/completed.log"
    return 0
  fi

  local run_dir="$SCRATCH_ROOT/qe-${stem}-${SLURM_JOB_ID:-manual}-$$"
  mkdir -p "$run_dir/tmp"
  cp "$INPUT_DIR/$input_name" "$run_dir/$input_name"
  export ESPRESSO_TMPDIR="$run_dir/tmp"

  local cmd=()
  if [[ -n "$QE_MPI_COMMAND" && "$QE_MPI_COMMAND" != "none" ]]; then
    read -r -a mpi_parts <<< "$QE_MPI_COMMAND"
    cmd+=("${mpi_parts[@]}")
  fi
  read -r -a qe_parts <<< "$QE_COMMAND"
  cmd+=("${qe_parts[@]}" "-in" "$run_dir/$input_name")

  {
    echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "host=$(hostname)"
    echo "input=$input_name"
    echo "command=${cmd[*]}"
  } > "$log_file"

  if "${cmd[@]}" > "$run_dir/${stem}.out" 2>> "$log_file"; then
    cp "$run_dir/${stem}.out" "$output_file"
  else
    cp "$run_dir/${stem}.out" "$output_file" 2>/dev/null || true
  fi

  if grep -q "JOB DONE" "$output_file"; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) completed $input_name" >> "$LOG_DIR/completed.log"
    rm -rf "$run_dir"
  else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) failed $input_name" >> "$LOG_DIR/failed.log"
    if [[ -n "${QE_RECOVERY_COMMAND:-}" ]]; then
      "$QE_RECOVERY_COMMAND" "$output_file" "$INPUT_DIR/$input_name" > "$INPUT_DIR/${stem}-retry.in" || true
      echo "${stem}-retry.in" >> "$QUEUE_FILE"
    fi
  fi
}

while true; do
  if time_to_stop; then
    echo "Stopping before walltime buffer." >> "$LOG_DIR/worker.log"
    break
  fi
  next="$(claim_input)"
  if [[ -z "$next" ]]; then
    echo "Queue empty." >> "$LOG_DIR/worker.log"
    break
  fi
  run_qe_input "$next"
done

