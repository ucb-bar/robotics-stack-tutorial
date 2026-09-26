#!/usr/bin/env bash
# Verify everything the labs need, and say precisely what to run when something is missing.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

fails=0
ok()   { printf '  \033[1;32mok\033[0m    %-22s %s\n' "$1" "${2-}"; }
bad()  { printf '  \033[1;31mMISS\033[0m  %-22s %s\n' "$1" "${2-}"; fails=$((fails+1)); }

step "Toolchain"
if command -v west >/dev/null 2>&1; then ok "west" "$(command -v west)"; else bad "west" "run scripts/00_bootstrap.sh"; fi
if [ -n "${ZEPHYR_BASE:-}" ] && [ -f "$ZEPHYR_BASE/VERSION" ]; then ok "ZEPHYR_BASE" "$ZEPHYR_BASE"
else bad "ZEPHYR_BASE" "zephyr_ws/zephyr not checked out"; fi
if [ -d "${ZEPHYR_SDK_INSTALL_DIR:-/nonexistent}" ]; then ok "Zephyr SDK" "$ZEPHYR_SDK_INSTALL_DIR"
else bad "Zephyr SDK" "run scripts/00_bootstrap.sh"; fi
if command -v cmake >/dev/null 2>&1; then ok "cmake" "$(cmake --version | head -1)"; else bad "cmake"; fi
if command -v dtc >/dev/null 2>&1; then ok "dtc" "$(command -v dtc)"; else bad "dtc" "spike needs the device-tree compiler"; fi

step "TACIT"
if [ -x "$TACIT_SPIKE" ]; then ok "spike" "$TACIT_SPIKE"; else bad "spike" "run scripts/05_build_tacit_tools.sh"; fi
if [ -x "$TACIT_DECODER" ]; then ok "decoder" "$TACIT_DECODER"; else bad "decoder" "run scripts/05_build_tacit_tools.sh"; fi
# MBP (patches/0006 and 0007). A toolchain built before those is a perfectly good TACIT
# toolchain that simply cannot run or read the packed-SIMD ops, and the symptom without
# this check is an illegal-instruction trap inside a guest, which names nothing. Probe the
# BINARIES, not the source tree: what was built is what will run.
if [ -x "$TACIT_SPIKE" ]; then
  if echo 'DASM(00b5068b)' | "$(dirname "$TACIT_SPIKE")/spike-dasm" 2>/dev/null | grep -q '^mbp\.dot8'
  then ok "spike MBP" "custom-0 packed-SIMD ops present (Lab A2)"
  else bad "spike MBP" "stale build -- run scripts/05_build_tacit_tools.sh --spike"; fi
fi
if [ -x "$TACIT_DECODER" ]; then
  if grep -qa 'mbp\.dot8' "$TACIT_DECODER"
  then ok "decoder MBP" "names mbp.* in trace.txt instead of unknown"
  else bad "decoder MBP" "stale build -- run scripts/05_build_tacit_tools.sh --decoder"; fi
  # Multi-hart merging (patches/0010). Without it Labs B7 and B11 decode fine but every
  # event claims pid 0 / tid 0, so the two harts cannot be put in one file -- and the
  # symptom is a `--trace` argument the decoder rejects, mid-lab, after the board run.
  if "$TACIT_DECODER" --help 2>/dev/null | grep -q -- '--trace '
  then ok "decoder multi-hart" "--trace FILE:HART:LABEL, one named track per hart (Labs B7/B11)"
  else bad "decoder multi-hart" "stale build -- run scripts/05_build_tacit_tools.sh --decoder"; fi
fi
# The kernel REVISION, asserted and not merely printed. This check is here because the doctor
# used to print a submodule's short HEAD and check nothing, so a tree at the wrong revision
# passed the doctor and failed in the lab -- and the wrong revision is the DEFAULT: the nested
# gitlink points at riskybirdv3-bringup's tip, four commits off the branch it declares, where
# patches/0120 cannot apply. See deps.lock's zephyr_ws/zephyr entry.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/deps_lock.sh"
_zrev_want="$(deps_pin zephyr_kernel)"
_zrev_have="$(git -C "$ZCS/zephyr_ws/zephyr" rev-parse HEAD 2>/dev/null || true)"
if [ -z "$_zrev_want" ]; then
  bad "zephyr kernel rev" "no 'pin zephyr_kernel' in deps.lock -- the revision is unchecked"
