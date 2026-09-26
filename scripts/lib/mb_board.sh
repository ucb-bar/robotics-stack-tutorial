# SPDX-License-Identifier: Apache-2.0
#
# Seat side of scripts/95_mb_kernel_llm.sh --where board (Lab B-LLM1). Sourced after
# scripts/lib/common.sh:
#
#   mb_board_publish <run> <op> <target> <name>   # build both arms for the card, gate them,
#                                                 # and publish to $MB_PUB/<name>/
#   mb_board_remote_run <name>                    # run the published images on the board
#                                                 # through the card's agent
#   mb_board_collect <run> <op> <name>            # find the board's consoles in
#                                                 # $MB_FROM_BOARD/*/mb/<name>/ -> <run>/board.json
#
# The card sits behind the tutorial router's NAT, so the seat cannot connect to it. The seat
# publishes the images and a manifest under ~/pub/mb/<name>/, and the consoles come back to
# ~/from-board/<board or seat>/mb/<name>/, where mb_board_collect parses them. Two transports:
#   * Card agent, when this seat holds its key: the card keeps a reverse ssh tunnel to its
#     seat, and mb_board_remote_run puts, runs and gets each image through it (see "board
#     agent" below).
#   * Board pull, otherwise or with MB_BOARD_NO_AGENT=1: the attendee runs one command on the
#     board (mb_board_command). It uses the card image's /opt/iiswc/host/aws_{pull,push,whoami}.sh
#     and fpga/pynq-z2/host/mb_board_run.sh to pull the images, run them in one PL session and
#     push the consoles back.
#
# Target: bitstream 0x5A5A0038 `all` (roccmoonnch8f40b98ball), which every card boots: the
# nch=8 RoCC engine in hart 1's tile and the MBP P-extension on hart 0, FCLK0 = 40 MHz. The
# guest board is chipyard_pynqz1_all_f40 (its PLIC/UART numbering differs from the pext
# builds). The build follows scripts/86_signdet_board.sh.
#
# Every modelblaster_pext image has one MBP.DOT8 in neg_worker (the negative test on hart 1),
# so MBP use is counted per function. SMP is required because the model is pinned to hart 0,
# where the MBP is. MB_ITERS defaults to 3 because the before arm is a float reference on a
# core without an FPU (gelu: about 2.2 s per inference at 40 MHz).

MB_BOARD_NAME="${MB_BOARD_NAME:-chipyard_pynqz1_all_f40}"
MB_BOARD_MAGIC="${MB_BOARD_MAGIC:-0x5A5A0038}"
MB_BOARD_BIT_BASENAME="${MB_BOARD_BIT_BASENAME:-pynqz1_rocket_micrgb_roccmoonnch8f40b98ball.bit}"
MB_BOARD_BIT_MD5="${MB_BOARD_BIT_MD5:-ced0aab0c7b52f25338eeffe8f678e4f}"
MB_BOARD_MTIME_HZ="${MB_BOARD_MTIME_HZ:-40000}"
MB_BOARD_FCLK_MHZ="${MB_BOARD_FCLK_MHZ:-40}"
MB_BOARD_RUNNER="${MB_BOARD_RUNNER:-run_rocket_roccmoonnch8f40b98ball.py}"
# Engine flags 0x5A5A0038 was built with (nch 8, cap 4, flat drain; see scripts/83).
MB_BOARD_ROCCMOON_CFLAGS="${MB_BOARD_ROCCMOON_CFLAGS:--falign-loops=4 -DMBXR_RT_CAP=4 -DMBXR_NCH=8 -DMBXR_RT_PLACE_EARLY=1}"
# Where the seat publishes images for the board, and where the board's results land
# (~/from-board/<board or seat>/...).
MB_PUB="${MB_PUB:-$HOME/pub/mb}"
MB_FROM_BOARD="${MB_FROM_BOARD:-$HOME/from-board}"
MB_BOARD_SAMPLE="${MB_BOARD_SAMPLE:-$IISWC_ROOT/samples/modelblaster_pext}"
MB_BOARD_ITERS="${MB_BOARD_ITERS:-3}"
MB_BOARD_PARSE="$IISWC_ROOT/scripts/lib/mb_board_parse.py"

