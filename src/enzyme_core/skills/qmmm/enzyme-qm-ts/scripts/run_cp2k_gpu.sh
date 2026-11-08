#!/usr/bin/env bash
# run_cp2k_gpu.sh — Local CUDA CP2K launcher for the enzyme-qm-ts skill.
#
# WHEN GPU HELPS: large QM regions (roughly >=150-200 QM atoms), hybrid functionals,
# or many-atom cluster/QM models. For a small QM/MM region (tens of QM atoms) GPU
# offload gives little/no speedup — use the CPU run_cp2k.sh instead.
#
# Usage:
#   run_cp2k_gpu.sh -i INPUT.inp [-o OUT.out] [-d WORKDIR] [-g "0"] [-t OMP_THREADS]
#   -g  comma-separated GPU ids to use (default "0"); RANKS auto = number of ids.
#
# Env overrides: CP2K_GPU_ROOT, CP2K_GPU_SETUP, MPIEXEC.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/cp2k_local_env.sh"

INPUT=""; OUTPUT=""; WORKDIR=""; GPUS="0"; THREADS=8
while getopts "i:o:d:g:t:h" opt; do
  case "$opt" in
    i) INPUT="$OPTARG" ;; o) OUTPUT="$OPTARG" ;; d) WORKDIR="$OPTARG" ;;
    g) GPUS="$OPTARG" ;; t) THREADS="$OPTARG" ;;
    h) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) exit 2 ;;
  esac
done
[ -n "$INPUT" ] && [ -f "$INPUT" ] || { echo "ERROR: -i INPUT.inp required and must exist" >&2; exit 2; }
WORKDIR="${WORKDIR:-$(cd "$(dirname "$INPUT")" && pwd)}"
IN="$(basename "$INPUT")"; OUTPUT="${OUTPUT:-${IN%.inp}.out}"
RANKS=$(( $(echo "$GPUS" | tr ',' '\n' | grep -c .) ))   # one rank per GPU

# An explicit GPU request must never silently become anything else.  Check the
# accelerator before the toolchain takes over, and point at the auto entry point
# that degrades to CPU on purpose.
AVAILABILITY="$(cp2k_gpu_availability)"
[ "$AVAILABILITY" = "cuda_available" ] || {
  echo "ERROR: GPU CP2K requested but CUDA acceleration is unavailable: $AVAILABILITY" >&2
  echo "       use scripts/run_cp2k_auto.sh (preferred default) or scripts/run_cp2k.sh for the CPU build" >&2
  exit 4
}

cp2k_local_activate gpu
export CUDA_VISIBLE_DEVICES="$GPUS"
export OMP_NUM_THREADS="$THREADS"
echo ">> CP2K local GPU binary=$CP2K_LOCAL_BIN gpus=[$GPUS] ranks=$RANKS threads=$THREADS"
echo ">> NOTE: GPU offload pays off mainly for large QM regions; for small QM/MM prefer run_cp2k.sh"

cd "$WORKDIR"
if [ "$RANKS" -eq 1 ]; then
  "$CP2K_LOCAL_BIN" -i "$IN" -o "$OUTPUT"
else
  "${MPIEXEC:-mpiexec}" -np "$RANKS" "$CP2K_LOCAL_BIN" -i "$IN" -o "$OUTPUT"
fi

echo ">> done. output: $WORKDIR/$OUTPUT"
