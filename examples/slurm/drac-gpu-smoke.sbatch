#!/usr/bin/env bash
#SBATCH --job-name=smi-gpu-smoke
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail

mkdir -p results

result_path="results/gpu-${SLURM_JOB_ID:-manual}.txt"

{
  echo "SMI GPU partition smoke test"
  echo "cluster=${SLURM_CLUSTER_NAME:-unknown}"
  echo "job_id=${SLURM_JOB_ID:-unknown}"
  echo "partition=${SLURM_JOB_PARTITION:-unknown}"
  echo "job_gpus=${SLURM_JOB_GPUS:-unknown}"
  echo "cuda_visible_devices=${CUDA_VISIBLE_DEVICES:-unset}"
  echo "rocr_visible_devices=${ROCR_VISIBLE_DEVICES:-unset}"
  echo "gpu_device_ordinal=${GPU_DEVICE_ORDINAL:-unset}"
  echo "host=$(hostname)"
  echo "user=$(whoami)"
  echo "workdir=$(pwd)"
  echo "started_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} > "${result_path}"

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  {
    echo "--- nvidia-smi ---"
    nvidia-smi -L
    nvidia-smi --query-gpu=name,uuid,driver_version,memory.total --format=csv,noheader
  } >> "${result_path}"
elif command -v rocm-smi >/dev/null 2>&1; then
  {
    echo "--- rocm-smi ---"
    rocm-smi --showproductname --showdriverversion --showmeminfo vram
  } >> "${result_path}"
else
  echo "error=no GPU diagnostic command found" >> "${result_path}"
  exit 3
fi

echo "completed_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "${result_path}"
