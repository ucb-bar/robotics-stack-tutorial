#!/usr/bin/env bash
# Lab B-LLM1: ModelBlaster with an LLM in the loop, one kernel at a time.
#
#   ./scripts/95_mb_kernel_llm.sh --list                     # the kernel menu
#   ./scripts/95_mb_kernel_llm.sh --op maxpool2d_s8               # LLM writes + optimizes it, on spike
#   MB_BACKEND=reference ./scripts/95_mb_kernel_llm.sh --op maxpool2d_s8   # curated kernel, no LLM
#   ./scripts/95_mb_kernel_llm.sh --op gelu_s8 --hint none        # no algorithm hint for the LLM
#   ./scripts/95_mb_kernel_llm.sh --op maxpool2d_s8 --where board # + before and after on the FPGA card
#   ./scripts/95_mb_kernel_llm.sh --collect <run>            # fold in board results later
#
# One op, one model. Each menu entry is a single-operator model (KernelBench style) in
# fpga/pynq-z2/modelblaster/mb_ops/<op>.py. generate_kernels has no per-op switch (every op in
# the IR gets a kernel) and --optimize scores each candidate by building and running the
# whole model on spike, so a single-operator model keeps each candidate to seconds.
#
# Two arms. Most menu ops have a curated kernel, so `--target <t>` alone would pick it in both
# arms. The lab copies the curated tree into the run and deletes the op's curated files (as
# scripts/58 does for silu), then asserts what each arm got:
#   before  --backend reference on the steered copy  -> must be source=reference
#   after   MB_BACKEND=llm:       --backend llm --optimize on the same copy -> must have
#                                 called the LLM for the op and never logged a curated HIT
#           MB_BACKEND=reference: --backend reference on the unsteered tree -> must pick
#                                 the op's curated kernel
# An arm that picked up the curated kernel unintentionally fails the lab.
#
# Prompting. With --hint described (default) the LLM gets the op's target algorithms from
# ModelBlaster's database; for gelu_s8 that is pext_memo_lut, whose description spells out
# the memo table, and the lab checks the result. --hint none passes --algorithms direct, so
# the LLM gets only the float reference.
#
# Correctness checks:
#   1. ModelBlaster's host verify, bit-exact for integer ops, random inputs at 4 shapes
#   2. the spike golden, one real tensor end to end, MODELBLASTER_VERIFY max_abs_err
#   3. for pointwise ops, scripts/lib/mb_enum_check.py: the whole input domain against the
#      reference expression
#
# Spike cycles are not board cycles: spike counts one cycle per instruction with no memory
# timing. By default the spike builds are soft-float like the board (MB_SPIKE_FLOAT, below).
#
# Workarounds for the pinned ModelBlaster tree:
#   * modelblaster/harness has no backends/pext.conf or pext_nl.conf, so --optimize builds for
#     those targets fail in CMake. The lab builds a copy of the harness with both.
#   * build_and_run gets the model name (for kernel_<op>_<model> mangling) from a graph.json
#     next to the model dir; the lab copies it there, or every candidate fails to link.
#   * beam_search_optimize stops after its first improving iteration, so --iterations > 1
#     never runs; the lab runs ROUNDS instead (see below).
#   * the default LLM provider is gemini; LLM_PROVIDER=bedrock is set here.
#   * the spike used while optimizing is `spike` from PATH; the TACIT spike is put first.
#   * MODELBLASTER_MAX_USD cannot price deepseek, so mb_llm_tap.py caps the number of calls
#     (--max-calls) and records every prompt and response.
#
# Output: out/mb_lab/<stamp>-<op>-<backend>/{run.json,report.txt,...}, linked from
# out/mb_lab/latest.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

# ---- the menu --------------------------------------------------------------------------
# op|target|curated files to remove (relative to the kernels tree, separated by spaces)|
#   the curated pick MB_BACKEND=reference must find|enumerable|one line for --list|default --guide
MENU='maxpool2d_s8|pext|pext/pext_maxpool2d_s8_pext_max8_rows.c|curated/pext_max8_rows|no|2x2 max pool on the MBP accelerator (MBP.MAX8). 5-8 min, up to ~20x on the FPGA, bit-exact. Start with this one.|isa
gelu_s8|pext|pext/pext_gelu_s8_pext_memo_lut.c pext_nl/pext_nl_gelu_s8_pext_int_lut.c|curated/pext_memo_lut|yes|GELU. ~3 min, 3-5 LLM calls, 43-49x on the FPGA, bit-exact. Uses no accelerator; the gain comes from the algorithm.|modelblaster
sigmoid_s8|pext||-|yes|sigmoid. No pext algorithm and no curated kernel; the LLM stays at 1.0x. An open exercise.|modelblaster
linear_s8|pext|pext/pext_linear_s8_pext_row_dot8.c|curated/pext_row_dot8|no|int8 GEMM 64x256x256 with MBP.DOT8, QMUL and CLIP8. 9-12x on the FPGA.|isa'

menu_field() {  # op field#
  printf '%s\n' "$MENU" | awk -F'|' -v op="$1" -v f="$2" '$1==op{print $f}'
}

MB_BACKEND="${MB_BACKEND:-llm}"
OP=""
WHERE="spike"
HINT="described"
GUIDE=""
BEAM=2; EXPANSIONS=2; ITERATIONS=1; ROUNDS=2
MAX_CALLS="${MB_MAX_CALLS:-12}"
NAME=""
STEER=1
REPLAY=0
KERNEL_FILE=""
BOARD_LOOP=0
COLLECT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --op)         OP="${2:?}"; shift 2 ;;
    --backend)    MB_BACKEND="${2:?}"; shift 2 ;;
    --where)      WHERE="${2:?}"; shift 2 ;;
    --hint)       HINT="${2:?}"; shift 2 ;;
    # isa: append fpga/pynq-z2/modelblaster/mb_ops/pext_isa_guide.md to every system prompt.
    # At this ModelBlaster pin the pext backends send the scalar optimization guide, which
    # never mentions MBP.DOT8/MAX8/QMUL/CLIP8. modelblaster: send only what ModelBlaster sends.
    # A file path appends that file (see GUIDE_FILE below).
    --guide)      GUIDE="${2:?}"; shift 2 ;;
    --beam)       BEAM="${2:?}"; shift 2 ;;
    --expansions) EXPANSIONS="${2:?}"; shift 2 ;;
    --iterations) ITERATIONS="${2:?}"; shift 2 ;;
    --rounds)     ROUNDS="${2:?}"; shift 2 ;;
    --max-calls)  MAX_CALLS="${2:?}"; shift 2 ;;
    --name)       NAME="${2:?}"; shift 2 ;;
    # Negative control: keep the curated kernel in the LLM arm's tree. The lab must then fail
    # at the curated HIT assertion, which shows the assertion works.
    --no-steer)   STEER=0; shift ;;
    # Fallback when Bedrock is down or the key has expired: the after arm replays a kernel the
    # LLM wrote in an earlier verified run (mb_ops/replay/<op>.{c,algo,transcript.jsonl})
    # without a model call. Everything downstream (spike, the check of the whole input domain,
    # board) is unchanged.
    --replay)     REPLAY=1; shift ;;
    # FPGA in the optimization loop (needs --where board): each round's best kernel runs on
    # the board, the next round's prompt gets the board's cycles, and the kernel kept is the
    # fastest on the FPGA. Rounds run through the card agent, or `mb go` on the board pulls them.
    --board-loop) BOARD_LOOP=1; shift ;;
    # Your own kernel as the after arm (e.g. edited from mb_ops/exercises/<op>_start.c),
    # verified and measured like a replay, with no model call.
    --kernel)     KERNEL_FILE="${2:?}"; REPLAY=1; shift 2 ;;
    # Fold the board's results into a finished --where board run (see step 6).
    --collect)    COLLECT="${2:?}"; shift 2 ;;
    --list)
      printf '%s\n' "$MENU" | awk -F'|' '{printf "  %-13s %-8s %s\n", $1, $2, $6}'
      exit 0 ;;
    -h|--help) sed -n '2,53p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
