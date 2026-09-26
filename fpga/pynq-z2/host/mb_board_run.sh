#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Board side of scripts/95_mb_kernel_llm.sh --where board. Runs on the board.
#
#   mb_board_run.sh <run>                 # the run name the seat printed
#   mb_board_run.sh <run> aws-NN.iiswc    # a seat other than this board's own
#
# The seat has already run the LLM and spike steps and published images of the same
# single-operator model under ~/pub/mb/<run>/: the float reference (before), the LLM's
# kernel (after), and optionally mbpoff, all for chipyard_pynqz1_all_f40 on bitstream
# 0x5A5A0038. This script is the board step when mb runs on the board. When mb runs on the
# seat, the seat drives the board through the card agent instead (scripts/lib/mb_board.sh)
# and this script is unused.
#
#   1. pull   manifest.json, before.bin, after.bin [, mbpoff.bin]   (aws_pull.sh)
#   2. check  nobody else holds the console, md5 of each image against the manifest
#   3. load   the PL once (skip with SKIP_BITSTREAM=1 when it already holds 0x38)
#   4. run    each image in turn: console reader + loader, same bitstream
#   5. push   boot.log and the consoles back to ~/from-board/<seat>/mb/<run>/ (aws_push.sh)
#
# The seat then parses the consoles (cycles per arm, the guest's golden check, a diff of the
# output tensor on the host) and adds the board numbers to the run's report.
#
# The board is behind the tutorial router's NAT, so nothing can connect to it; the board pulls
# and pushes with the seat key and aws_* scripts on every card, like aws_run.sh does for one
# image.
#
# Environment (all optional; defaults match aws_run.sh):
#   PYNQ_DIR   staging dir with the loader and console.py   (default /home/xilinx/tutorial)
#   VARIANT    loader/bitstream family                        (default roccmoonnch8f40b98ball)
#   FCLK       FCLK0 MHz                                      (default 40)
#   RUN_S      seconds to read each console                   (default 120)
#   IDLE       stop reading after this many silent seconds    (default 20; the float
#              reference computes for about 9 s without printing)
#   SKIP_BITSTREAM=1   do not reprogram the PL
#   MB_LOCAL=1  development only: the images are already in ~/mb-runs/<run>/; skip the pull
#               and the push, no seat involved
#   MB_WORK     where the run's files live on the board             (default ~/mb-runs)
#
# The loader and console.py are taken from $PYNQ_DIR (a provisioned staging dir), this
# script's directory, or /opt/iiswc/host (the tutorial card image), whichever has them.
set -euo pipefail

RUN_NAME="${1:?usage: mb_board_run.sh <run> [aws-NN.iiswc|ip]}"
PYNQ_DIR="${PYNQ_DIR:-/home/xilinx/tutorial}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARIANT="${VARIANT:-roccmoonnch8f40b98ball}"
RUNNER="run_rocket_${VARIANT}.py"
BIT="${BIT:-pynqz1_rocket_micrgb_${VARIANT}.bit}"
FCLK="${FCLK:-40}"
RUN_S="${RUN_S:-120}"
IDLE="${IDLE:-20}"
WANT_MAGIC="0x5A5A0038"

c_grn=$'\033[32m'; c_red=$'\033[31m'; c_yel=$'\033[33m'; c_off=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "$c_grn" "$*" "$c_off"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s[warn]%s %s\n' "$c_yel" "$c_off" "$*" >&2; }
die()  { printf '%s[fail]%s %s\n' "$c_red" "$c_off" "$*" >&2; exit 1; }

case "$RUN_NAME" in *[!A-Za-z0-9._-]*) die "run names are [A-Za-z0-9._-] only: '$RUN_NAME'" ;; esac
LOCAL="${MB_LOCAL:-0}"
HOSTDIR=""
for d in "$PYNQ_DIR" "$HERE" /opt/iiswc/host; do
  [ -f "$d/$RUNNER" ] && [ -f "$d/console.py" ] && { HOSTDIR="$d"; break; }
