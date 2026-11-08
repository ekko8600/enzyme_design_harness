#!/usr/bin/env bash
# param_substrate.sh — Parameterize a small-molecule substrate/ligand for Amber (GAFF2 + AM1-BCC).
#
# Produces <name>.mol2 (with charges) + <name>.frcmod, ready for tleap `loadmol2`/`loadamberparams`.
# The substrate is treated as QM in the later QM/MM step; these MM params only serve
# classical solvation/equilibration and (if the ligand is covalent) the boundary.
#
# Usage:
#   param_substrate.sh -i input.{sdf,mol2,pdb} -n NAME -c NET_CHARGE [-m MULT] [-o OUTDIR]
#
# Env: AmberTools commands are taken from the active environment. Set AMBER_SH only
# when an external Amber installation must be sourced first.
set -euo pipefail

AMBER_SH="${AMBER_SH:-}"
# amber.sh references unset vars (PERL5LIB, etc.); relax nounset only for the source.
if [ -n "$AMBER_SH" ] && [ -f "$AMBER_SH" ]; then set +u; source "$AMBER_SH"; set -u; fi
command -v antechamber >/dev/null || { echo "ERROR: antechamber not on PATH (source amber.sh)"; exit 3; }

IN=""; NAME=""; CHG=""; MULT=1; OUT="."
while getopts "i:n:c:m:o:h" opt; do
  case "$opt" in
    i) IN="$OPTARG" ;; n) NAME="$OPTARG" ;; c) CHG="$OPTARG" ;;
    m) MULT="$OPTARG" ;; o) OUT="$OPTARG" ;;
    h) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) exit 2 ;;
  esac
done
[ -n "$IN" ] && [ -n "$NAME" ] && [ -n "$CHG" ] || { echo "ERROR: -i, -n, -c required"; exit 2; }
mkdir -p "$OUT"; IN="$(readlink -f "$IN")"; OUT="$(readlink -f "$OUT")"

ext="${IN##*.}"
echo ">> [1] add H at pH 7 (obabel). Keep existing 3D; only --gen3d if input is 2D/0D."
# NOTE: never --gen3d when the input already carries a sane 3D conformer (e.g. PubChem
# record_type=3d) — it discards pose/stereochemistry. Set GEN3D=1 to force generation.
if [ "${GEN3D:-0}" = "1" ]; then
  obabel "$IN" -O "$OUT/${NAME}_h.mol2" -p 7.0 --gen3d 2>/dev/null
else
  obabel "$IN" -O "$OUT/${NAME}_h.mol2" -p 7.0 2>/dev/null
fi

echo ">> [2] antechamber: GAFF2 atom types + AM1-BCC charges (net charge $CHG)"
cd "$OUT"
antechamber -i "${NAME}_h.mol2" -fi mol2 -o "${NAME}.mol2" -fo mol2 \
  -c bcc -nc "$CHG" -m "$MULT" -at gaff2 -rn "${NAME:0:3}" -pf y

echo ">> [3] parmchk2: fill missing GAFF2 params -> frcmod"
parmchk2 -i "${NAME}.mol2" -f mol2 -o "${NAME}.frcmod" -s gaff2

echo ">> done:"
ls -la "$OUT/${NAME}.mol2" "$OUT/${NAME}.frcmod"
echo ">> assigned net charge (sum of AM1-BCC):"
awk '/@<TRIPOS>ATOM/{f=1;next}/@<TRIPOS>BOND/{f=0}f{s+=$9}END{printf "  %.4f\n", s}' "$OUT/${NAME}.mol2"