# mb_board_cflags <target> -> Backend(<target>).resolved_kernel_cflags, plus the engine
# flags above for the roccmoon target
mb_board_cflags () {
  local cf
  cf="$( cd "$ZCS" && python -c "
import sys
from modelblaster.pipeline import backends
print(' '.join(backends.get(sys.argv[1]).resolved_kernel_cflags(sys.argv[2])))" "$1" "$ZCS/modelblaster" )" \
    || return 1
  if [ "$1" = roccmoon ]; then
    [ -n "$MB_BOARD_ROCCMOON_CFLAGS" ] || { echo "no RoCC engine flags for $MB_BOARD_MAGIC" >&2; return 1; }
    cf="$cf $MB_BOARD_ROCCMOON_CFLAGS"
  fi
  printf '%s\n' "$cf"
}

# mb_board_requires <target> -> LAB_REQUIRES for lib/feature_gate.sh
mb_board_requires () {
  case "$1" in roccmoon) echo "rocc_engine pext" ;; *) echo "pext" ;; esac
}

# mb_board_build <gen> <target> <dir> [<kernel cflags>, default: the target's]
#   west build of samples/modelblaster_pext on <gen> into <dir>/build, then the gates. Leaves
#   <dir>/{zephyr.bin,zephyr.elf,zephyr.dis,isa.txt,image.json,kernel_cflags.txt} and removes
#   <dir>/build to save disk unless MB_BOARD_KEEP_BUILD=1.
mb_board_build () {
  local gen="$1" target="$2" d="$3" cfl="${4:-}"
  need_file "$gen/kernels.c" "no generated model at $gen"
  # CMake resolves a relative MODEL_DIR against the sample's directory, so make it absolute.
  gen="$(cd "$gen" && pwd)"; mkdir -p "$d"; d="$(cd "$d" && pwd)"
  command -v west >/dev/null 2>&1 || die "west not on PATH; source ~/.config/iiswc/dev.env"
  [ -n "$cfl" ] || cfl="$(mb_board_cflags "$target")" || die "could not form the kernel cflags for target '$target'"
  mkdir -p "$d"
  rm -rf "$d/build"
  # Same flags as scripts/86_signdet_board.sh. fpga/pynq-z2/sw is on CPATH because the
  # roccmoon kernels include roccmoon/mbxr_rt.h.
  CPATH="$IISWC_ROOT/fpga/pynq-z2/sw${CPATH:+:$CPATH}" \
  run west build -p always -b "$MB_BOARD_NAME" "$MB_BOARD_SAMPLE" -d "$d/build" -- \
      -DBOARD_ROOT="$IISWC_ROOT" -DMODEL_DIR="$gen" -DMB_ITERS="$MB_BOARD_ITERS" \
      -DMB_WARMUP=1 -DMB_JOIN_TIMEOUT_S="${MB_BOARD_JOIN_TIMEOUT_S:-1800}" \
      -DMODELBLASTER_KERNEL_CFLAGS="$cfl" \
    > "$d/build.log" 2>&1 \
    || { tail -30 "$d/build.log"; die "board build failed for $gen; see $d/build.log"; }
  printf '%s\n' "$cfl" > "$d/kernel_cflags.txt"
  cp "$gen/kernel_picks.json" "$d/" 2>/dev/null || true
  mb_board_gate "$d" "$cfl"
  [ "${MB_BOARD_KEEP_BUILD:-0}" = 1 ] || rm -rf "$d/build"
}

