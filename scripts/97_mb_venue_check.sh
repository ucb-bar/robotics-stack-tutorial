#!/usr/bin/env bash
# Venue check: runs the ModelBlaster + LLM lab end to end on this seat and its board.
#
#   scripts/97_mb_venue_check.sh             # on a seat (a JupyterLab terminal): ~5 min
#   scripts/97_mb_venue_check.sh --full      # + the failure drills: a trapping kernel, the board
#                                            #   unreachable, the LLM key broken (~12 min)
#   scripts/97_mb_venue_check.sh --live      # + one live LLM run with the board in the loop (~6 min),
#                                            #   and every cell of mb_by_hand.ipynb (~10 min; it writes
#                                            #   ~/work/modelblaster-llm-lab/by-hand/, as an attendee would)
#   scripts/97_mb_venue_check.sh --seats FILE --ssh-key K [--jobs N] [--full|--live]
#                                            # from an instructor's machine: every seat, one line each
#
# Each check runs the attendee's commands (mb, the notebook helper) and checks their output.
# A table at the end gives PASS or FAIL per check and why; the exit code is the number of
# failures. The runs are ordinary runs and show up in `mb list`.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

FULL=0; LIVE=0; SEATS=""; KEY=""; JOBS=4
while [ $# -gt 0 ]; do
  case "$1" in
    --full)    FULL=1; shift ;;
    --live)    LIVE=1; shift ;;
    --seats)   SEATS="${2:?}"; shift 2 ;;
    --ssh-key) KEY="${2:?}"; shift 2 ;;
    --jobs)    JOBS="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

# ---- all seats: run this script on each seat over ssh, one line per seat -------------------
if [ -n "$SEATS" ]; then
  need_file "$SEATS"
  LOGD="$IISWC_OUT/venue_check/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$LOGD"
  export VC_ARGS="$([ "$FULL" = 1 ] && echo --full) $([ "$LIVE" = 1 ] && echo --live)"
  export VC_SSH="ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR${KEY:+ -i $KEY}"
  info "$(sed -e 's/#.*//' "$SEATS" | awk 'NF' | wc -l) seats, $JOBS at a time; logs in $LOGD"
  sed -e 's/#.*//' "$SEATS" | awk 'NF{print $1}' | xargs -P "$JOBS" -I{} sh -c \
    'l="$1/$(echo "$0" | tr "@/" "__").log"
     $VC_SSH "$0" "bash -lc \". ~/.config/iiswc/dev.env 2>/dev/null; ~/iiswc-tutorial/scripts/97_mb_venue_check.sh $VC_ARGS\"" > "$l" 2>&1
     rc=$?; s=$(grep -a "^SUMMARY" "$l" | tail -1)
     if [ "$rc" = 0 ]; then echo "PASS  $0   ${s#SUMMARY }"; else echo "FAIL  $0   ${s#SUMMARY }   ($l)"; fi' {} "$LOGD" \
    | sort -k2 | tee "$LOGD/summary.txt"
  ! grep -q '^FAIL' "$LOGD/summary.txt"; exit $?
fi

# ---- one seat ---------------------------------------------------------------------------------
[ -f "$HOME/.config/iiswc/dev.env" ] || die "run this on a seat set up by scripts/96_seat_mb_setup.sh (no ~/.config/iiswc/dev.env)"
MB="$IISWC_ROOT/fpga/pynq-z2/host/mb"
EX="$IISWC_ROOT/fpga/pynq-z2/modelblaster/mb_ops/exercises"
T="$(mktemp -d /tmp/mb-venue.XXXXXX)"; trap 'rm -rf "$T"' EXIT
RES=(); NFAIL=0
ANSI='s/\x1b\[[0-9;]*[A-Za-z]//g'
rec() {   # <PASS|FAIL> <check> <seconds> <detail>
  RES+=("$(printf '%-4s  %-44s %5ss  %s' "$1" "$2" "$3" "$4")")
  [ "$1" = PASS ] || NFAIL=$((NFAIL + 1))
  printf '%s%-4s%s  %s  (%ss)  %s\n' "$([ "$1" = PASS ] && echo "$_c_grn" || echo "$_c_red")" "$1" "$_c_off" "$2" "$3" "$4"
}
run() {   # <log name> <command...>: run an mb command, keep its output as plain text
  local t0=$SECONDS rc=0
  "${@:2}" > "$T/$1.log" 2>&1 < /dev/null || rc=$?      # some checks expect a failure; record rc and continue
  echo "$rc" > "$T/$1.rc"; echo $((SECONDS - t0)) > "$T/$1.s"
  sed -i "$ANSI" "$T/$1.log"
}
has() { grep -a -q -E "$2" "$T/$1.log"; }
field() { grep -a -o -E "$2" "$T/$1.log" | head -1; }