done
[ -n "$HOSTDIR" ] || die "no $RUNNER + console.py in $PYNQ_DIR, $HERE or /opt/iiswc/host"
AWS="$HERE"; [ -x "$AWS/aws_pull.sh" ] || AWS=/opt/iiswc/host
if [ "$LOCAL" != 1 ]; then
  for t in aws_pull.sh aws_push.sh aws_whoami.sh; do
    [ -x "$AWS/$t" ] || die "$t is not in $AWS; this card is not provisioned for the seat path"
  done
fi

# ---- 0.  which seat ---------------------------------------------------------------------
TARGET="${2:-}"
if [ "$LOCAL" = 1 ]; then
  TARGET="(local)"
elif [ -z "$TARGET" ]; then
  TARGET="$("$AWS/aws_whoami.sh" --name)" || die "could not work out this board's seat; pass it:
       mb_board_run.sh $RUN_NAME aws-NN.iiswc"
  info "this board's seat: $TARGET"
fi
REMOTE="pub/mb/$RUN_NAME"
WORK="${MB_WORK:-$HOME/mb-runs}/$RUN_NAME"
mkdir -p "$WORK"

# ---- 1.  pull ---------------------------------------------------------------------------
if [ "$LOCAL" = 1 ]; then
  step "1/5  MB_LOCAL=1: using the images already in $WORK (no seat)"
  [ -f "$WORK/manifest.json" ] || die "MB_LOCAL=1 but no $WORK/manifest.json"
else
  step "1/5  pull $REMOTE from $TARGET"
  "$AWS/aws_pull.sh" "$TARGET" "$REMOTE/manifest.json" "$WORK/manifest.json" >/dev/null \
    || die "could not pull $REMOTE/manifest.json (did the lab on the seat finish its build step?)"
fi
MF="$WORK/manifest.json"
mf() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))$1)" "$MF"; }
# before, after, and mbpoff if the LLM's kernel uses the MBP (the same kernel with the MBP
# replaced by a software model that matches it bit for bit).
ARMS="$(python3 -c "import json,sys;i=json.load(open(sys.argv[1]))['images'];print(' '.join(a for a in ('before','after','mbpoff') if a in i))" "$MF")"
for arm in $ARMS; do
  if [ "$LOCAL" = 1 ]; then [ -f "$WORK/$arm.bin" ] || die "MB_LOCAL=1 but no $WORK/$arm.bin"; continue; fi
  "$AWS/aws_pull.sh" "$TARGET" "$REMOTE/$arm.bin" "$WORK/$arm.bin" >/dev/null \
    || die "could not pull $REMOTE/$arm.bin (did the lab on the seat finish its build step?)"
done
OP="$(mf "['op']")"
[ "$(mf "['magic']")" = "$WANT_MAGIC" ] || die "the manifest is for $(mf "['magic']"), this script loads $WANT_MAGIC"
info "run $RUN_NAME: op $OP, $(mf "['llm_calls']") LLM calls on the seat"

# ---- 2.  checks -------------------------------------------------------------------------
step "2/5  image md5s, and nobody else on the console"
for arm in $ARMS; do
  want="$(mf "['images']['$arm']['md5']")"
  got="$(md5sum < "$WORK/$arm.bin" | cut -d' ' -f1)"
  [ "$want" = "$got" ] || die "$arm.bin md5 $got, the manifest says $want: a partial pull"
  info "$arm.bin  $got  ok"
done
# Two readers on one console corrupt each other's capture. Walk /proc and skip our own PID,
# as aws_run.sh does; a pattern match on "console" would also match our own command line.
others=""
for p in /proc/[0-9]*; do
  pid="${p#/proc/}"
  [ "$pid" = "$$" ] && continue
  c="$( { tr '\0' ' ' < "$p/cmdline"; } 2>/dev/null )" || continue
  case "$c" in *console.py*) others="$others$pid  $c"$'\n' ;; esac