# mb_board_gate <dir> <cflags>: scripts/30's gate() for one image, plus the mbxr ABI gate
# (lib/mbxr_abi.sh) that every engine lab runs before using the board.
mb_board_gate () {
  local d="$1" cfl="$2" cfg="$1/build/zephyr/.config" OBJDUMP READELF
  READELF="$(command -v riscv64-zephyr-elf-readelf || true)"
  OBJDUMP="$(command -v riscv64-zephyr-elf-objdump || true)"
  [ -n "$READELF" ] && [ -n "$OBJDUMP" ] || die "riscv64-zephyr-elf-{readelf,objdump} not on PATH"
  need_file "$d/build/zephyr/zephyr.bin" "the build produced no raw image"
  cp "$d/build/zephyr/zephyr.elf" "$d/build/zephyr/zephyr.bin" "$d/"
  local hz; hz=$(grep -E '^CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC=' "$cfg" | cut -d= -f2)
  [ "${hz:-0}" = "$MB_BOARD_MTIME_HZ" ] || die "$d: CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC=$hz, expected $MB_BOARD_MTIME_HZ
       (the wrong Zephyr board for $MB_BOARD_MAGIC; the console would be garbled)"
  local ncpu; ncpu=$(grep -E '^CONFIG_MP_MAX_NUM_CPUS=' "$cfg" | cut -d= -f2)
  [ "${ncpu:-1}" -ge 2 ] || die "$d: CONFIG_MP_MAX_NUM_CPUS=$ncpu (hart 1 would never run)"
  grep -q '^CONFIG_SMP=y' "$cfg" || die "$d: CONFIG_SMP is not enabled"
  grep -q '^CONFIG_MB_PEXT=y' "$cfg" || die "$d: CONFIG_MB_PEXT is not set"
  grep -q '^CONFIG_SCHED_CPU_MASK=y' "$cfg" || die "$d: CONFIG_SCHED_CPU_MASK is off (no pinning)"
  if grep -q '^CONFIG_FPU=y' "$cfg"; then die "$d: CONFIG_FPU=y against a WithoutFPU core"; fi
  {
    "$READELF" -h "$d/zephyr.elf" | grep -E 'Entry point|Flags'
    "$READELF" -A "$d/zephyr.elf" | grep Tag_RISCV_arch
  } > "$d/isa.txt"
  grep -q 'soft-float ABI' "$d/isa.txt" || { cat "$d/isa.txt"; die "$d: not a soft-float ABI build"; }
  . "$IISWC_ROOT/scripts/lib/mbxr_abi.sh"
  mbxr_abi_gate "$d/zephyr.elf" "$MB_BOARD_MAGIC" | sed 's/^/    /' | tee -a "$d/isa.txt" \
    || die "$d: guest/bitstream ABI mismatch (lib/mbxr_abi.sh)"
  "$OBJDUMP" -d "$d/zephyr.elf" > "$d/zephyr.dis"
  local mem; mem=$(grep -E '^\s+(RAM|ROM):' "$d/build.log" | tr -s ' ' | sed 's/^ //' | paste -sd';' || true)
  python3 - "$d/zephyr.dis" "$d/zephyr.bin" "$d/zephyr.elf" "$cfl" "$mem" "$d/image.json" <<'PY' \
    | tee -a "$d/isa.txt" || die "$d: image gate failed (float instructions, or a garbled MBP word)"
import json, os, re, sys
dis, binf, elf, cfl, mem, out = sys.argv[1:7]
names = ["dot8", "max8", "qmul", "clip8"]
fn, per_fn, other, fp, c1 = None, {}, 0, 0, {}
fpre = re.compile(r"\s(f(add|sub|mul|div|sqrt|mv|cvt|ld|sd|lw|sw|sgnj|min|max|eq|lt|le|class|madd|msub|nmadd|nmsub)\S*|c\.f(ld|sd|lw|sw)\S*|v(set|le|se|add|mul|mac)\S*)\s")
for line in open(dis):
    m = re.match(r"^[0-9a-f]+ <([^>]+)>:", line)
    if m:
        fn = m.group(1); continue
    m = re.match(r"^\s+[0-9a-f]+:\s+([0-9a-f]{8})\s+(\S+)", line)
    if not m:
        if re.match(r"^\s+[0-9a-f]+:\s+[0-9a-f]{4}\s+c\.f", line):
            fp += 1
        continue
    if fpre.search(" " + m.group(2) + " "):
        fp += 1
    v = int(m.group(1), 16)
    if (v & 0x7f) == 0x2b:                      # custom-1: the RoCC engine / lanes
        c1[fn] = c1.get(fn, 0) + 1
    if (v & 0x7f) != 0x0b:
        continue
    if (v >> 25) != 0 or ((v >> 12) & 7) > 3:
        other += 1; continue
    c = per_fn.setdefault(fn, {n: 0 for n in names})
    c[names[(v >> 12) & 7]] += 1
tot = lambda fns: sum(sum(c.values()) for f, c in per_fn.items() if f in fns)
neg = [f for f in per_fn if f == "neg_worker"]
kern = [f for f in per_fn if f != "neg_worker"]
r = {"bin_bytes": os.path.getsize(binf), "elf_bytes": os.path.getsize(elf),
     "kernel_cflags": cfl, "memory": mem,
     "mbp_total": tot(per_fn), "mbp_negtest": tot(neg), "mbp_outside_negtest": tot(kern),
     "mbp_by_function": per_fn, "custom0_not_mbp": other,
     "custom1_rocc_total": sum(c1.values()), "custom1_by_function": c1,
     "float_instructions": fp}
json.dump(r, open(out, "w"), indent=2)
print(f"    custom-0 MBP words: {r['mbp_total']} total, {r['mbp_negtest']} in neg_worker, "
      f"{r['mbp_outside_negtest']} elsewhere {sorted(kern) if kern else ''}")
print(f"    custom-1 RoCC words: {r['custom1_rocc_total']}"
      + (f" in {sorted(c1)}" if c1 else ""))
print(f"    float instructions: {fp}   bin {r['bin_bytes']:,} B   {mem}")
sys.exit(1 if (fp or other) else 0)
PY
}

