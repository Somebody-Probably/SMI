#!/usr/bin/env bash
#SBATCH --job-name=smi-cpu-smoke
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail

mkdir -p results

result_path="results/cpu-${SLURM_JOB_ID:-manual}.txt"

{
  echo "SMI CPU partition smoke test"
  echo "cluster=${SLURM_CLUSTER_NAME:-unknown}"
  echo "job_id=${SLURM_JOB_ID:-unknown}"
  echo "partition=${SLURM_JOB_PARTITION:-unknown}"
  echo "cpus_on_node=${SLURM_CPUS_ON_NODE:-unknown}"
  echo "job_cpus_per_node=${SLURM_JOB_CPUS_PER_NODE:-unknown}"
  echo "host=$(hostname)"
  echo "user=$(whoami)"
  echo "workdir=$(pwd)"
  echo "started_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} > "${result_path}"

sleep 2

echo "completed_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "${result_path}"
