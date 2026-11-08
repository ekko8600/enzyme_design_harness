#!/usr/bin/env bash
# cluster_ensemble.sh — Cluster an MD trajectory into N representative conformations,
# and (for the reactant state) filter the Near-Attack-Conformation (NAC) sub-population.
#
# Produces, per state: <TAG>_cN.pdb (N cluster representatives) + cluster populations,
# so a protein generative model can be conditioned on the conformational spread of each
# catalytic state. For the reactant, also reports the NAC fraction and NAC-subset centroids.
#
# Usage:
#   cluster_ensemble.sh -p prmtop -x prod.dcd -t TAG [-n 20] [-m ':1-559@CA'] \
#        [--nac 'Ow_idx C6_idx O2_idx dcut acut']   # NAC only for reactant
set -u
AMBER_SH="${AMBER_SH:-}"
[ -n "$AMBER_SH" ] && [ -f "$AMBER_SH" ] && { set +u; source "$AMBER_SH"; set -u; }
# Some local AmberTools/cpptraj builds link against libkmmd.so from a separate
# pmemd installation.  Add the discovered workstation library directory when
# it exists; keep AMBER_KMMD_LIB overridable for other machines.
AMBER_KMMD_LIB="${AMBER_KMMD_LIB:-${ENZYME_DATA_ROOT}/Amber_MD_run/pmemd26_install/lib}"
if [ -f "$AMBER_KMMD_LIB/libkmmd.so" ]; then
  export LD_LIBRARY_PATH="$AMBER_KMMD_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
PRM=""; TRAJ=""; TAG="state"; N=20; MASK=":1-559@CA"; NAC=""
while [ $# -gt 0 ]; do case "$1" in
  -p) PRM="$2"; shift 2;; -x) TRAJ="$2"; shift 2;; -t) TAG="$2"; shift 2;;
  -n) N="$2"; shift 2;; -m) MASK="$2"; shift 2;; --nac) NAC="$2"; shift 2;; *) shift;; esac; done
[ -n "$PRM" ] && [ -n "$TRAJ" ] || { echo "need -p prmtop -x traj"; exit 2; }

# 1. cluster (hierarchical agglomerative on backbone/active-site RMSD after global align)
cpptraj -p "$PRM" >"${TAG}_cluster.log" 2>&1 <<EOF
trajin $TRAJ
autoimage
rms first :1-559@CA
cluster C1 hieragglo clusters $N rms "$MASK" \
  out ${TAG}_cnum.dat summary ${TAG}_clusters.summary \
  repout ${TAG}_rep repfmt pdb
run
EOF
echo ">> [$TAG] $N cluster representatives -> ${TAG}_rep.cX.pdb ; populations in ${TAG}_clusters.summary"
head -25 "${TAG}_clusters.summary" 2>/dev/null

# 2. NAC filter (reactant only): --nac "Ow C6 O2 dcut acut" with 1-based atom indices
if [ -n "$NAC" ]; then
  set -- $NAC; OW=$1; C6=$2; O2=$3; DCUT=${4:-3.2}; ACUT=${5:-150}
  cpptraj -p "$PRM" >"${TAG}_nac.log" 2>&1 <<EOF
trajin $TRAJ
autoimage
distance dOwC6 @${OW} @${C6} out ${TAG}_nac_geom.dat
angle aOwC6O2 @${OW} @${C6} @${O2} out ${TAG}_nac_geom.dat
run
EOF
  # NAC population = frames with attack dist < DCUT AND in-line angle > ACUT
  python3 - "$TAG" "$DCUT" "$ACUT" <<'PY'
import sys
tag,dcut,acut=sys.argv[1],float(sys.argv[2]),float(sys.argv[3])
rows=[l.split() for l in open(f"{tag}_nac_geom.dat") if l.strip() and not l.startswith('#')]
d=[(float(r[1]),float(r[2])) for r in rows if len(r)>=3]
nac=[1 for dist,ang in d if dist<dcut and ang>acut]
print(f">> [{tag}] NAC 判据 d(Ow-C6)<{dcut}Å 且 角>{acut}°: {sum(nac)}/{len(d)} 帧 = {100*sum(nac)/max(len(d),1):.1f}% NAC 布居")
PY
fi