# mb_board_build_both <run> <target> [<outdir>=<run>/board]
mb_board_build_both () {
  local run="$1" target="$2" bd="${3:-$1/board}" arm
  for arm in before after; do
    info "$arm: west build -b $MB_BOARD_NAME samples/modelblaster_pext  (MB_ITERS=$MB_BOARD_ITERS)"
    mb_board_build "$run/$arm/gen" "$target" "$bd/$arm"
  done
  # Whether the after arm uses the MBP depends on the op (gelu's memo LUT is scalar code), so
  # it is reported, not gated. The before arm (the float reference) must not use it.
  python3 -c "
import json,sys
b=json.load(open(sys.argv[1])); a=json.load(open(sys.argv[2]))
print(f'    MBP outside the negative test: before {b[\"mbp_outside_negtest\"]}, after {a[\"mbp_outside_negtest\"]}')
sys.exit(1 if b['mbp_outside_negtest'] else 0)" "$bd/before/image.json" "$bd/after/image.json" \
    || die "the before arm (the float reference) contains MBP instructions outside neg_worker"
  # If the after kernel uses the MBP, build it again with -DMB_PEXT_HW=0, where pext.h turns
  # each MBP intrinsic into a C model that matches it bit for bit. after vs mbpoff is the
  # accelerator's share of the speedup, before vs mbpoff the rewritten loop's.
  # MB_BOARD_MBPOFF=0 skips it.
  rm -rf "$bd/mbpoff"
  local nafter; nafter=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['mbp_outside_negtest'])" "$bd/after/image.json")
  if [ "${MB_BOARD_MBPOFF:-1}" = 1 ] && [ "${nafter:-0}" -gt 0 ]; then
    local cfl; cfl="$(sed 's/-DMB_PEXT_HW=1/-DMB_PEXT_HW=0/' "$bd/after/kernel_cflags.txt")"
    case "$cfl" in *MB_PEXT_HW=0*) ;; *) cfl="$cfl -DMB_PEXT_HW=0" ;; esac
    info "mbpoff: the after kernel again, with the MBP off ($cfl)"
    mb_board_build "$run/after/gen" "$target" "$bd/mbpoff" "$cfl"
    python3 -c "
import json,sys
o=json.load(open(sys.argv[1]))
print(f'    MBP outside the negative test with the accelerator off: {o[\"mbp_outside_negtest\"]}')
sys.exit(1 if o['mbp_outside_negtest'] else 0)" "$bd/mbpoff/image.json" \
      || die "the mbpoff image still contains MBP instructions: MB_PEXT_HW=0 did not reach the kernel"
  fi
}

# mb_board_features <run> <target>: lib/feature_gate.sh on this build's picks and cflags.
# Fails closed when MAGIC_FEATURES.tsv has no row for the md5 (scripts/95 checks this in its
# preflight, before any LLM call).
mb_board_features () {
  local run="$1" target="$2" bd="$1/board"
  # The gate needs fpga/pynq-z2/MAGIC_REGISTRY.md, which this repo does not ship. Without it,
  # warn and continue: the board still refuses any MAGIC but 0x5A5A0038, and
  # mb_board_collect refuses a foreign bitstream md5.
  if [ ! -f "$IISWC_ROOT/fpga/pynq-z2/MAGIC_REGISTRY.md" ]; then
    warn "feature gate SKIPPED: no fpga/pynq-z2/MAGIC_REGISTRY.md in this checkout.  The kernels
       need '$(mb_board_requires "$target")'; MAGIC_FEATURES.tsv lists 0x5A5A0038 with pext."
    echo '{"skipped": "no MAGIC_REGISTRY.md in this checkout"}' > "$bd/feature_gate.json"
    return 0
  fi
  . "$IISWC_ROOT/scripts/lib/feature_gate.sh"
  # The seat never loads a bitstream, so the gate uses the md5 the cards ship
  # (MB_BOARD_BIT_MD5). The board reports the md5 it loaded, and mb_board_collect refuses a
  # mismatch.
  BIT_MD5="$MB_BOARD_BIT_MD5" \
  LAB_REQUIRES="$(mb_board_requires "$target")" WANT_MAGIC="$MB_BOARD_MAGIC" NAME="95_mb_kernel_llm" \
  FEATURE_GATE_OUT="$bd/feature_gate.json" \
    feature_gate "$bd/after/kernel_picks.json" "$(cat "$bd/after/kernel_cflags.txt")"
}

