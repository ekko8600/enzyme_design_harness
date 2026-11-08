#!/usr/bin/env bash
# selfcheck.sh — Verify the local CP2K CPU and CUDA builds.
#
# Prints versions/features and runs the same tiny H2O DFT single point on both.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "=== [1/3] local CP2K versions + features ==="
for variant in cpu gpu; do
  # shellcheck disable=SC1091
  source "$HERE/cp2k_local_env.sh"
  cp2k_local_activate "$variant"
  echo "--- $variant: $CP2K_LOCAL_BIN"
  "$CP2K_LOCAL_BIN" --version 2>&1 | grep -E 'CP2K version|cp2kflags' | head -2
done

echo "=== [2/3] prepare tiny H2O DFT single-point ==="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/h2o.inp" <<'EOF'
&GLOBAL
  PROJECT h2o_selfcheck
  RUN_TYPE ENERGY
  PRINT_LEVEL LOW
&END GLOBAL
&FORCE_EVAL
  METHOD QS
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME POTENTIAL
    &MGRID
      CUTOFF 280
    &END MGRID
    &QS
      METHOD GPW
    &END QS
    &POISSON
      PERIODIC NONE
      PSOLVER WAVELET
    &END POISSON
    &SCF
      EPS_SCF 1.0E-5
      MAX_SCF 40
      &OT ON
        MINIMIZER DIIS
      &END OT
    &END SCF
    &XC
      &XC_FUNCTIONAL BLYP
      &END XC_FUNCTIONAL
    &END XC
  &END DFT
  &SUBSYS
    &CELL
      ABC 10.0 10.0 10.0
      PERIODIC NONE
    &END CELL
    &COORD
      O   0.000000   0.000000   0.000000
      H   0.000000   0.757000   0.586000
      H   0.000000  -0.757000   0.586000
    &END COORD
    &KIND H
      BASIS_SET DZVP-MOLOPT-GTH
      POTENTIAL GTH-BLYP-q1
    &END KIND
    &KIND O
      BASIS_SET DZVP-MOLOPT-GTH
      POTENTIAL GTH-BLYP-q6
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
EOF

echo "=== [3/3] CPU/GPU calculations ==="
energies=()
for variant in cpu gpu; do
  mkdir "$TMP/$variant"
  cp "$TMP/h2o.inp" "$TMP/$variant/h2o.inp"
  if [ "$variant" = cpu ]; then
    "$HERE/run_cp2k.sh" -i "$TMP/$variant/h2o.inp" -d "$TMP/$variant" -n 1 -t 2
  else
    "$HERE/run_cp2k_gpu.sh" -i "$TMP/$variant/h2o.inp" -d "$TMP/$variant" -g 0 -t 2
    grep -q 'ACC: GPU backend is enabled.*T' "$TMP/$variant/h2o.out" || {
      echo "ERROR: GPU calculation ended without enabled CUDA backend" >&2
      exit 1
    }
  fi
  grep -q 'PROGRAM ENDED AT' "$TMP/$variant/h2o.out"
  energy="$(grep -E 'ENERGY\\| Total FORCE_EVAL' "$TMP/$variant/h2o.out" | tail -1 | awk '{print $NF}')"
  [ -n "$energy" ] || { echo "ERROR: no $variant energy found" >&2; exit 1; }
  energies+=("$energy")
  echo "$variant energy: $energy hartree"
done
python3 - "${energies[@]}" <<'PY'
import sys
a, b = map(float, sys.argv[1:3])
delta = abs(a-b)
print(f"CPU/GPU |delta E| = {delta:.3e} hartree")
if delta > 1e-9:
    raise SystemExit("ERROR: CPU/GPU energies disagree")
PY
echo "OK: local CPU and CUDA CP2K builds passed"
