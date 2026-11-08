#!/usr/bin/env bash
# run_scan.sh — Relaxed reaction-coordinate scan (QM/MM GEO_OPT per window) via local CP2K.
#
# Drives a constrained COLVAR distance across windows. The MM environment is frozen
# (only the QM region relaxes), and each window's geometry is chained into the next
# via CP2K EXT_RESTART (topology re-read from the clean initial PDB).
# Writes scan_energies.dat: "window target_A total_energy_hartree converged=<0|1>".
#
# Meant to be launched detached:
#   setsid nohup bash run_scan.sh -d DIR -c init.pdb -m tmpl -t "3.0 ... 1.54" </dev/null >scan.log 2>&1 &
set -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RUNDIR="."; INIT="cm_min.pdb"; RANKS=16; THREADS=8; GPU="0"; TMPL="qmmm_scan.inp.tmpl"
TARGETS="3.00 2.80 2.60 2.40 2.30 2.20 2.10 2.00 1.90 1.80 1.70 1.60 1.54"
START_K=0; INITIAL_RESTART=""   # resume: -s START_K -R prev.restart (chain first window from it)
while getopts "d:c:t:n:T:g:m:s:R:h" opt; do
  case "$opt" in
    d) RUNDIR="$OPTARG" ;; c) INIT="$OPTARG" ;; t) TARGETS="$OPTARG" ;;
    n) RANKS="$OPTARG" ;; T) THREADS="$OPTARG" ;; g) GPU="$OPTARG" ;; m) TMPL="$OPTARG" ;;
    s) START_K="$OPTARG" ;; R) INITIAL_RESTART="$OPTARG" ;;
    h) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  esac
done
cd "$RUNDIR" || { echo "cannot cd $RUNDIR"; exit 2; }
[ -f "$TMPL" ] || { echo "template $TMPL not found"; exit 2; }

runcp2k() {  # $1 = input basename (no ext)
  "$HERE/run_cp2k_gpu.sh" -i "$PWD/$1.inp" -o "$1.out" -d "$PWD" -g "$GPU" -t "$THREADS"
}

# fresh run truncates the log; resume (START_K>0) appends
[ "$START_K" -eq 0 ] && : > scan_energies.dat
k="$START_K"; prev=""
for tgt in $TARGETS; do
  proj="cm_scan_$(printf '%02d' "$k")"
  # topology always from clean PDB; geometry chained via EXT_RESTART for k>0
  # (resume: the first window chains from INITIAL_RESTART instead of a prev window)
  restart_src=""
  if [ -n "$prev" ]; then restart_src="${prev}-1.restart"
  elif [ "$k" -gt 0 ] && [ -n "$INITIAL_RESTART" ]; then restart_src="$INITIAL_RESTART"; fi
  if [ -z "$restart_src" ]; then
    : > .ext_block
  else
    {
      echo "&EXT_RESTART"
      echo "  RESTART_FILE_NAME ${restart_src}"
      echo "  RESTART_POS .TRUE."
      echo "  RESTART_COUNTERS .FALSE."
      echo "  RESTART_CONSTRAINT .FALSE."
      echo "&END EXT_RESTART"
    } > .ext_block
    wfn_src="${prev:-cm_scan_$(printf '%02d' $((k-1)))}-RESTART.wfn"
    [ -f "$wfn_src" ] && cp "$wfn_src" "${proj}-RESTART.wfn"
  fi
  # fill scalar placeholders, then splice EXT_RESTART block at its marker line
  sed -e "s/__PROJECT__/$proj/" -e "s/__TGT__/$tgt/" \
      -e "s|__COORD__|$INIT|" -e "s/__COORDFMT__/PDB/" "$TMPL" \
    | awk '/__EXT_RESTART__/{while((getline l<".ext_block")>0)print l; close(".ext_block"); next}{print}' \
    > "$proj.inp"
  echo ">> [$(date +%H:%M:%S)] window $k target=$tgt A -> $proj"
  if runcp2k "$proj"; then
    e=$(grep -E "ENERGY\| Total FORCE_EVAL" "$proj.out" | tail -1 | awk '{print $NF}')
    # LBFGS prints "run CONVERGED!"; BFGS prints "GEOMETRY OPTIMIZATION COMPLETED".
    conv=$(grep -cE "run CONVERGED|GEOMETRY OPTIMIZATION COMPLETED" "$proj.out")
    # actual CV (forming C9-C2 distance) from the window's restart geometry
    # Actual reaction coordinate for the profile x-axis. Configure the atoms via
    # RC_ATOMS="brk_i,brk_j,frm_i,frm_j" (1-based prmtop indices): RC = d(brk) - d(frm).
    # If unset, RC logging is skipped (energies are still recorded correctly).
    cv=$(RC_ATOMS="${RC_ATOMS:-}" python3 - "${proj}-1.restart" 2>/dev/null <<'PY'
import sys,os,re,math
rc=os.environ.get('RC_ATOMS','').strip()
if not rc: print("NA"); sys.exit()
try:
    a=[int(x)-1 for x in rc.split(',')]  # brk_i,brk_j,frm_i,frm_j -> 0-based
    t=open(sys.argv[1],encoding='latin-1').read(); m=re.search(r'&COORD(.*?)&END COORD',t,re.S)
    rows=[l.split() for l in m.group(1).strip().splitlines() if len(l.split())>=4]
    g=lambda i:[float(x) for x in rows[i][1:4]]
    dbrk=math.dist(g(a[0]),g(a[1])); dfrm=math.dist(g(a[2]),g(a[3]))
    print("%.3f/brk%.2f/frm%.2f"%(dbrk-dfrm, dbrk, dfrm))
except Exception: print("NA")
PY
)
    echo "$k $tgt ${e:-NA} cv=${cv:-NA} converged=$conv" >> scan_energies.dat
    echo ">> [$(date +%H:%M:%S)] window $k done: E=${e:-NA} converged=$conv"
  else
    echo "$k $tgt FAILED" >> scan_energies.dat
    echo "!! window $k failed; stopping."; break
  fi
  prev="$proj"; k=$((k+1))
done
echo ">> [$(date +%H:%M:%S)] scan finished."