# ---- --collect <run>: the board's consoles came back after the lab stopped waiting ---------
if [ -n "$COLLECT" ]; then
  LIB="$IISWC_ROOT/scripts/lib"
  RUN="$IISWC_OUT/mb_lab/$COLLECT"
  need_file "$RUN/run.json" "no finished lab run named $COLLECT under $IISWC_OUT/mb_lab"
  . "$LIB/mb_board.sh"
  OP="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['op'])" "$RUN/run.json")"
  step "collect the board results for $COLLECT ($OP)"
  mb_board_collect "$RUN" "$OP" "$COLLECT" || warn "the board arms are not both bit-exact; see $RUN/board.json"
  python3 - "$RUN" <<'PY' || die "could not fold board.json into run.json"
import json, sys
run = sys.argv[1]
r = json.load(open(f"{run}/run.json")); bj = json.load(open(f"{run}/board.json"))
r["where"] = "spike+board"; r["board"] = bj; r["board_speedup"] = bj.get("speedup")
spike_ok = r.get("spike_verdict", r.get("verdict")) == "PASS"
r["verdict"] = "PASS" if spike_ok and bj.get("verdict") == "PASS" else "FAIL"
json.dump(r, open(f"{run}/run.json", "w"), indent=2)
PY
  python3 "$LIB/mb_report.py" "$RUN" || die "the report generator failed"
  V=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['verdict'])" "$RUN/run.json")
  [ "$V" = PASS ] || die "verdict FAIL; see $RUN/report.txt"
  exit 0
fi
[ -n "$OP" ] || die "which kernel?  --op <op>; the menu is  $0 --list"
TARGET="$(menu_field "$OP" 2)"
[ -n "$TARGET" ] || die "$OP is not on the menu ($0 --list)"
REMOVE="$(menu_field "$OP" 3)"
ANSWER="$(menu_field "$OP" 4)"
ENUM="$(menu_field "$OP" 5)"
case "$MB_BACKEND" in llm|reference) ;; *) die "--backend must be llm or reference" ;; esac
case "$WHERE" in spike|board) ;; *) die "--where must be spike or board" ;; esac
case "$HINT" in described|none) ;; *) die "--hint must be described or none" ;; esac
# Ops whose curated kernel uses MBP instructions default to the ISA guide: without it they
# measured 1.0x (maxpool) and 0 of 4 synth (linear), with it 23.7x and 21.9x.
[ -n "$GUIDE" ] || GUIDE="$(menu_field "$OP" 7)"; GUIDE="${GUIDE:-modelblaster}"
# --guide <file>: your own text appended to every system prompt (the exercise in
# docs/MB_GELU_WALKTHROUGH.md: write the algorithm description ModelBlaster's database lacks).
GUIDE_FILE=""
case "$GUIDE" in
  modelblaster|isa) ;;
  *) [ -f "$GUIDE" ] || die "--guide must be modelblaster, isa, or a text file (no such file: $GUIDE)"
     GUIDE_FILE="$(cd "$(dirname "$GUIDE")" && pwd)/$(basename "$GUIDE")"; GUIDE="file:$(basename "$GUIDE")" ;;
esac
[ "$MB_BACKEND" = reference ] && [ "$ANSWER" = - ] \
  && die "$OP has no curated kernel, so MB_BACKEND=reference has nothing to compare; use the LLM"

MB="$ZCS/modelblaster"
KERNELS="$IISWC_ROOT/fpga/pynq-z2/modelblaster/kernels"
BENCH="$IISWC_ROOT/fpga/pynq-z2/modelblaster/mb_ops/$OP.py"
LIB="$IISWC_ROOT/scripts/lib"
[ -d "$MB/pipeline" ] || die "no modelblaster at $MB; source ~/.config/iiswc/dev.env first"
need_file "$BENCH"
for f in $REMOVE; do need_file "$KERNELS/$f" "a curated kernel the menu says $OP has"; done
need_file "$IISWC_ROOT/fpga/pynq-z2/sw/pext.h"
need_exec "$TACIT_SPIKE" "the TACIT spike (has the MBP opcodes)"
command -v west >/dev/null 2>&1 || die "west not on PATH; source ~/.config/iiswc/dev.env"

STAMP="$(date +%Y%m%d-%H%M%S)"
TAG="$MB_BACKEND"; [ "$REPLAY" = 1 ] && TAG=replay; [ -n "$KERNEL_FILE" ] && TAG=mine; [ "$HINT" = none ] && TAG="$TAG-nohint"; [ "$GUIDE" = isa ] && TAG="$TAG-isa"; [ -n "$GUIDE_FILE" ] && TAG="$TAG-myguide"
NAME="${NAME:-$STAMP-$OP-$TAG}"
TOP="$IISWC_OUT/mb_lab"
RUN="$TOP/$NAME"
[ -e "$RUN" ] && die "$RUN exists; pass a different --name"
mkdir -p "$RUN"
ln -sfn "$NAME" "$TOP/latest"

