#!/usr/bin/env bash
#SBATCH --job-name=smi-array-smoke
#SBATCH --time=00:02:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --array=0-3
#SBATCH --output=logs/%x-%A_%a.out
#SBATCH --error=logs/%x-%A_%a.err

set -euo pipefail

mkdir -p results

task_id="${SLURM_ARRAY_TASK_ID:-0}"
result_path="results/task-${task_id}.txt"

{
  echo "SMI array smoke test"
  echo "cluster=${SLURM_CLUSTER_NAME:-unknown}"
  echo "job_id=${SLURM_JOB_ID:-unknown}"
  echo "array_job_id=${SLURM_ARRAY_JOB_ID:-unknown}"
  echo "array_task_id=${task_id}"
  echo "host=$(hostname)"
  echo "user=$(whoami)"
  echo "workdir=$(pwd)"
  echo "started_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} > "${result_path}"

sleep 2

echo "completed_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "${result_path}"