# mb_board_publish <run> <op> <target> <name>
#   Build both arms for the card, gate them, and publish before.bin, after.bin (and
#   mbpoff.bin) with a manifest of md5s that the script on the board checks.
mb_board_publish () {
  local run="$1" op="$2" target="$3" name="$4" bd="$1/board" pub
  mkdir -p "$bd"
  mb_board_build_both "$run" "$target" "$bd"
  mb_board_features "$run" "$target" \
    || die "the feature gate refused: the kernels need features 0x5A5A0038 is not registered
       with in fpga/pynq-z2/MAGIC_FEATURES.tsv (see $bd/feature_gate.json)"
  pub="$MB_PUB/$name"
  mkdir -p "$pub"
  cp "$bd/before/zephyr.bin" "$pub/before.bin"
  cp "$bd/after/zephyr.bin" "$pub/after.bin"
  rm -f "$pub/mbpoff.bin"; [ -f "$bd/mbpoff/zephyr.bin" ] && cp "$bd/mbpoff/zephyr.bin" "$pub/mbpoff.bin"
  # Publish the board script next to the images, so a card image without mb_board_run.sh can
  # pull it with aws_pull.sh (see mb_board_command).
  cp "$IISWC_ROOT/fpga/pynq-z2/host/mb_board_run.sh" "$MB_PUB/mb_board_run.sh"
  mb_board_manifest "$pub" "$run" "$op" "$name"
}

# mb_board_manifest <pub> <run> <op> <name>: manifest.json for the images present in <pub>
mb_board_manifest () {
  local pub="$1" run="$2" op="$3" name="$4"
  python3 - "$pub" "$run" "$op" "$name" "$MB_BOARD_MAGIC" "$MB_BOARD_FCLK_MHZ" \
            "$MB_BOARD_NAME" "$MB_BOARD_ITERS" <<'PY' || die "could not write the manifest"
import hashlib, json, os, sys, time
pub, run, op, name, magic, fclk, guest, iters = sys.argv[1:9]
def md5(p): return hashlib.md5(open(p, "rb").read()).hexdigest()
tp = os.path.join(run, "after", "transcript.jsonl")
calls = sum(1 for l in open(tp) if l.strip()) if os.path.exists(tp) else 0
if os.path.isdir(os.path.join(run, "kernels_replay")):
    calls = 0                                   # replay / your kernel: no model call this run
m = {"schema": 1, "run": name, "op": op, "magic": magic, "fclk_mhz": fclk,
     "guest_board": guest, "mb_iters": int(iters), "llm_calls": calls,
     "published": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
     "images": {a: {"file": f"{a}.bin", "md5": md5(os.path.join(pub, f"{a}.bin")),
                    "bytes": os.path.getsize(os.path.join(pub, f"{a}.bin"))}
                for a in ("before", "after", "mbpoff") if os.path.exists(os.path.join(pub, f"{a}.bin"))}}
json.dump(m, open(os.path.join(pub, "manifest.json"), "w"), indent=2)
print(f"    published {pub}: " + ", ".join(f"{k} {v['md5'][:8]}" for k, v in m['images'].items()))
PY
}

# ---- --board-loop: the FPGA inside the LLM's optimization loop ----------------------------
# Each round's best kernel is built and published as <name>-r<R>: after.bin, mbpoff.bin if it
# uses the MBP, and in round 1 also before.bin (the reference). With the card agent the seat
# runs each round on the board (mb_board_remote_run); otherwise `mb go` on the board pulls
# each round as it appears and pushes the consoles back. The FPGA cycles go into the next
# round's system prompt, and the final pick is the kernel fastest on the FPGA, not on spike.