export PYTHONPATH="$ZCS${PYTHONPATH:+:$PYTHONPATH}"
export CPATH="$IISWC_ROOT/fpga/pynq-z2/sw${CPATH:+:$CPATH}"
export PATH="$(dirname "$TACIT_SPIKE"):$PATH"
# Score on the card's ISA. The card's Rocket has no FPU and its images are rv64imac/lp64
# (soft-float), but ModelBlaster's spike harness defaults to rv64imafdc, which made candidates
# heavy in float code look ~25x cheaper on spike than on the FPGA. CONFIG_FPU=n makes every
# spike build here, including each candidate the optimizer ranks (via
# MODELBLASTER_EXTRA_CMAKE_ARGS), compile like the board image:
# -march=rv64imac_zicsr_zifencei -mabi=lp64.
SPIKE_FLOAT="${MB_SPIKE_FLOAT:-soft}"
if [ "$SPIKE_FLOAT" = soft ]; then
  export MODELBLASTER_EXTRA_CMAKE_ARGS="-DCONFIG_FPU=n;-DCONFIG_FLOAT_HARD=n${MODELBLASTER_EXTRA_CMAKE_ARGS:+;$MODELBLASTER_EXTRA_CMAKE_ARGS}"
fi
T0=$(date +%s)

# The run's live status, read by `mb` (on the seat or the board), the notebook and
# scripts/lib/mb_progress.py to show the run as it happens.
status() {
  python3 - "$RUN/status.json" "$1" "$2" "${3:-}" "$OP" "$MB_BACKEND" "$HINT" "$GUIDE" "$$" <<'PY' || true
import json, os, sys, time
f, state, step, why, op, be, hint, guide, pid = sys.argv[1:]
d = {"state": state, "step": step, "op": op, "backend": be, "hint": hint, "guide": guide,
     "t": int(time.time()), "pid": int(pid), "host": os.uname().nodename}
if why:
    d["why"] = why
tmp = os.path.join(os.path.dirname(f), ".status.json.tmp")
with open(tmp, "w") as fh:
    fh.write(json.dumps(d) + "\n")
os.replace(tmp, f)
PY
}
# die() records its message so the status can say why a run stopped, not only where.
FAIL_WHY=""
die() { FAIL_WHY="$*"; printf '%s[fail]%s %s\n' "$_c_red" "$_c_off" "$*" >&2; exit 1; }
trap 'FAIL_WHY="${FAIL_WHY:-failed: $BASH_COMMAND}"' ERR
trap 'FAIL_WHY="interrupted"; exit 130' INT TERM
trap '[ -f "$RUN/run.json" ] || status failed "$CUR_STEP" "$FAIL_WHY"' EXIT
CUR_STEP="starting"; status running "$CUR_STEP"
lstep() { CUR_STEP="$1"; step "$1"; status running "$1"; }

# A command line as it was run, appended to commands.txt for the report's "how to redo it".
note_cmd() { printf '%s\n' "$*" >> "$RUN/commands.txt"; }
# commands.sh logs every tool this run executed, in order, with its working directory and
# arguments, so the run can be inspected (mb commands <run>) and rerun by hand.
log_cmd() { { printf 'cd %q && ' "$PWD"; printf '%q ' "$@"; printf '\n'; } >> "$RUN/commands.sh"; }
run() { printf '%s    $ %s%s\n' "$_c_dim" "$*" "$_c_off"; log_cmd "$@"; "$@"; }
export MB_CMD_LOG="$RUN/commands.sh"
note_cmd "MB_BACKEND=$MB_BACKEND ./scripts/95_mb_kernel_llm.sh --op $OP --where $WHERE --hint $HINT --guide $GUIDE --beam $BEAM --expansions $EXPANSIONS --rounds $ROUNDS --max-calls $MAX_CALLS"

# Build ModelBlaster's harness for spike on a generated model dir, run it on the TACIT
# spike, and write <arm>/spike.json with the op's cycles and the golden verdict.
spike_measure() {
  local arm="$1" gen="$RUN/$1/gen"
  rm -rf "$RUN/$arm/build"
  run west build -p always -b spike_riscv64 "$RUN/harness" --build-dir "$RUN/$arm/build" -- \
      -DMODEL_DIR="$gen" -DMODELBLASTER_BACKEND="$TARGET" \
      "-DMODELBLASTER_KERNEL_CFLAGS=-DMB_PEXT_HW=1" \
      $( [ "$SPIKE_FLOAT" = soft ] && echo "-DCONFIG_FPU=n -DCONFIG_FLOAT_HARD=n" ) \
    > "$RUN/$arm/build.log" 2>&1 || {
      # Show the compiler's error lines rather than ninja's tail.
      grep -E -A3 'error:' "$RUN/$arm/build.log" | head -20 | sed 's/^/    /'
      grep -q 'error:' "$RUN/$arm/build.log" || tail -30 "$RUN/$arm/build.log"
      [ "$arm" = after ] && [ -n "$KERNEL_FILE" ] && die "your kernel does not compile (above).  Fix it:  mb edit $OP   then   mb try $OP"
      die "$arm: spike build failed; see $RUN/$arm/build.log"; }
  # The reference (float on a soft-float build) can take minutes, but a new kernel that traps
  # (e.g. a misaligned 8-byte load) would hang for the full 600 s, so the after arm gets less.
  local tmo=600; [ "$arm" = after ] && tmo="${MB_SPIKE_AFTER_TIMEOUT:-180}"
  ( cd "$RUN/$arm" && log_cmd "$TACIT_SPIKE" build/zephyr/zephyr.elf && timeout "$tmo" "$TACIT_SPIKE" build/zephyr/zephyr.elf ) 2>&1 | tr -d '\r' > "$RUN/$arm/spike.txt" \
    || { [ "$arm" = after ] && die "the new kernel did not finish on spike within ${tmo} s: it most likely TRAPS (an MB_PEXT_LD8/ST8
       on an address that is not 8-byte aligned) or never ends.  It was not sent to the board.$( [ -n "$KERNEL_FILE" ] && printf '  Fix it:  mb edit %s' "$OP")"
         die "$arm: spike run failed; see $RUN/$arm/spike.txt"; }
  python3 - "$RUN/$arm/spike.txt" "$RUN/$arm/spike.json" "$OP" <<'PY' || die "$arm: could not parse the spike run"
import json, re, sys
t, op = open(sys.argv[1]).read(), sys.argv[3]
v = re.search(r"MODELBLASTER_VERIFY === max_abs_err=(\S+) max_rel_err=(\S+) n=(\d+)", t)
rows = re.findall(rf"^\d+,\w+,{op},([^,]*),(\d+)$", t, re.M)
if not (v and rows):
    sys.exit(f"no MODELBLASTER_VERIFY / {op} profile row in the spike output")
cyc = sum(int(c) for _, c in rows)
n = int(v.group(3))
r = {"shape": rows[0][0], "cycles": cyc, "n_out": n,
     "golden_max_abs_err": float(v.group(1)), "cycles_per_output": cyc / n}