done
[ -z "$others" ] || { printf '%s' "$others" | sed 's/^/        /' >&2
  die "another console reader holds /dev/ttyPS1; stop it by its PID, then rerun"; }
info "no other console reader"

# ---- root for the loader --------------------------------------------------------------------
# The labs expect passwordless sudo for xilinx (scripts/provision_board.sh checks it). On an
# unprovisioned card, ask for the password once if there is a terminal (sudo caches it for the
# run); otherwise fail with what is missing.
if sudo -n true 2>/dev/null; then
  SUDO="sudo -n"
elif [ -t 0 ]; then
  info "this board has no passwordless sudo; enter the board password once"
  sudo -v || die "sudo refused: the loader needs root"
  SUDO="sudo"
else
  die "the loader needs root and this board has no passwordless sudo.  Either run this from a
       terminal on the board (ssh -t ...) and type the board password once, or provision the
       card (passwordless sudo for xilinx; see scripts/provision_board.sh --check)."
fi

# ---- 3.  load the PL once ---------------------------------------------------------------
step "3/5  load the PL ($WANT_MAGIC, FCLK0 $FCLK MHz)"
cd "$WORK"
info "loader and console reader: $HOSTDIR"
if [ "${SKIP_BITSTREAM:-0}" != "1" ]; then
  BITP=""
  for c in "$PYNQ_DIR/$BIT" "$HOSTDIR/$BIT" "/opt/iiswc/bit/$BIT"; do [ -f "$c" ] && { BITP="$c"; break; }; done
  [ -n "$BITP" ] || die "no $BIT in $PYNQ_DIR or /opt/iiswc/bit; see fpga/pynq-z2/bitstreams.csv"
  # bash -lc: the PYNQ runtime is only on the PATH of root's login shell (see aws_run.sh).
  $SUDO bash -lc "cd $WORK && python3 -u $HOSTDIR/$RUNNER --bitstream $BITP --fclk $FCLK --hold" \
    > "$WORK/boot.log" 2>&1 || { tail -20 "$WORK/boot.log"; die "could not program the PL"; }
  grep -q "MAGIC = $WANT_MAGIC" "$WORK/boot.log" \
    || { tail -20 "$WORK/boot.log"; die "the PL did not come up as $WANT_MAGIC"; }
  md5sum "$BITP" | cut -d' ' -f1 > "$WORK/bitstream.md5"
  info "MAGIC $WANT_MAGIC, $(basename "$BITP") md5 $(cat "$WORK/bitstream.md5")"
else
  warn "SKIP_BITSTREAM=1: the PL is assumed to hold $WANT_MAGIC already (the card boots it)"
  : > "$WORK/boot.log"
fi

