#!/usr/bin/env bash
# run_cp2k.sh — Local CPU CP2K launcher for the enzyme-qm-ts skill.
#
# Usage:
#   run_cp2k.sh -i INPUT.inp [-o OUTPUT.out] [-d WORKDIR] [-n MPI_RANKS] [-t OMP_THREADS]
#
# Defaults: OUTPUT = <input>.out, WORKDIR = dirname(INPUT), ranks/threads auto-sized.
# Env overrides: CP2K_CPU_ROOT, CP2K_CPU_SETUP, MPIEXEC.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/cp2k_local_env.sh"

INPUT=""; OUTPUT=""; WORKDIR=""; RANKS=""; THREADS=""
while getopts "i:o:d:n:t:h" opt; do
  case "$opt" in
    i) INPUT="$OPTARG" ;;
    o) OUTPUT="$OPTARG" ;;
    d) WORKDIR="$OPTARG" ;;
    n) RANKS="$OPTARG" ;;
    t) THREADS="$OPTARG" ;;
    h) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "bad option" >&2; exit 2 ;;
  esac
done

[ -n "$INPUT" ] || { echo "ERROR: -i INPUT.inp required" >&2; exit 2; }
[ -f "$INPUT" ] || { echo "ERROR: input not found: $INPUT" >&2; exit 2; }

WORKDIR="${WORKDIR:-$(cd "$(dirname "$INPUT")" && pwd)}"
INPUT_BASE="$(basename "$INPUT")"
OUTPUT="${OUTPUT:-${INPUT_BASE%.inp}.out}"

# Auto-size parallelism: default to a conservative split leaving headroom.
CORES="$(nproc)"
if [ -z "$RANKS" ] && [ -z "$THREADS" ]; then
  THREADS=2
  RANKS=$(( CORES / THREADS / 4 ))          # quarter of node by default; override with -n
  [ "$RANKS" -lt 1 ] && RANKS=1
fi
RANKS="${RANKS:-1}"; THREADS="${THREADS:-1}"

cp2k_local_activate cpu
export OMP_NUM_THREADS="$THREADS"
echo ">> CP2K local CPU binary=$CP2K_LOCAL_BIN ranks=$RANKS threads=$THREADS workdir=$WORKDIR"
echo ">> input=$INPUT_BASE output=$OUTPUT"
# GPU reminder, emitted only when CUDA acceleration is actually usable.  For
# LARGE QM regions (~>=150-200 QM atoms), hybrid functionals or big cluster/QM
# models the GPU launcher gives a real speedup; for small QM/MM regions this CPU
# path is fine.
AVAILABILITY="$(cp2k_gpu_availability)"
if [ "$AVAILABILITY" = "cuda_available" ]; then
  echo ">> [hint] CUDA acceleration available — for large QM regions use scripts/run_cp2k_auto.sh (default) or scripts/run_cp2k_gpu.sh"
else
  echo ">> [hint] CUDA acceleration unavailable ($AVAILABILITY) — CPU build is the only option here"
fi

cd "$WORKDIR"
if [ "$RANKS" -eq 1 ]; then
  "$CP2K_LOCAL_BIN" -i "$INPUT_BASE" -o "$OUTPUT"
else
  "${MPIEXEC:-mpiexec}" -np "$RANKS" "$CP2K_LOCAL_BIN" -i "$INPUT_BASE" -o "$OUTPUT"
fi

echo ">> done. output at: $WORKDIR/$OUTPUT"
