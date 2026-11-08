#!/usr/bin/env bash
# run_cp2k_auto.sh — default CP2K entry point for the enzyme-qm-ts skill.
#
# The GPU build is preferred.  When CUDA acceleration is unavailable the run
# degrades to the CPU build instead of failing, and records which variant ran
# together with the reason, so the choice is auditable rather than silent.
#
# Usage:
#   run_cp2k_auto.sh -i INPUT.inp [-o OUT.out] [-d WORKDIR] [-g "0"] [-t OMP_THREADS]
#   run_cp2k_auto.sh --dry-run            # report the resolved variant and exit
#
# Env overrides: CP2K_VARIANT=auto|gpu|cpu (default auto), plus every override
# honoured by run_cp2k.sh / run_cp2k_gpu.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/cp2k_local_env.sh"

DRY_RUN=0
ARGS=()
for argument in "$@"; do
  if [ "$argument" = "--dry-run" ]; then DRY_RUN=1; else ARGS+=("$argument"); fi
done

REQUESTED="${CP2K_VARIANT:-auto}"
PROBE="$(cp2k_gpu_availability)"
VARIANT="$(cp2k_select_variant)"
if [ "$REQUESTED" = "auto" ]; then
  REASON="$PROBE"
else
  # An explicit selection is never silently reinterpreted: say it was forced,
  # and refuse outright when the forced GPU variant cannot actually run.
  REASON="forced_by_CP2K_VARIANT"
  if [ "$VARIANT" = "gpu" ] && [ "$PROBE" != "cuda_available" ]; then
    echo ">> CP2K requested=$REQUESTED variant=$VARIANT reason=forced_by_CP2K_VARIANT:cuda_unavailable:$PROBE"
    echo "ERROR: CP2K_VARIANT=gpu requested but CUDA acceleration is unavailable: $PROBE" >&2
    echo "       unset CP2K_VARIANT for GPU-preferred automatic fallback, or set CP2K_VARIANT=cpu" >&2
    exit 4
  fi
fi
echo ">> CP2K requested=$REQUESTED variant=$VARIANT reason=$REASON"

if [ "$DRY_RUN" = "1" ]; then
  exit 0
fi

case "$VARIANT" in
  gpu) exec "$HERE/run_cp2k_gpu.sh" ${ARGS[@]+"${ARGS[@]}"} ;;
  cpu) exec "$HERE/run_cp2k.sh" ${ARGS[@]+"${ARGS[@]}"} ;;
  *) echo "ERROR: unresolved CP2K variant: $VARIANT" >&2; exit 2 ;;
esac
