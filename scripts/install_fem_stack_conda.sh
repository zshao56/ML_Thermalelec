#!/usr/bin/env bash
# Install an open-source FEM stack into the current conda environment.
#
# Intended use on the Linux server:
#   conda activate teml
#   bash scripts/install_fem_stack_conda.sh

set -euo pipefail

if ! command -v conda >/dev/null 2>&1; then
  echo "conda was not found in PATH."
  echo "Activate Miniconda/Anaconda first, then rerun this script."
  exit 1
fi

if [[ -z "${CONDA_DEFAULT_ENV:-}" ]]; then
  echo "No active conda environment was detected."
  echo "Run: conda activate teml"
  echo "Then rerun this script."
  exit 1
fi

echo "Active conda environment: ${CONDA_DEFAULT_ENV}"
echo "Installing FEM packages from conda-forge..."

conda install -y -c conda-forge \
  numpy \
  scipy \
  meshio \
  gmsh \
  sfepy

echo
echo "Rechecking FEM environment..."
python3 scripts/check_fem_environment.py
