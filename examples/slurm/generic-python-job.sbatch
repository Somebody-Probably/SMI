#!/usr/bin/env bash
#SBATCH --job-name=generic-python-job
#SBATCH --output=logs/%x-%j.out
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G

set -euo pipefail

mkdir -p logs outputs

echo "Host: $(hostname)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

python --version
python - <<'PY'
from pathlib import Path
Path("outputs/hello-cluster.txt").write_text("Hello from a generic SLURM job.\n", encoding="utf-8")
print("Wrote outputs/hello-cluster.txt")
PY

echo "Finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