# ---- 4.  before, then after -------------------------------------------------------------
CPID=""
# The UART is root:dialout 0660, and xilinx is not in dialout on an unprovisioned card, so
# read it as root when needed. The reader's stderr goes to console_<arm>.err so that a
# failure to open the UART is not mistaken for a silent guest.
CSUDO=""
[ -r /dev/ttyPS1 ] && [ -w /dev/ttyPS1 ] || { CSUDO="$SUDO"; info "console: /dev/ttyPS1 (via sudo)"; }
# Under sudo, CPID is sudo itself, and sudo does not relay a signal sent from its own process
# group, so the reader would run its full --seconds window. Signal sudo's child directly.
stop_reader() {
  [ -n "${CPID:-}" ] || return 0
  if [ -n "$CSUDO" ]; then $CSUDO pkill -TERM -P "$CPID" 2>/dev/null || true; fi
  kill "$CPID" 2>/dev/null || true
}
trap 'stop_reader' EXIT INT TERM
run_arm() {   # <arm>: load that image, capture its console into console_<arm>.txt
  local arm="$1"
  cp "$WORK/$arm.bin" "$WORK/zephyr.bin"
  rm -f console.out
  nohup $CSUDO python3 -u "$HOSTDIR/console.py" --seconds "$RUN_S" --idle "$IDLE" > console.out 2> "$WORK/console_$arm.err" &
  CPID=$!
  sleep 1.5
  # --status prints a fresh boot status block, as in aws_run.sh; DRAM is not cleared between loads.
  $SUDO bash -lc "cd $WORK && python3 -u $HOSTDIR/$RUNNER --no-load --fclk $FCLK --elf $WORK/zephyr.bin --status" \
    >> "$WORK/boot.log" 2>&1 || { stop_reader; die "the loader failed on $arm"; }
  # Stop one second after the RESULT line instead of waiting out IDLE seconds of silence.
  for _ in $(seq 1 "$RUN_S"); do
    grep -aq '^RESULT:' console.out 2>/dev/null && { sleep 1; stop_reader; break; }
    $CSUDO kill -0 "$CPID" 2>/dev/null || break
    sleep 1
  done
  wait "$CPID" 2>/dev/null || true
  CPID=""
  tr -d '\r' < console.out > "$WORK/console_$arm.txt"
}

# One run at a time on this board, or two runs load images over each other. The lock is
# released when this script exits.
exec 9>>/tmp/mb-board.lock
if ! flock -n 9; then
  info "another run is using this board: waiting for it"
  flock -w 1800 9 || die "this board stayed busy for 30 min; is another  ~/mb  still running?  (ps aux | grep mb_board)"
fi
for arm in $ARMS; do
  case "$arm" in mbpoff) step "4/5  run the after kernel again with the accelerator OFF" ;;
                 *) step "4/5  run the $arm image" ;; esac
  run_arm "$arm"
  BYTES=$(wc -c < "$WORK/console_$arm.txt")
  [ "$BYTES" -gt 0 ] || { [ -s "$WORK/console_$arm.err" ] && { tail -3 "$WORK/console_$arm.err" >&2
      die "the console reader failed (above), not the guest"; }
    die "0 console bytes from the $arm image: the guest never printed.
       Stop here and tell the instructors: this is a board or bitstream problem, not the kernel."; }
  # A console without the RESULT line is an incomplete capture (a second reader, dropped
  # bytes), not a wrong kernel, so run the image once more.
  if ! grep -aq '^RESULT:' "$WORK/console_$arm.txt"; then
    warn "the $arm console came back incomplete (no RESULT line); running that image once more"
    run_arm "$arm"
    grep -aq '^RESULT:' "$WORK/console_$arm.txt" \
      || die "the $arm console is still incomplete.  Rerun just the board step:  ~/mb run $RUN_NAME"
  fi
  grep -a -E '^(MB_PEXT_RUN|RESULT)' "$WORK/console_$arm.txt" | sed 's/^/    /' || true
done

# ---- 5.  push back ----------------------------------------------------------------------
if [ "$LOCAL" = 1 ]; then
  printf '\n%sdone (MB_LOCAL=1).%s  Results are in %s on this board.\n' "$c_grn" "$c_off" "$WORK"
  exit 0
fi
step "5/5  push the consoles back to $TARGET"
for f in boot.log console_before.txt console_after.txt console_mbpoff.txt bitstream.md5; do
  [ -f "$WORK/$f" ] || continue
  "$AWS/aws_push.sh" -t "$TARGET" "$WORK/$f" "mb/$RUN_NAME" >/dev/null \
    || die "could not push $f; the results are on this board in $WORK"
done
printf '\n%sdone.%s  Results are on %s under ~/from-board/<seat>/mb/%s/;\n' "$c_grn" "$c_off" "$TARGET" "$RUN_NAME"
printf 'the lab on the seat picks them up (or: ~/mb collect %s).\n' "$RUN_NAME"