elif [ -z "$_zrev_have" ]; then
  bad "zephyr kernel rev" "zephyr_ws/zephyr not checked out -- run scripts/00_bootstrap.sh"
elif [ "$_zrev_have" = "$_zrev_want" ]; then
  ok "zephyr kernel rev" "${_zrev_want:0:12}  (deps.lock: pin zephyr_kernel)"
else
  bad "zephyr kernel rev" "at ${_zrev_have:0:12}, pinned ${_zrev_want:0:12} -- run scripts/00_bootstrap.sh
        (patches/0120 does not apply at any other revision; see deps.lock)"
fi
# The kernel checkout is not tracked by this repo, so its patch is applied by a script and
# can silently be missing on a tree someone re-cloned by hand.
if grep -q 'CONFIG_STARTUP_TACIT_SINK_DMA_ADDR' "$ZCS/zephyr_ws/zephyr/arch/riscv/core/reset.S" 2>/dev/null
then ok "zephyr patches" "reset.S programs the TACIT sink + target"
else bad "zephyr patches" "run scripts/06_patch_zephyr.sh"; fi
# The ModelBlaster backend patch (patches/0009, a `pext` target for the MBP kernels) is
# applied to the submodule's WORKING TREE, not to its pin, so it is absent on a fresh
# checkout by design. Lab B8's script applies it idempotently on every run, which is why
# this reports rather than accuses -- nobody who is not running that lab needs it.
if grep -q '"pext"' "$ZCS/modelblaster/pipeline/backends.py" 2>/dev/null
then ok "modelblaster MBP patch" "pext backend + curated algorithms (Lab B8)"
else ok "modelblaster MBP patch" "not applied -- scripts/28_rocket_modelblaster_pext.sh applies it"; fi
# One level further down, and NOT a failure when it is absent. The rocket-chip patch
# (mcycle free-running through wfi, patches/0004) is applied to a real Chipyard tree, which
# most people running these labs will not have -- building a bitstream uses the vendored
# generated Verilog instead. So: report it when CHIPYARD_DIR points somewhere, and say
# nothing accusatory when it does not.
if [ -z "${CHIPYARD_DIR:-}" ]; then
  ok "chipyard tree" "CHIPYARD_DIR unset -- only needed to re-elaborate the RTL"
elif [ ! -f "$CHIPYARD_DIR/generators/rocket-chip/src/main/scala/rocket/CSR.scala" ]; then
  bad "chipyard tree" "CHIPYARD_DIR=$CHIPYARD_DIR has no generators/rocket-chip"
elif grep -q 'TACIT-MULTICORE-MCYCLE-FREE-RUN' \
     "$CHIPYARD_DIR/generators/rocket-chip/src/main/scala/rocket/CSR.scala" 2>/dev/null
then ok "rocket-chip patches" "mcycle free-runs through wfi (Lab B7)"
else bad "rocket-chip patches" "run scripts/07_patch_rocketchip.sh"; fi

# The second rocket-chip patch (MBP packed SIMD in the ALU, patches/0008) is independent of
# the first and is only needed for Lab A3, so it is reported the same way: informative when
# CHIPYARD_DIR points at a tree, silent when it does not.
if [ -n "${CHIPYARD_DIR:-}" ] && \
   [ -f "$CHIPYARD_DIR/generators/rocket-chip/src/main/scala/rocket/ALU.scala" ]; then
  if grep -q 'MBP packed SIMD is defined on RV64 only' \
       "$CHIPYARD_DIR/generators/rocket-chip/src/main/scala/rocket/ALU.scala" 2>/dev/null
  then ok "rocket-chip MBP patch" "DOT8/MAX8/QMUL/CLIP8 in the ALU (Lab A3)"
  else bad "rocket-chip MBP patch" "run scripts/09_patch_rocket_pext.sh"; fi
fi

step "Submodules"
for sm in zephyr-chipyard-sw third_party/tacit-decoder third_party/riscv-isa-sim; do
  if [ -n "$(ls -A "$IISWC_ROOT/$sm" 2>/dev/null)" ]; then
    ok "$sm" "$(git -C "$IISWC_ROOT/$sm" rev-parse --short HEAD 2>/dev/null)"
  else bad "$sm" "git submodule update --init $sm"; fi