# mb_board_round <run> <target> <R> <name> <op>
mb_board_round () {
  local run="$1" target="$2" r="$3" name="$4" op="$5" bd="$1/board.r$3" pub nafter cfl
  rm -rf "$bd"; mkdir -p "$bd"
  [ "$r" = 1 ] && mb_board_build "$run/before/gen" "$target" "$bd/before"
  mb_board_build "$run/after/gen" "$target" "$bd/after"
  nafter=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['mbp_outside_negtest'])" "$bd/after/image.json")
  if [ "${MB_BOARD_MBPOFF:-1}" = 1 ] && [ "${nafter:-0}" -gt 0 ]; then
    cfl="$(sed 's/-DMB_PEXT_HW=1/-DMB_PEXT_HW=0/' "$bd/after/kernel_cflags.txt")"
    case "$cfl" in *MB_PEXT_HW=0*) ;; *) cfl="$cfl -DMB_PEXT_HW=0" ;; esac
    mb_board_build "$run/after/gen" "$target" "$bd/mbpoff" "$cfl"
  fi
  pub="$MB_PUB/$name-r$r"; rm -rf "$pub"; mkdir -p "$pub"
  local a; for a in before after mbpoff; do [ -f "$bd/$a/zephyr.bin" ] && cp "$bd/$a/zephyr.bin" "$pub/$a.bin"; done
  cp "$IISWC_ROOT/fpga/pynq-z2/host/mb_board_run.sh" "$MB_PUB/mb_board_run.sh"
  mb_board_manifest "$pub" "$run" "$op" "$name-r$r"
}