step "1/6  this seat, its board and the LLM key  (mb doctor)"
run doctor "$MB" doctor
if has doctor 'ready: run'; then rec PASS "mb doctor" "$(cat $T/doctor.s)" "$(field doctor 'board [a-z0-9-]+ answers' || true)"
else rec FAIL "mb doctor" "$(cat $T/doctor.s)" "$(grep -a -E 'FAIL' "$T/doctor.log" | head -2 | tr -s ' ' | tr '\n' ';')"; fi

step "2/6  a replayed LLM kernel on the FPGA  (mb go maxpool2d_s8 --replay)"
run replay "$MB" go maxpool2d_s8 --replay
if has replay 'PASS.*speedup [0-9.]+x' && has replay 'on the accelerator: YES'; then
  rec PASS "replay on the FPGA" "$(cat $T/replay.s)" "$(field replay 'speedup [0-9.]+x' | head -1) on the board, on the accelerator"
elif has replay 'spike only'; then rec FAIL "replay on the FPGA" "$(cat $T/replay.s)" "fell back to spike only: the board did not answer"
else rec FAIL "replay on the FPGA" "$(cat $T/replay.s)" "$(grep -a -E '\[fail\]|FAIL' "$T/replay.log" | head -1)"; fi

step "3/6  your turn, the solution kernel  (mb try maxpool2d_s8 <solution>)"
run try_ok "$MB" try maxpool2d_s8 "$EX/maxpool2d_s8_solution.c"
if has try_ok 'PASS.*YOUR KERNEL' && has try_ok 'on the accelerator: YES'; then
  rec PASS "mb try, the solution" "$(cat $T/try_ok.s)" "$(field try_ok 'speedup [0-9.]+x' | tail -1) on the board"
else rec FAIL "mb try, the solution" "$(cat $T/try_ok.s)" "$(grep -a -E '\[fail\]' "$T/try_ok.log" | head -1)"; fi

step "4/6  a wrong kernel must be stopped before the board"
sed 's/output\[((n\*C + c)\*OH + oh)\*OW + ow\] = m;/output[((n*C + c)*OH + oh)*OW + ow] = (int8_t)(m - (m > 100));/' \
  "$EX/maxpool2d_s8_start.c" > "$T/wrong.c"
run try_wrong "$MB" try maxpool2d_s8 "$T/wrong.c"
if has try_wrong 'not sending it to the board'; then rec PASS "a wrong kernel is refused" "$(cat $T/try_wrong.s)" "stopped on spike, never sent to the board"
else rec FAIL "a wrong kernel is refused" "$(cat $T/try_wrong.s)" "it was not stopped; rerun it by hand to see why ($T is removed on exit)"; fi

step "5/6  a kernel that does not compile says so"
sed 's/int8_t m = INT8_MIN;/int8_t m = INT8_MIN/' "$EX/maxpool2d_s8_start.c" > "$T/syntax.c"
run try_syntax "$MB" try maxpool2d_s8 "$T/syntax.c"
if has try_syntax 'does not compile'; then rec PASS "a broken kernel says 'does not compile'" "$(cat $T/try_syntax.s)" "with the compiler's own error"
else rec FAIL "a broken kernel says 'does not compile'" "$(cat $T/try_syntax.s)" "$(grep -a -E '\[fail\]' "$T/try_syntax.log" | head -1)"; fi

step "6/6  the notebooks"
L="$HOME/work/modelblaster-llm-lab"
NBOK=1; for f in README.md mb_lab.ipynb mb_lab_solved.ipynb mb_by_hand.ipynb mb_by_hand_solved.ipynb walkthroughs/README.md your-kernel/README.md; do [ -f "$L/$f" ] || NBOK=0; done
PYJ="$(command -v /opt/jup/bin/python3 || command -v python3)"
if [ "$NBOK" = 1 ] && "$PYJ" -c "import sys; sys.path.insert(0, '$IISWC_ROOT/notebooks/mb_lab'); import mb_lab; mb_lab.max8([1]*8, [2]*8)" >/dev/null 2>&1; then
  rec PASS "the lab's folder in JupyterLab" "0" "~/work/modelblaster-llm-lab: notebooks, walkthroughs, READMEs; the helper loads"
