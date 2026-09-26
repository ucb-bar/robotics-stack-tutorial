#!/usr/bin/env bash
# Source me:  source env.sh
#
# Single source of truth for every path the tutorial scripts use. Each value can be
# overridden by exporting it before sourcing (useful for pointing at a prebuilt spike
# or a decoder that lives outside the repo).

IISWC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export IISWC_ROOT
export ZCS="${ZCS:-$IISWC_ROOT/zephyr-chipyard-sw}"
export IISWC_OUT="${IISWC_OUT:-$IISWC_ROOT/out}"

# --- Chipyard ------------------------------------------------------------------------
#
# TWO SEPARATE THINGS, AND ONLY ONE OF THEM NEEDS CHIPYARD.
#
#   Building a bitstream needs the GENERATED VERILOG. That is vendored in this repo for
#   every pinned config (fpga/pynq-z2/chipyard/gensrc/, ~1 MB each) and unpacked by
#   scripts/08_gensrc.sh into $CHIPYARD_GENSRC_ROOT. No Chipyard install required.
#
#   Changing the SoC config needs a real CHIPYARD TREE -- hours to install, tens of GB.
#   Set CHIPYARD_DIR to point at one. There is deliberately NO default: a hard-coded path
#   into somebody's scratch directory is exactly what made this repo unbuildable elsewhere.
#   Scripts that need it (scripts/07_patch_rocketchip.sh, scripts/08_gensrc.sh --pack)
#   fail with an instruction rather than silently reading the wrong tree.
#
# Anything this repo needs changed inside Chipyard lives in patches/ and is applied by
# scripts/07_patch_rocketchip.sh; the vendored bundles record which patches they were
# elaborated with. See docs/REPRODUCING.md.
export CHIPYARD_DIR="${CHIPYARD_DIR:-${CHIPYARD_ROOT:-}}"
export CHIPYARD_ROOT="$CHIPYARD_DIR"          # older name, kept for scripts that use it
export CHIPYARD_GENSRC_ROOT="${CHIPYARD_GENSRC_ROOT:-$IISWC_OUT/gensrc}"

# TACIT toolchain. Defaults point at the in-repo submodule builds produced by
# scripts/05_build_tacit_tools.sh.
export TACIT_SPIKE="${TACIT_SPIKE:-$IISWC_ROOT/third_party/riscv-isa-sim/build/spike}"
export TACIT_DECODER="${TACIT_DECODER:-$IISWC_ROOT/third_party/tacit-decoder/target/release/ltrace-decoder}"

# Zephyr build environment (conda env + SDK live inside the zephyr-chipyard-sw submodule,
# installed or symlinked by scripts/00_bootstrap.sh).
if [ -f "$ZCS/tools/miniforge3/etc/profile.d/conda.sh" ]; then
  # shellcheck disable=SC1091
  . "$ZCS/tools/miniforge3/etc/profile.d/conda.sh"
  # conda's activate/deactivate hooks reference unset variables (e.g. Chipyard's
  # deactivate-riscv-tools.sh reads CONDA_BACKUP_RISCV when this is sourced from a shell that
  # already sourced Chipyard's env.sh). Under a caller's `set -u` that kills the shell outright
  # -- `|| true` cannot catch it and 2>/dev/null hides why -- so every script sourcing
  # scripts/lib/common.sh exited 1 with no output. Relax nounset for the activation only.
  case $- in *u*) _iiswc_nounset=1; set +u ;; *) _iiswc_nounset=0 ;; esac
  conda activate zephyr 2>/dev/null || true
  [ "$_iiswc_nounset" = 1 ] && set -u
  unset _iiswc_nounset
fi
if [ -f "$ZCS/scripts/set_envvars_sdk.sh" ]; then
  # shellcheck disable=SC1091
  . "$ZCS/scripts/set_envvars_sdk.sh" >/dev/null 2>&1
fi

# west must come from the conda env, not a system python.
if [ -d "$ZCS/tools/miniforge3/envs/zephyr/bin" ]; then
  export PATH="$ZCS/tools/miniforge3/envs/zephyr/bin:$PATH"
  export WEST_PYTHON="$ZCS/tools/miniforge3/envs/zephyr/bin/python"
fi

# --- which board ---------------------------------------------------------------------
#
# THERE IS DELIBERATELY NO DEFAULT HOST HERE, for the same reason CHIPYARD_DIR has none.
# Until 2026-09-21 this line carried a baked-in default: the address of one particular
# board on one particular bench. That is right on exactly one machine. Somewhere else it is either
# nothing at all or, on a shared segment, SOMEBODY ELSE'S board, and a lab that downloads a
# bitstream into a stranger's PL is a worse failure than one that refuses to start.
#
# Resolution order:
#   1. $PYNQ_HOST already exported          (always wins -- per-command overrides still work)
#   2. board.conf in the repo root          (per-machine, untracked; see board.conf.example)
#      or $IISWC_BOARD_CONF if you point it elsewhere
#   3. unset -- and scripts/lib/board_id.sh's require_pynq_host() fails with an instruction
#
# A checkout that always drives the same board puts it in board.conf; nothing is baked in here.
IISWC_BOARD_CONF="${IISWC_BOARD_CONF:-$IISWC_ROOT/board.conf}"
if [ -z "${PYNQ_HOST:-}" ] && [ -f "$IISWC_BOARD_CONF" ]; then
  # shellcheck disable=SC1090
  . "$IISWC_BOARD_CONF"
fi
export IISWC_BOARD_CONF
export PYNQ_HOST="${PYNQ_HOST:-}"
# Staging directory on the BOARD, not on this host: /home/xilinx exists on every stock PYNQ
# v3.1.1 image, so this default is a property of the image and travels fine.
export PYNQ_DIR="${PYNQ_DIR:-/home/xilinx/tutorial}"
# Non-interactive ssh does not source /etc/profile.d, and pynq lives in a venv that also
# needs XRT's environment -- without both, pynq.Bitstream raises "No Devices Found".
export PYNQ_ENV="source /etc/profile.d/xrt_setup.sh 2>/dev/null; source /usr/local/share/pynq-venv/bin/activate;"

# --- Bedrock key for BACKEND=llm -----------------------------------------------------
#
# ModelBlaster's LLM backend reads AWS_BEARER_TOKEN_BEDROCK, AWS_REGION, MODEL and
# MODELBLASTER_MAX_USD. They come from an untracked file with mode 600 that
# `scripts/93_bedrock_key.sh distribute` installs on each seat. Without it they stay unset
# and only BACKEND=llm is unavailable.
#
# Resolution order, as for board.conf above:
#   1. AWS_BEARER_TOKEN_BEDROCK already exported
#   2. $IISWC_BEDROCK_ENV, default ~/.config/iiswc/bedrock.env
#   3. unset; scripts/94_bedrock_check.sh reports what is missing
IISWC_BEDROCK_ENV="${IISWC_BEDROCK_ENV:-$HOME/.config/iiswc/bedrock.env}"
if [ -z "${AWS_BEARER_TOKEN_BEDROCK:-}" ] && [ -f "$IISWC_BEDROCK_ENV" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$IISWC_BEDROCK_ENV"
  set +a
fi
export IISWC_BEDROCK_ENV

mkdir -p "$IISWC_OUT"