done

step "Board"
# Local checks only -- the doctor never opens an ssh connection, so it is safe to run with
# no board attached and it can never take the board lock. Labs A and A2 need no board at
# all, so an unset PYNQ_HOST is reported, not failed.
if [ -n "${PYNQ_HOST:-}" ]; then
  if [ -f "$IISWC_BOARD_CONF" ] && grep -q '^[[:space:]]*PYNQ_HOST=' "$IISWC_BOARD_CONF" 2>/dev/null; then
    ok "PYNQ_HOST" "$PYNQ_HOST  (from $(basename "$IISWC_BOARD_CONF"))"
  else
    ok "PYNQ_HOST" "$PYNQ_HOST  (from the environment)"
  fi
  if [ -n "${IISWC_BOARD:-}" ]; then ok "board name" "$IISWC_BOARD"
  else bad "board name" "PYNQ_HOST is not in fpga/pynq-z2/bwlab/boards.csv -- set IISWC_BOARD=<short-name>"; fi
else
  ok "PYNQ_HOST" "unset -- board labs will refuse with an instruction; Labs A/A2 need no board"
  info "      to drive a board:  cp board.conf.example board.conf  &&  \$EDITOR board.conf"
fi

step "Bitstreams"
# The one artefact nobody on the tutorial path can regenerate: no Vivado, no Chipyard, no
# licence. Checks presence AND md5 against fpga/pynq-z2/bitstreams.csv; rows marked
# 'untracked' are not required here (see the "Bitstreams" section of README.md).
_bs_out="$("$IISWC_ROOT/scripts/check_bitstreams.sh" 2>&1)" && _bs_rc=0 || _bs_rc=$?
if [ "$_bs_rc" -eq 0 ]; then
  ok "bitstreams" "$(printf '%s\n' "$_bs_out" | grep -c '^  .*ok ') verified against fpga/pynq-z2/bitstreams.csv"
else
  printf '%s\n' "$_bs_out" | grep -E 'MISSING|WRONG|have |want ' || true
  bad "bitstreams" "scripts/check_bitstreams.sh"
fi

# Every check above greps a patched file for a marker, which proves a patch went IN. None
# of them can see an edit made ON TOP of an applied patch: that still passes the marker
# grep and still passes `git apply --reverse --check`, while making the tree something a
# fresh checkout cannot reproduce. 02_verify_patches.sh reconstructs each tree from its
# pinned base plus the tracked patches and demands byte-equality, which is the only check
# that catches it.
step "Patch fidelity"
_pf_out="$("$IISWC_ROOT/scripts/02_verify_patches.sh" --quiet 2>&1)" && _pf_rc=0 || _pf_rc=$?
# Drop the sub-script's own banner and trailing blank line; keep its per-tree verdicts,
# which are already in this script's ok/bad format.
printf '%s\n' "$_pf_out" | grep -v '==>' | grep -v '^[[:space:]]*$' || true
[ "$_pf_rc" -eq 0 ] || fails=$((fails+1))

# Only BACKEND=llm needs the key, so a missing key is reported, not failed. A configured key
# that does not answer (revoked, expired, no egress) counts as a failure.
step "Bedrock (BACKEND=llm only)"
if [ -n "${AWS_BEARER_TOKEN_BEDROCK:-}" ]; then
  _br_out="$("$IISWC_ROOT/scripts/94_bedrock_check.sh" 2>&1)" && _br_rc=0 || _br_rc=$?
  printf '%s\n' "$_br_out"
  [ "$_br_rc" -eq 0 ] || fails=$((fails+1))
else
  printf '  \033[2m--\033[0m    %-22s %s\n' "bedrock key" "not configured ($IISWC_BEDROCK_ENV); only needed for BACKEND=llm (docs/BEDROCK.md)"
fi

echo
if [ "$fails" -eq 0 ]; then printf '\033[1;32mAll checks passed.\033[0m  Try: scripts/10_tacit_hello.sh\n'; exit 0
else printf '\033[1;31m%d check(s) failed.\033[0m\n' "$fails"; exit 1; fi