# mb_board_round_wait <run> <R> <name> <timeout s> -> the from-board dir, once every console
# the round published has come back
mb_board_round_wait () {
  local run="$1" r="$2" name="$3" limit="$4" t0 d a ok
  t0=$(date +%s)
  while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
    for d in "$MB_FROM_BOARD"/*/mb/"$name-r$r"; do
      [ -d "$d" ] || continue
      ok=1
      for a in before after mbpoff; do
        [ -f "$MB_PUB/$name-r$r/$a.bin" ] && [ ! -f "$d/console_$a.txt" ] && ok=0
      done
      [ "$ok" = 1 ] && { echo "$d"; return 0; }
    done
    sleep 3
  done
  return 1
}

# mb_board_round_record <run> <op> <R> <src> <spike best> <kernel file>
#   Appends the FPGA's numbers for round R to <run>/board_rounds.jsonl and rewrites the
#   feedback the next round's LLM calls see (<run>/after/board_feedback.md).
mb_board_round_record () {
  python3 - "$@" "$MB_BOARD_PARSE" <<'PY'
import json, os, subprocess, sys
run, op, r, src, spike_best, kfile, parse = sys.argv[1:8]
r = int(r)
def arm(a):
    p = os.path.join(src, f"console_{a}.txt")
    if not os.path.exists(p):
        return None
    g = os.path.join(run, "before" if a == "before" else "after", "gen", "test_golden.bin")
    out = subprocess.run([sys.executable, parse, "console", p, "--op", op, "--golden", g],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)
b, f, o = arm("before"), arm("after"), arm("mbpoff")
exact = lambda x: bool(x and x.get("run") and x["run"].get("max_abs_err") == 0
                       and x.get("host_max_abs_err", 0) in (0, None))
rec = {"round": r, "src": src, "kernel": kfile, "spike_cycles": int(float(spike_best)),
       "after_cycles": f and f.get("op_cycles"), "after_exact": exact(f),
       "mbpoff_cycles": o and o.get("op_cycles"), "n_out": f and f.get("out_len")}
if b:
    rec["before_cycles"] = b.get("op_cycles")
rows = [json.loads(l) for l in open(os.path.join(run, "board_rounds.jsonl"))] \
    if os.path.exists(os.path.join(run, "board_rounds.jsonl")) else []
rows = [x for x in rows if x["round"] != r] + [rec]
with open(os.path.join(run, "board_rounds.jsonl"), "w") as fh:
    for x in rows:
        fh.write(json.dumps(x) + "\n")
ref = next((x.get("before_cycles") for x in rows if x.get("before_cycles")), None)
n = rec["n_out"] or 1
L = ["### Hardware in the loop feedback: cycles measured on the FPGA",
     "",
     "Every round, the best kernel so far is also run on the actual board: a Rocket RV64 core at "
     "40 MHz with the MBP extension, cycles by rdcycle.  Spike, which scores your candidates, charges "
     "one cycle per instruction and has no memory timing; the board pays real latency for every "
     "load and store, so a kernel with fewer, wider memory accesses gains more on the board than "
     "spike shows.  Optimize for the board's numbers below.", ""]
if ref:
    L.append(f"- reference kernel on the FPGA: {ref:,} cycles ({ref / n:.1f} per output)")
for x in sorted(rows, key=lambda x: x["round"]):
    if not x.get("after_cycles"):
        continue
    s = (f"- round {x['round']}'s best kernel: {x['after_cycles']:,} cycles on the FPGA "
         f"({x['after_cycles'] / n:.1f} per output"
         + ((f", {ref / x['after_cycles']:.1f}x faster than the reference" if ref / x['after_cycles'] >= 1
             else f", SLOWER than the reference ({ref / x['after_cycles']:.2f}x)") if ref else "")
         + f"); spike said {x['spike_cycles']:,}")
    if x.get("mbpoff_cycles"):
        s += (f"; with the MBP instructions replaced by software it takes {x['mbpoff_cycles']:,}, "
              f"so the MBP gives it {x['mbpoff_cycles'] / x['after_cycles']:.1f}x")
    elif x.get("after_cycles"):
        s += "; it uses NO MBP instruction"
    if not x.get("after_exact"):
        s += "; WRONG OUTPUT on the board"
    L.append(s + ".")
open(os.path.join(run, "after", "board_feedback.md"), "w").write("\n".join(L) + "\n")
print(f"    FPGA, round {r}: {rec['after_cycles'] or 0:,} cycles ({(rec['after_cycles'] or 0) / n:.1f}/output)"
      + (f", MBP off {rec['mbpoff_cycles']:,}" if rec.get('mbpoff_cycles') else "")
      + ("" if rec["after_exact"] else "  NOT BIT-EXACT"))
PY
}

# ---- board agent, through the card's reverse tunnel ----------------------------------------
# Each card keeps a reverse ssh tunnel to its seat (iiswc-tunnel.service: seat localhost:19022
# -> board sshd). The board answers with a forced command, /opt/iiswc/host/tunnel_agent.sh:
# a fixed set of verbs (ping, put, run, get, ...), JSON replies, and root only through the
# card's tunnel_priv.sh. The seat holds the agent key, ~/.ssh/iiswc-board-agent. Per image the
# lab does put zephyr.bin, run zephyr, get console.out.
MB_BOARD_AGENT_PORT="${MB_BOARD_AGENT_PORT:-19022}"
MB_BOARD_AGENT_KEY="${MB_BOARD_AGENT_KEY:-$HOME/.ssh/iiswc-board-agent}"

mb_board_agent_on () {   # true if this machine holds the agent key and MB_BOARD_NO_AGENT is unset
  [ -z "${MB_BOARD_NO_AGENT:-}" ] && [ -r "$MB_BOARD_AGENT_KEY" ]
}

mb_board_agent () {   # "<verb> [args]" -> the agent's reply (stdin is the upload for `put`)
  [ -n "${MB_CMD_LOG:-}" ] && printf 'ssh -p %s -i %q xilinx@localhost %q\n' "$MB_BOARD_AGENT_PORT" "$MB_BOARD_AGENT_KEY" "$1" >> "$MB_CMD_LOG"
  ssh -p "$MB_BOARD_AGENT_PORT" -i "$MB_BOARD_AGENT_KEY" -o BatchMode=yes -o IdentitiesOnly=yes \
      -o ConnectTimeout=15 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o LogLevel=ERROR xilinx@localhost "$1"
}

mb_board_agent_host () {   # the board's hostname if it answers `ping`, else nothing
  mb_board_agent ping < /dev/null 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin); print(d["host"] if d.get("ok") else "")
except Exception:
    pass'
}

# mb_board_remote_run <name>: run every image published as <name> on the board and leave the
# consoles in $MB_FROM_BOARD/<board>/mb/<name>/.
mb_board_remote_run () {
  local name="$1" pub="$MB_PUB/$1" board dest arm f n md5 try
  [ -f "$pub/manifest.json" ] || die "nothing published as $name"
  # One run per board at a time, or two runs' put/run/get overwrite each other's image and
  # console. mb_by_hand.ipynb takes the same lock.
  exec 9>>"$HOME/.mb-board.lock"
  if ! flock -n 9; then
    info "your board is busy with another run on this seat: waiting for it"
    type status >/dev/null 2>&1 && status running "waiting: your board is busy with another run" || true
    flock -w "${MB_BOARD_LOCK_WAIT:-1800}" 9 || die "the board stayed busy for $(( ${MB_BOARD_LOCK_WAIT:-1800} / 60 )) min"
    type status >/dev/null 2>&1 && status running "${CUR_STEP:-6/7  the board runs the images}" || true
  fi
  board="$(mb_board_agent_host)"
  [ -n "$board" ] || die "the board does not answer through its tunnel (this seat's port $MB_BOARD_AGENT_PORT)"
  dest="$MB_FROM_BOARD/$board/mb/$name"; rm -rf "$dest"; mkdir -p "$dest"; : > "$dest/boot.log"
  for arm in before after mbpoff; do
    f="$pub/$arm.bin"; [ -f "$f" ] || continue
    n=$(stat -c %s "$f"); md5=$(md5sum < "$f" | cut -d' ' -f1)
    mb_board_agent "put zephyr.bin $n $md5" < "$f" >> "$RUN/board_agent.log" 2>&1 \
      || { tail -3 "$RUN/board_agent.log"; die "could not upload the $arm image to $board"; }
    # A console without the RESULT line is an incomplete capture (dropped bytes), not a wrong
    # kernel, so run the image once more.
    for try in 1 2; do
      if mb_board_agent "run zephyr" < /dev/null >> "$RUN/board_agent.log" 2>&1; then
        mb_board_agent "get console.out" < /dev/null | tr -d '\r' > "$dest/console_$arm.txt"
        mb_board_agent "get run.log" < /dev/null >> "$dest/boot.log" 2>/dev/null || true
        grep -aq '^RESULT:' "$dest/console_$arm.txt" && break
      fi
      [ "$try" = 2 ] && { tail -3 "$RUN/board_agent.log"; die "the $arm image did not complete on $board; see $RUN/board_agent.log"; }
    done
  done
  exec 9>&-
}

# mb_board_find <name> -> the from-board directory holding this run's consoles
# ($MB_FROM_BOARD/<board or seat>/mb/<name>/), or nothing. Any subdirectory is accepted: the
# agent files runs under the board's hostname, aws_push under the seat's, and an attendee may
# have used a neighbour's board.
mb_board_find () {
  local d
  for d in "$MB_FROM_BOARD"/*/mb/"$1"; do
    [ -f "$d/console_before.txt" ] && [ -f "$d/console_after.txt" ] || continue
    # If an mbpoff image was published, its console is required too.
    [ ! -f "$MB_PUB/$1/mbpoff.bin" ] || [ -f "$d/console_mbpoff.txt" ] || continue
    echo "$d"; return 0
  done
  return 1
}

