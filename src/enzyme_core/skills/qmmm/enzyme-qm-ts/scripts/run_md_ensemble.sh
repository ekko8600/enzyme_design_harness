#!/usr/bin/env bash
# run_md_ensemble.sh — OpenMM classical MD compatibility launcher.
#
# Retains Amber prmtop/coordinates but runs min -> heat -> equil -> production
# with OpenMM from conda agent. AmberTools remains the topology preparation layer.
#
# Usage:
#   run_md_ensemble.sh -p prmtop -c start.{pdb,rst7} -t TAG -g GPU [-m '1,4-12'] [-d OUTDIR]
#   -m is an explicit 1-based atom index list/ranges, not an Amber mask.
# Env: OPENMM_CONDA_ENV=agent; additional Python-runner options may follow --.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PRM=""; START=""; TAG="md"; GPU="0"; INDICES=""; OUTDIR="."
while getopts "p:c:t:g:m:d:h" o; do case "$o" in
  p) PRM="$OPTARG";; c) START="$OPTARG";; t) TAG="$OPTARG";; g) GPU="$OPTARG";;
  m) INDICES="$OPTARG";; d) OUTDIR="$OPTARG";; h) grep '^#' "$0"|sed 's/^# \{0,1\}//'; exit 0;; esac
done
[ -n "$PRM" ] && [ -f "$PRM" ] && [ -n "$START" ] && [ -f "$START" ] || {
  echo "ERROR: need existing -p prmtop and -c coordinates" >&2
  exit 2
}
shift $((OPTIND - 1))
CONDA_SH="${PROJECT_CONDA_SH:-${ENZYME_CONDA_ROOT}/etc/profile.d/conda.sh}"
[ -f "$CONDA_SH" ] || { echo "ERROR: conda activation script not found: $CONDA_SH" >&2; exit 3; }
cmd=(python
  "$HERE/run_openmm_ensemble.py" -p "$PRM" -c "$START" -t "$TAG" -o "$OUTDIR"
  -g "$GPU")
[ -n "$INDICES" ] && cmd+=(--restraint-indices "$INDICES")
cmd+=("$@")
echo ">> [$TAG] engine=OpenMM conda=${OPENMM_CONDA_ENV:-agent} GPU=$GPU restraints=${INDICES:-none}"
bash -c 'source "$1"; conda activate "$2"; shift 2; exec "$@"' \
  _ "$CONDA_SH" "${OPENMM_CONDA_ENV:-agent}" "${cmd[@]}"