else rec FAIL "the lab's folder in JupyterLab" "0" "something missing in $L, or notebooks/mb_lab/mb_lab.py does not import in $PYJ"; fi

if [ "$FULL" = 1 ]; then
  step "drill: a kernel that traps (a misaligned 8-byte load) must not hang or reach the board"
  sed 's/int64_t r0 = MB_PEXT_LD8(row0 + iw_start);/int64_t r0 = MB_PEXT_LD8(row0 + iw_start + 1);/' "$EX/maxpool2d_s8_solution.c" > "$T/trap.c"
  run try_trap "$MB" try maxpool2d_s8 "$T/trap.c"
  if has try_trap 'TRAPS|not sending it to the board'; then rec PASS "a trapping kernel is stopped" "$(cat $T/try_trap.s)" "$(field try_trap 'TRAPS|not sending it to the board')"
  else rec FAIL "a trapping kernel is stopped" "$(cat $T/try_trap.s)" "$(grep -a -E '\[fail\]|PASS' "$T/try_trap.log" | head -1)"; fi

  step "drill: the board unreachable -> spike only"
  run noboard env MB_BOARD_AGENT_PORT=1 "$MB" go maxpool2d_s8 --replay
  if has noboard 'spike only' && has noboard 'PASS'; then rec PASS "no board: spike only fallback" "$(cat $T/noboard.s)" "verdict on spike, said so"
  else rec FAIL "no board: spike only fallback" "$(cat $T/noboard.s)" "$(grep -a -E '\[fail\]' "$T/noboard.log" | head -1)"; fi

  step "drill: the LLM key broken -> automatic replay"
  run nokey env AWS_BEARER_TOKEN_BEDROCK=not-a-key "$MB" go maxpool2d_s8
  if has nokey 'replaying a verified LLM kernel' && has nokey 'PASS'; then rec PASS "no LLM -> replay fallback" "$(cat $T/nokey.s)" "$(field nokey 'speedup [0-9.]+x' | tail -1) with the replayed kernel"
  else rec FAIL "no LLM -> replay fallback" "$(cat $T/nokey.s)" "$(grep -a -E '\[fail\]' "$T/nokey.log" | head -1)"; fi
fi

if [ "$LIVE" = 1 ]; then
  step "live: the LLM with the board in the loop  (mb go maxpool2d_s8)"
  run live "$MB" go maxpool2d_s8
  if has live 'PASS.*speedup [0-9.]+x' && has live 'on the accelerator: YES'; then
    rec PASS "live LLM run, board in the loop" "$(cat $T/live.s)" "$(field live 'speedup [0-9.]+x' | head -1) on the board"
  else rec FAIL "live LLM run, board in the loop" "$(cat $T/live.s)" "$(grep -a -E '\[fail\]|speedup' "$T/live.log" | head -1)"; fi

  step "live: mb_by_hand.ipynb, every cell  (ModelBlaster's own commands, as Run All does)"
  JUP="$(command -v /opt/jup/bin/jupyter || command -v jupyter || echo jupyter)"
  run byhand "$JUP" nbconvert --to notebook --execute --ExecutePreprocessor.timeout=1800 \
      --output-dir "$T" --output byhand "$IISWC_ROOT/notebooks/mb_lab/mb_by_hand.ipynb"
  BH="$(python3 - "$T/byhand.ipynb" <<'PY' 2>/dev/null
import json, re, sys
t = "".join("".join(o.get("text", "")) for c in json.load(open(sys.argv[1]))["cells"] for o in c.get("outputs", []))
m = re.search(r"([0-9.]+x) faster on your FPGA", t)
print(m.group(1) if m else "")
PY
)"
  if [ "$(cat "$T/byhand.rc")" = 0 ] && [ -n "$BH" ]; then
    rec PASS "mb_by_hand.ipynb, every cell" "$(cat $T/byhand.s)" "$BH on the board, from the tools' own commands"
  else rec FAIL "mb_by_hand.ipynb, every cell" "$(cat $T/byhand.s)" "$(grep -a -E 'Error' "$T/byhand.log" | tail -1)"; fi
fi

printf '\n%s==> venue check on %s%s\n' "$_c_grn" "$(hostname)" "$_c_off"
printf '  %s\n' "${RES[@]}"
echo "SUMMARY $(( ${#RES[@]} - NFAIL ))/${#RES[@]} passed"
exit "$NFAIL"