# mb_board_collect <run> <op> <name> -> <run>/board.json; returns 1 unless both arms are
# bit-exact on the board and MAGIC matched.
mb_board_collect () {
  local run="$1" op="$2" name="$3" bd="$1/board" src seat md5
  src="$(mb_board_find "$name")" || die "no consoles for $name under $MB_FROM_BOARD/*/mb/ yet"
  seat="$(basename "$(dirname "$(dirname "$src")")")"
  mkdir -p "$bd/before" "$bd/after"
  cp "$src/console_before.txt" "$bd/before/console.txt"
  cp "$src/console_after.txt" "$bd/after/console.txt"
  [ -f "$src/console_mbpoff.txt" ] && [ -d "$bd/mbpoff" ] && cp "$src/console_mbpoff.txt" "$bd/mbpoff/console.txt"
  [ -f "$src/boot.log" ] && cp "$src/boot.log" "$bd/boot.log"
  md5="$(cat "$src/bitstream.md5" 2>/dev/null || echo "not-loaded:card-boot-image")"
  case "$md5" in
    "$MB_BOARD_BIT_MD5"|not-loaded:*) ;;
    *) die "the board loaded a bitstream with md5 $md5, not the card's $MB_BOARD_BIT_MD5;
       its numbers are not a measurement of $MB_BOARD_MAGIC" ;;
  esac
  info "board results from $seat ($src)"
  python3 "$MB_BOARD_PARSE" board --run "$run" --op "$op" --board-dir "$bd" \
      --bit "card:$MB_BOARD_BIT_BASENAME" --bit-md5 "$md5" \
      --want-magic "$MB_BOARD_MAGIC" --mtime-hz "$MB_BOARD_MTIME_HZ" \
      --iters "$MB_BOARD_ITERS" --board "$seat" --guest-board "$MB_BOARD_NAME"
}

# mb_board_command <name> -> the line the attendee pastes on their board. It works on any card
# with /opt/iiswc/host/aws_*.sh: it finds the board's seat, pulls the board script published
# next to the images, and runs it.
mb_board_command () {
  printf '%s\n' "S=\$(/opt/iiswc/host/aws_whoami.sh --name) && /opt/iiswc/host/aws_pull.sh \$S pub/mb/mb_board_run.sh ~/mb_board_run.sh >/dev/null && bash ~/mb_board_run.sh $1 \$S"
}