json.dump(r, open(sys.argv[2], "w"), indent=2)
print(f"    {op}: {cyc:,} cycles ({cyc / n:.1f}/output element), golden max_abs_err {v.group(1)}")
PY
}

# The fields of kernel_picks.json for the op, as "source/algorithm".
pick_of() {
  python3 -c "import json,sys;p=json.load(open(sys.argv[1]))['picks'][sys.argv[2]];print(f\"{p['source']}/{p['algorithm']}\")" "$1" "$OP"
}

# ---------------------------------------------------------------------------
lstep "1/7  preflight"
info "op=$OP  target=$TARGET  backend=$MB_BACKEND  where=$WHERE  hint=$HINT"
if [ "$BOARD_LOOP" = 1 ]; then
  [ "$WHERE" = board ] || die "--board-loop needs --where board"
  if [ "$MB_BACKEND" != llm ] || [ "$REPLAY" = 1 ]; then
    info "--board-loop: nothing to iterate without the LLM (replay / your kernel / reference): one board step instead"
    BOARD_LOOP=0
  else
    info "board loop: every round's best kernel runs on the FPGA; the LLM is told the board's cycles"
  fi
fi
info "run: $RUN"
[ "$(command -v spike)" = "$TACIT_SPIKE" ] || die "spike on PATH is not \$TACIT_SPIKE"
if [ "$WHERE" = board ]; then
  # Check before spending any LLM calls. No board login is needed here: with the agent key the
  # lab drives the board through the card agent, otherwise the seat publishes and the board
  # pulls (see scripts/lib/mb_board.sh).
  . "$LIB/mb_board.sh"
  python3 "$IISWC_ROOT/fpga/pynq-z2/scripts/feature_gate.py" features "$MB_BOARD_BIT_MD5" >/dev/null 2>&1 \
    || die "fpga/pynq-z2/MAGIC_FEATURES.tsv has no row for $MB_BOARD_BIT_MD5 ($MB_BOARD_MAGIC):
       the feature gate would refuse the board images after the LLM rounds.  Merge the row first."
  info "board: $MB_BOARD_MAGIC, guest $MB_BOARD_NAME, MB_ITERS=$MB_BOARD_ITERS; publishes to $MB_PUB/$NAME"
  BOARD_AGENT=0
  if mb_board_agent_on; then
    B_HOST="$(mb_board_agent_host)"
    [ -n "$B_HOST" ] || die "this seat holds the board agent's key, but no board answers through the tunnel
       (port $MB_BOARD_AGENT_PORT).  Is your board on, and its iiswc-tunnel.service up?  (mb doctor)"
    BOARD_AGENT=1
    info "board: $B_HOST answers through its reverse tunnel; this lab runs the board steps itself"
  fi
fi
if [ "$MB_BACKEND" = llm ] && [ "$REPLAY" = 0 ]; then
  "$IISWC_ROOT/scripts/94_bedrock_check.sh" > "$RUN/bedrock_check.txt" 2>&1 \
    || { cat "$RUN/bedrock_check.txt"; die "the Bedrock key does not answer; see docs/BEDROCK.md §8"; }
  info "bedrock: $(grep -o 'deepseek[^ ]* in [^ ]* answered in [^ ]*' "$RUN/bedrock_check.txt" || echo ok)"
  info "LLM calls capped at $MAX_CALLS; beam=$BEAM expansions=$EXPANSIONS rounds=$ROUNDS"
fi

# ---------------------------------------------------------------------------
lstep "2/7  single-operator model: $OP  (fpga/pynq-z2/modelblaster/mb_ops/$OP.py)"
mkdir -p "$RUN/ir"
( cd "$ZCS" && run python -m modelblaster.pipeline.extract_graph \
    --bench-file "$BENCH" --out-dir "$RUN/ir" --quant int8 --num-calibration 1 \
    --fusion-target "$TARGET" --bench-target-mb 0 ) > "$RUN/ir/extract.log" 2>&1 \
  || { tail -20 "$RUN/ir/extract.log"; die "extract_graph failed"; }
python3 - "$RUN/ir/graph.json" "$OP" <<'PY' || die "the IR is not exactly one $OP"
import json, sys
ops = [o for o in json.load(open(sys.argv[1]))["ops"] if o["op"] != "view"]
print(f"    ops: {[(o['op'], o.get('shape')) for o in ops]}")
sys.exit(0 if [o["op"] for o in ops] == [sys.argv[2]] else 1)
PY
note_cmd "extract_graph --bench-file fpga/pynq-z2/modelblaster/mb_ops/$OP.py --quant int8 --fusion-target $TARGET"

# The harness copy with the overlays ModelBlaster's own harness lacks (see header).
cp -r "$MB/harness" "$RUN/harness"
chmod -R u+w "$RUN/harness"
for t in pext pext_nl; do
  printf '# %s backend: nothing beyond prj.conf, as for scalar.conf. Added by scripts/95.\n' "$t" \
    > "$RUN/harness/backends/$t.conf"
done

# The steered curated tree: everything the repo curates except this op's kernels.
cp -r "$KERNELS" "$RUN/kernels_steered"
if [ "$STEER" -eq 1 ]; then
  for f in $REMOVE; do rm -f "$RUN/kernels_steered/$f"; info "steered: removed $f from the run's copy"; done
  [ -n "$REMOVE" ] || info "nothing to steer: $OP has no curated kernel for $TARGET"
else
  warn "--no-steer: the curated kernel is LEFT IN (negative control)"
fi

# Skeleton for one arm, with graph.json next to it so build_and_run mangles kernel names.
skeleton() {
  mkdir -p "$RUN/$1/gen" "$RUN/$1/cache"
  ( cd "$ZCS" && run python -m modelblaster.pipeline.generate_skeleton \
      --ir "$RUN/ir/graph.json" --weights "$RUN/ir/weights.npz" --io "$RUN/ir/io.npz" \
      --out-dir "$RUN/$1/gen" --backend "$TARGET" ) > "$RUN/$1/codegen.log" 2>&1 \
    || { tail -20 "$RUN/$1/codegen.log"; die "$1: generate_skeleton failed"; }
  cp "$RUN/ir/graph.json" "$RUN/$1/gen/"
}
GK_COMMON=(--ir "$RUN/ir/graph.json" --target "$TARGET" --quant int8 --io "$RUN/ir/io.npz"
           --repo-root "$MB" --harness-dir "$RUN/harness")

# ---------------------------------------------------------------------------
lstep "3/7  before: ModelBlaster's reference kernel  (the slow version the LLM must beat)"
skeleton before
( cd "$ZCS" && run python -m modelblaster.pipeline.generate_kernels "${GK_COMMON[@]}" \
    --out-dir "$RUN/before/gen" --backend reference --build-dir "$RUN/before/kverify" \
    --cache-dir "$RUN/before/cache" --algorithms all \
    --global-curated-dir "$RUN/kernels_steered" ) >> "$RUN/before/codegen.log" 2>&1 \
  || { tail -30 "$RUN/before/codegen.log"; die "before: generate_kernels failed"; }
PICK_BEFORE="$(pick_of "$RUN/before/gen/kernel_picks.json")"
info "pick: $OP = $PICK_BEFORE"
if [ "$STEER" -eq 1 ]; then
  [ "${PICK_BEFORE%%/*}" = reference ] \
    || die "before arm picked $PICK_BEFORE, not the float reference: the steer did not take"
fi
note_cmd "generate_kernels --backend reference --target $TARGET --global-curated-dir <curated minus $OP's kernels>"
spike_measure before

# ---------------------------------------------------------------------------
RP_DIR="$IISWC_ROOT/fpga/pynq-z2/modelblaster/mb_ops/replay"
if [ "$REPLAY" = 1 ]; then
  if [ -n "$KERNEL_FILE" ]; then
    lstep "4/7  after: YOUR kernel, $KERNEL_FILE (no model call)"
    need_file "$KERNEL_FILE" "no kernel file $KERNEL_FILE"
    SRC="$KERNEL_FILE"
  else
    lstep "4/7  after: REPLAY of a verified LLM kernel (no model call)"
    need_file "$RP_DIR/$OP.c" "no replay kernel for $OP; replayable ops: $(cd "$RP_DIR" && ls *.c 2>/dev/null | sed 's/\.c$//' | tr '\n' ' ')"
    SRC="$RP_DIR/$OP.c"
  fi
  # The algorithm name only sets the file name ModelBlaster files the kernel under; it comes
  # from the menu's answer key.
  ALGO="${ANSWER#curated/}"
  [ -n "$ALGO" ] && [ "$ALGO" != "-" ] || die "$OP has no algorithm slot to file a kernel under"
  skeleton after
  # Put the replay kernel into the run's curated tree under its algorithm's file name, so
  # ModelBlaster probes, verifies and picks it like any curated kernel.
  cp -r "$RUN/kernels_steered" "$RUN/kernels_replay"
  mkdir -p "$RUN/kernels_replay/$TARGET"
  cp "$SRC" "$RUN/kernels_replay/$TARGET/${TARGET}_${OP}_${ALGO}.c"
  [ -n "$KERNEL_FILE" ] || cp "$RP_DIR/$OP.transcript.jsonl" "$RUN/after/transcript.jsonl"
  ( cd "$ZCS" && run python -m modelblaster.pipeline.generate_kernels "${GK_COMMON[@]}" \
      --out-dir "$RUN/after/gen" --backend reference --build-dir "$RUN/after/kverify" \
      --cache-dir "$RUN/after/cache" --algorithms all \
      --global-curated-dir "$RUN/kernels_replay" ) >> "$RUN/after/codegen.log" 2>&1 \
    || { tail -30 "$RUN/after/codegen.log"; die "after: generate_kernels (replay) failed"; }
  PICK_AFTER="$(pick_of "$RUN/after/gen/kernel_picks.json")"
  if [ "$PICK_AFTER" != "curated/$ALGO" ]; then
    # ModelBlaster rejected the file. It skips host verify for kernels in a curated slot, so this
    # is almost always a compile error; wrong outputs are caught by the spike golden below.
    grep -E -i 'error|verify|mismatch|FAIL' "$RUN/after/codegen.log" | grep -v -i 'warning' | tail -8 | sed 's/^/    /'
    [ -n "$KERNEL_FILE" ] && die "your kernel was rejected (above: most likely a compile error).
       Fix it and try again; the full log is $RUN/after/codegen.log"
    die "replay: ModelBlaster picked $PICK_AFTER, not the replayed $ALGO"
  fi
  PICK_AFTER="replay/$ALGO"; [ -n "$KERNEL_FILE" ] && PICK_AFTER="yours/$ALGO"
  AFTER_KERNEL="$RUN/kernels_replay/$TARGET/${TARGET}_${OP}_${ALGO}.c"
  if [ -n "$KERNEL_FILE" ]; then info "pick: $OP = $PICK_AFTER  (your kernel compiled; spike checks its output next)"
  else info "pick: $OP = $PICK_AFTER  (the kernel an earlier LLM run wrote; its transcript: mb calls)"; fi
  LLM_WALL=0
  note_cmd "(replay) generate_kernels --backend reference --global-curated-dir <curated minus $OP's, plus mb_ops/replay/$OP.c as $ALGO>"
elif [ "$MB_BACKEND" = llm ]; then
  lstep "4/7  after: the LLM writes it, then optimizes it  (--backend llm --optimize)"
  skeleton after
  ALGOS=all; [ "$HINT" = none ] && ALGOS=direct
  GUIDE_ARGS=()
  if [ -n "$GUIDE_FILE" ]; then
    GUIDE_ARGS=(--system-append "$GUIDE_FILE"); cp "$GUIDE_FILE" "$RUN/my_guide.txt"
    info "guide: appending YOUR text ($(wc -w < "$GUIDE_FILE") words) to every system prompt"
  elif [ "$GUIDE" = isa ]; then
    GUIDE_ARGS=(--system-append "$IISWC_ROOT/fpga/pynq-z2/modelblaster/mb_ops/pext_isa_guide.md")
    info "guide: appending mb_ops/pext_isa_guide.md (the MBP instructions) to every system prompt"
  fi
  GUIDE_ARGS+=(--system-append "$IISWC_ROOT/fpga/pynq-z2/modelblaster/mb_ops/idea_line.md")
  [ "$BOARD_LOOP" = 1 ] && GUIDE_ARGS+=(--system-append "$RUN/after/board_feedback.md")
  export LLM_PROVIDER=bedrock BEDROCK_CALLS_LOG="$RUN/after/calls.jsonl"
  info "every call is recorded in after/transcript.jsonl; progress below"
  TL0=$(date +%s)
  # Rounds instead of --iterations: beam_search_optimize (generate_kernels.py ~1428) updates
  # best_cycles while collecting candidates, then stops when `beam_set[0][1] >= best_cycles`,
  # which holds after any improving iteration, so --iterations > 1 never runs. Each round is
  # one generate_kernels call that starts from the previous round's best in --cache-dir.
  # Rounds stop when one does not improve or the call budget is spent.
  # A candidate that traps (e.g. a misaligned MB_PEXT_LD8) halts Zephyr and leaves spike
  # running until ModelBlaster's timeout (300 s by default). Healthy single-operator
  # candidates take seconds, and 90 s still fits the slowest arm here.
  export SPIKE_VERIFY_TIMEOUT="${SPIKE_VERIFY_TIMEOUT:-90}"
  RETRIED=0
  R=1
  while [ "$R" -le "$ROUNDS" ]; do
    USED=0; [ -f "$RUN/after/transcript.jsonl" ] && USED=$(wc -l < "$RUN/after/transcript.jsonl")
    LEFT=$(( MAX_CALLS - USED ))
    [ "$LEFT" -gt 0 ] || { info "round $R: call budget spent ($USED/$MAX_CALLS), stopping"; break; }
    info "round $R/$ROUNDS  ($LEFT calls left)"
    status running "4/7  LLM round $R/$ROUNDS"
    set +e
    ( cd "$ZCS" && python -u "$LIB/mb_llm_tap.py" --transcript "$RUN/after/transcript.jsonl" \
        --max-calls "$LEFT" --call-offset "$USED" --round "$R" --status "$RUN/status.json" \
        ${GUIDE_ARGS[@]+"${GUIDE_ARGS[@]}"} -- "${GK_COMMON[@]}" \
        --out-dir "$RUN/after/gen" --backend llm --build-dir "$RUN/after/kbuild" \
        --cache-dir "$RUN/after/cache" --algorithms "$ALGOS" \
        --global-curated-dir "$RUN/kernels_steered" \
        --optimize --beam "$BEAM" --expansions "$EXPANSIONS" --iterations "$ITERATIONS" ) 2>&1 \
      | tee -a "$RUN/after/codegen.log" \
      | grep --line-buffered -E '^\[tap\]|attempt [0-9]|verify (PASS|FAIL)|curated HIT|cache HIT|baseline=| -> [0-9]+ cyc|FAIL|BEST:|Traceback' \
      | sed -u 's/^ */      /'
    RC=${PIPESTATUS[0]}
    set -e
    if [ "$RC" -ne 0 ]; then
      # A synth kernel that passes host verify but fails on spike is fatal to the round inside
      # ModelBlaster. Retry the round once from an empty cache if calls remain; a second
      # failure is reported.
      if [ "$RETRIED" -eq 0 ] && ! grep -q 'curated HIT' "$RUN/after/codegen.log"; then
        RETRIED=1
        warn "round $R failed (exit $RC); retrying it once from scratch (see $RUN/after/codegen.log)"
        [ "$R" -eq 1 ] && rm -f "$RUN/after/cache"/*.c
        status running "4/7  LLM round $R: retrying after a failed candidate"
        continue
      fi
      die "after: generate_kernels (llm) round $R exited $RC; see $RUN/after/codegen.log"
    fi
    # Checked every round, so a run that used the curated kernel stops before spending more
    # calls on it.
    if grep -q 'curated HIT' "$RUN/after/codegen.log"; then
      die "after arm logged a curated HIT: it used the curated kernel, not the LLM's"
    fi
    [ -f "$RUN/after/gen/optimize_summary.json" ] \
      || die "round $R wrote no optimize_summary.json; see $RUN/after/codegen.log"
    mv "$RUN/after/gen/optimize_summary.json" "$RUN/after/round$R.optimize_summary.json"
    mv "$RUN/after/gen/beam_search_trajectory.jsonl" "$RUN/after/round$R.trajectory.jsonl" 2>/dev/null || true
    IMPROVED=1
    python3 -c "
import json,sys
s=json.load(open(sys.argv[1]))[sys.argv[2]]
sys.exit(0 if s['best'] < s['baseline'] else 1)" "$RUN/after/round$R.optimize_summary.json" "$OP" || IMPROVED=0
    # With the board in the loop, round 1's best always goes to the FPGA, improved or not,
    # because the verdict needs one board measurement. A later round that did not improve
    # holds a kernel already measured, so it is skipped.
    if [ "$BOARD_LOOP" = 1 ] && { [ "$IMPROVED" = 1 ] || [ "$R" = 1 ]; }; then
      # Run this round's best on the FPGA and feed the result into the next round's prompt.
      rm -rf "$RUN/after/gen.r$R"; cp -r "$RUN/after/gen" "$RUN/after/gen.r$R"
      cp "$(ls -t "$RUN/after/cache"/*.c | head -1)" "$RUN/after/round$R.kernel.c"
      RBEST=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]]['best'])" "$RUN/after/round$R.optimize_summary.json" "$OP")
      info "round $R: building its best kernel for the FPGA"
      status running "4/7  round $R: building its best kernel for the FPGA"
      mb_board_round "$RUN" "$TARGET" "$R" "$NAME" "$OP" >> "$RUN/board_rounds.log" 2>&1 \
        || { tail -20 "$RUN/board_rounds.log"; die "round $R: the board build failed; see $RUN/board_rounds.log"; }
      status running "4/7  round $R: the FPGA runs its best kernel"
      [ "$BOARD_AGENT" = 1 ] && mb_board_remote_run "$NAME-r$R"
      SRC_R="$(mb_board_round_wait "$RUN" "$R" "$NAME" "${MB_LOOP_WAIT:-900}")" \
        || die "round $R: no results from the board after $(( ${MB_LOOP_WAIT:-900} / 60 )) min (is \`mb go ... \` running on it?)"
      mb_board_round_record "$RUN" "$OP" "$R" "$SRC_R" "$RBEST" "$RUN/after/round$R.kernel.c"
    fi
    # With the board in the loop, a round that did not improve on spike does not end the
    # search: the next round sees the FPGA's numbers (e.g. slower than the reference), which
    # spike cannot show. Without the board, it does.
    if [ "$IMPROVED" = 0 ]; then
      if [ "$BOARD_LOOP" = 1 ] && [ "$R" -lt "$ROUNDS" ]; then info "round $R: no improvement on spike; the next round gets the FPGA's numbers"
      else info "round $R: no improvement, stopping"; break; fi
    fi
    R=$((R + 1))
  done
  [ "$BOARD_LOOP" = 1 ] && { mkdir -p "$MB_PUB/$NAME"; : > "$MB_PUB/$NAME/loop_done"; }
  LLM_WALL=$(( $(date +%s) - TL0 ))
  note_cmd "LLM_PROVIDER=bedrock scripts/lib/mb_llm_tap.py --max-calls <budget left> -- generate_kernels --backend llm --target $TARGET --algorithms $ALGOS --optimize --beam $BEAM --expansions $EXPANSIONS --iterations $ITERATIONS --global-curated-dir <curated minus $OP's kernels>   # x $ROUNDS rounds, same --cache-dir"

  # Second check that the result is the LLM's and not a renamed curated kernel.
  python3 - "$RUN/after/transcript.jsonl" "$OP" <<'PY' || die "after arm made no successful synth:$OP LLM call"
import json, sys, os
p = sys.argv[1]
calls = [json.loads(l) for l in open(p)] if os.path.exists(p) else []
ok = [c for c in calls if (c.get("phase") or "").startswith("synth:" + sys.argv[2]) and not c.get("error")]
print(f"    LLM calls: {len(calls)} ({len(ok)} synth ok)")
sys.exit(0 if ok else 1)
PY
  AFTER_KERNEL="$(ls -t "$RUN/after/cache"/*.c 2>/dev/null | head -1)"
  [ -n "$AFTER_KERNEL" ] || die "the LLM arm cached no kernel"
  if [ "$BOARD_LOOP" = 1 ]; then
    # Keep the round whose kernel was fastest on the FPGA and bit-exact there.
    BR=$(python3 -c "
import json,sys
rows=[json.loads(l) for l in open(sys.argv[1])]
ok=[r for r in rows if r.get('after_exact') and r.get('after_cycles')]
print(min(ok,key=lambda r:r['after_cycles'])['round'] if ok else '')" "$RUN/board_rounds.jsonl" 2>/dev/null || true)
    [ -f "$RUN/board_rounds.jsonl" ] || die "no round reached the FPGA; see $RUN/board_rounds.log"
    [ -n "$BR" ] || die "no round's kernel was bit-exact on the FPGA; see $RUN/board_rounds.jsonl"
    rm -rf "$RUN/after/gen"; cp -r "$RUN/after/gen.r$BR" "$RUN/after/gen"
    AFTER_KERNEL="$RUN/after/round$BR.kernel.c"
    info "kept round $BR's kernel: the fastest on the FPGA"
  fi
  PICK_AFTER="$(pick_of "$RUN/after/gen/kernel_picks.json")"
  info "pick: $OP = $PICK_AFTER  (kernel: ${AFTER_KERNEL#$RUN/})"
else
  lstep "4/7  after: the curated kernel, no LLM  (--backend reference, unsteered tree)"
  skeleton after
  ( cd "$ZCS" && run python -m modelblaster.pipeline.generate_kernels "${GK_COMMON[@]}" \
      --out-dir "$RUN/after/gen" --backend reference --build-dir "$RUN/after/kverify" \
      --cache-dir "$RUN/after/cache" --algorithms all \
      --global-curated-dir "$KERNELS" ) >> "$RUN/after/codegen.log" 2>&1 \
    || { tail -30 "$RUN/after/codegen.log"; die "after: generate_kernels failed"; }
  PICK_AFTER="$(pick_of "$RUN/after/gen/kernel_picks.json")"
  info "pick: $OP = $PICK_AFTER"
  [ "$PICK_AFTER" = "$ANSWER" ] || die "after arm picked $PICK_AFTER, not $ANSWER"
  AFTER_KERNEL="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['picks'][sys.argv[2]]['path'])" \
                  "$RUN/after/gen/kernel_picks.json" "$OP")"
  LLM_WALL=0
  note_cmd "generate_kernels --backend reference --target $TARGET --global-curated-dir fpga/pynq-z2/modelblaster/kernels"
fi
spike_measure after
# A kernel whose output is already wrong on spike does not go to the board: the attendee
# finds out in ~30 s, and the FPGA is not used for it.
AFTER_ERR="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['golden_max_abs_err'])" "$RUN/after/spike.json")"
if [ "$AFTER_ERR" != "0.0" ] && [ "$WHERE" = board ]; then
  die "the after kernel's output differs from the reference on spike (golden max_abs_err $AFTER_ERR):
       not sending it to the board.$( [ -n "$KERNEL_FILE" ] && printf '  Fix it:  mb edit %s   then   mb try %s' "$OP" "$OP")"
fi

# ---------------------------------------------------------------------------
if [ "$ENUM" = yes ]; then
  lstep "5/7  bit-exact over the whole input domain"
  SCALES=$(python3 -c "
import json,sys
o=[o for o in json.load(open(sys.argv[1]))['ops'] if o['op']==sys.argv[2]][0]
q=o.get('quant') or {}
ks=[k for k in ('scale_a','scale_b','scale_in','scale_out') if k in q]
print(','.join(str(q[k]) for k in ks))" "$RUN/ir/graph.json" "$OP" 2>/dev/null || true)
  python3 "$LIB/mb_enum_check.py" --op "$OP" --candidate "$AFTER_KERNEL" --build-dir "$RUN/enum" \
      --json "$RUN/enum.json" ${SCALES:+--scales "$SCALES"} 2>&1 | tail -6 | sed 's/^/    /'
  need_file "$RUN/enum.json" "the enumeration check produced no result"
else
  lstep "5/7  whole-domain check: not applicable to $OP (not a pointwise map); checks 1 and 2 stand"
fi

# ---------------------------------------------------------------------------
if [ "$WHERE" = board ] && [ "$BOARD_LOOP" = 1 ]; then
  lstep "6/7  board: the kept kernel was measured in round $BR; the reference in round 1"
  # Nothing new to run: the verdict uses the loop's own board runs (one PL session each, same
  # bitstream), the same data a separate board step would produce.
  SRC1="$(mb_board_round_wait "$RUN" 1 "$NAME" 5)" && SRCB="$(mb_board_round_wait "$RUN" "$BR" "$NAME" 5)" \
    || die "the loop's board results for rounds 1 and $BR are gone from $MB_FROM_BOARD"
  DEST="$(dirname "$SRCB")/$NAME"          # $MB_FROM_BOARD/<board or seat>/mb/<run>, where collect looks
  rm -rf "$RUN/board" "$DEST"; mkdir -p "$RUN/board" "$DEST"
  cp -r "$RUN/board.r1/before" "$RUN/board/before"
  for a in after mbpoff; do [ -d "$RUN/board.r$BR/$a" ] && cp -r "$RUN/board.r$BR/$a" "$RUN/board/$a"; done
  cp "$SRC1/console_before.txt" "$DEST/"
  for f in console_after.txt console_mbpoff.txt bitstream.md5 boot.log; do [ -f "$SRCB/$f" ] && cp "$SRCB/$f" "$DEST/"; done
  [ -f "$RUN/board/mbpoff/zephyr.bin" ] && { mkdir -p "$MB_PUB/$NAME"; cp "$RUN/board/mbpoff/zephyr.bin" "$MB_PUB/$NAME/mbpoff.bin"; }
  mb_board_collect "$RUN" "$OP" "$NAME" || warn "the board arms are not all bit-exact; see $RUN/board.json"
elif [ "$WHERE" = board ]; then
  lstep "6/7  board: publish the images for $MB_BOARD_MAGIC; the board runs them"
  # Build and gate here, publish under $MB_PUB/<run>/, then wait for the board's consoles to
  # arrive in $MB_FROM_BOARD/<board or seat>/mb/<run>/ (mb_board_remote_run puts them there
  # through the card agent; without the agent, aws_push.sh on the board does).
  mb_board_publish "$RUN" "$OP" "$TARGET" "$NAME"
  if [ "$BOARD_AGENT" = 1 ]; then
    status running "6/7  the FPGA runs the images"
    mb_board_remote_run "$NAME"
  fi
  note_cmd "west build -p always -b $MB_BOARD_NAME samples/modelblaster_pext -- -DBOARD_ROOT=\$IISWC_ROOT -DMODEL_DIR=<arm>/gen -DMB_ITERS=$MB_BOARD_ITERS -DMB_WARMUP=1 -DMODELBLASTER_KERNEL_CFLAGS=\"$(mb_board_cflags "$TARGET")\"   # before and after"
  BOARD_CMD="$(mb_board_command "$NAME")"
  printf '%s\n' "$BOARD_CMD" > "$RUN/board_command.txt"
  note_cmd "on the board:  $BOARD_CMD"
  WAIT_S="${MB_BOARD_WAIT:-900}"
  if [ "$BOARD_AGENT" = 0 ]; then
    info ""
    info "NOW, on your board (logged in over the tutorial WiFi), run:"
    info ""
    info "    $BOARD_CMD"
    info ""
    info "waiting up to $((WAIT_S / 60)) min for the results (Ctrl-C is safe: collect later with --collect $NAME)"
  fi
  if [ "$BOARD_AGENT" = 1 ]; then status running "6/7  collecting the board's results"
  elif [ "$WAIT_S" = 0 ]; then status running "6/7  images published; your board is running them"
  else status running "6/7  waiting for the board: run the command above on your board"; fi
  T_W=$(date +%s)
  while ! mb_board_find "$NAME" >/dev/null && [ $(( $(date +%s) - T_W )) -lt "$WAIT_S" ]; do sleep 5; done
  if mb_board_find "$NAME" >/dev/null; then
    mb_board_collect "$RUN" "$OP" "$NAME" || warn "the board arms are not both bit-exact; see $RUN/board.json"
  else
    warn "no board results after $((WAIT_S / 60)) min: the spike result stands; later:
       ./scripts/95_mb_kernel_llm.sh --collect $NAME"
  fi
else
  lstep "6/7  board: not requested (--where board runs before and after on the card)"
fi

# ---------------------------------------------------------------------------
lstep "7/7  verdict and report"
python3 - "$RUN" <<PY || die "could not write $RUN/run.json"
import json, sys, os, glob
run = sys.argv[1]
op, backend, hint, target, guide = "$OP", "$MB_BACKEND", "$HINT", "$TARGET", "$GUIDE"
b = json.load(open(f"{run}/before/spike.json"))
a = json.load(open(f"{run}/after/spike.json"))
e = json.load(open(f"{run}/enum.json")) if os.path.exists(f"{run}/enum.json") else None
tp = f"{run}/after/transcript.jsonl"
calls = [json.loads(l) for l in open(tp) if l.strip()] if os.path.exists(tp) else []
summ = {}
rounds = sorted(glob.glob(f"{run}/after/round*.optimize_summary.json"),
                key=lambda p: int(os.path.basename(p)[5:].split(".")[0]))
for k, sp in enumerate(rounds, 1):
    s = json.load(open(sp)).get(op, {})
    if not summ:
        summ = {"baseline": s.get("baseline"), "history": []}
    summ["best"] = s.get("best")
    summ["history"] += [{**h, "round": k} for h in s.get("history", [])]
summ["rounds"] = len(rounds)
kpath = "$AFTER_KERNEL"
r = {
    "lab": "95_mb_kernel_llm", "op": op, "target": target, "where": "spike",
    "backend": backend, "replay": "$REPLAY" == "1" and not "$KERNEL_FILE", "kernel_file": "$KERNEL_FILE" or None, "hint": hint, "guide": guide, "model": os.environ.get("MODEL"),
    "pick_before": "$PICK_BEFORE", "pick_after": "$PICK_AFTER",
    "after_kernel": os.path.relpath(kpath, run) if kpath.startswith(run) else kpath,
    "before": b, "after": a, "speedup": b["cycles"] / a["cycles"],
    "enumerated": e, "optimize": summ,
    "llm_calls": 0 if "$REPLAY" == "1" else len(calls),   # a replay shows the transcript that wrote it, but calls nothing
    "llm_tokens_in": sum(c.get("input_tokens") or 0 for c in calls),
    "llm_tokens_out": sum(c.get("output_tokens") or 0 for c in calls),
    "llm_seconds": sum(c.get("latency_s") or 0 for c in calls),
    "llm_wall_s": int("$LLM_WALL"), "wall_s": int(os.path.getmtime(f"{run}/after/spike.json") - $T0),
    "spike_float": "$SPIKE_FLOAT",
    "cycles_note": ("spike: built soft-float rv64imac like the board (no FPU), one cycle per "
                    "instruction; the board's real cycles also pay for memory"
                    if "$SPIKE_FLOAT" == "soft" else
                    "spike: rv64gc with a hardware FPU, one cycle per instruction; the board is "
                    "WithoutFPU (soft-float), so float references are far slower there"),
}
bj = f"{run}/board.json"
if os.path.exists(bj):
    r["where"] = "spike+board"
    r["board"] = json.load(open(bj))
    r["board_speedup"] = r["board"].get("speedup")
ok = a["golden_max_abs_err"] == 0 and b["golden_max_abs_err"] == 0 and (e is None or e["bit_exact"])
r["spike_verdict"] = "PASS" if ok else "FAIL"
if "$WHERE" == "board":
    if "board" in r:
        ok = ok and r["board"].get("verdict") == "PASS"
    else:
        # Not a failure: the board is the attendee's step and may come later (--collect).
        r["board"] = {"state": "pending",
                      "command": open(f"{run}/board_command.txt").read().strip()}
r["verdict"] = "PASS" if ok else "FAIL"
json.dump(r, open(f"{run}/run.json", "w"), indent=2)
PY
# The report is written whatever the verdict: a failing run is the one most worth reading.
python3 "$LIB/mb_report.py" "$RUN" >/dev/null || die "the report generator failed"
VERDICT=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['verdict'])" "$RUN/run.json")
status "$( [ "$VERDICT" = PASS ] && echo done || echo failed )" "verdict $VERDICT"
cat "$RUN/report.txt"
info ""
info "every LLM call:  python3 scripts/lib/mb_report.py --calls $RUN   (on the board: mb calls $NAME)"
[ "$VERDICT" = PASS ] || die "verdict FAIL: not bit-exact somewhere; see $RUN/report.txt"
