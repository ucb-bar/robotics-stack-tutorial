#!/usr/bin/env bash
# Install what scripts/95_mb_kernel_llm.sh needs on an AWS seat, then check it there.
#
#   scripts/96_seat_mb_setup.sh --seat ubuntu@<seat IP> --ssh-key <seat key>
#   scripts/96_seat_mb_setup.sh --seat ubuntu@aws-N.iiswc --ssh-key K --check     # report only
#   scripts/96_seat_mb_setup.sh --seats FILE --ssh-key K                          # every seat
#   other options: --no-smoke (skip the reference run at the end), --jobs N (seats at once with --seats)
#
# The seat AMI has XPU-RT and a source checkout of zephyr-chipyard-sw, but no spike, west,
# Zephyr SDK or torch, so the lab cannot build a kernel there. This script copies a minimal
# prebuilt toolchain from a machine whose dev.env points at a built tree, and writes the
# seat's ~/.config/iiswc/dev.env. The lab then runs on the seat as
#     cd ~/iiswc-tutorial && ./scripts/95_mb_kernel_llm.sh --op maxpool2d_s8
# Run it on one seat and snapshot the AMI, or on all seats with --seats. It is idempotent
# (rsync; pip installs only what is missing).
#
# Copied into ~/mb-tools on the seat (about 1.9 GB):
#   zephyr-chipyard-sw  sources incl. the patched modelblaster, without .git, tools/ (the 7 GB
#                       conda env) and the zephyr_ws symlinks that point out of the tree
#   .../tools-manual/zephyr-sdk-1.0.0-beta1   only riscv64-zephyr-elf, the SDK's cmake/ and
#                       version files, at the same path as on the source machine so that
#                       scripts/set_envvars_sdk.sh (sourced by env.sh) finds it
#   modules/picolibc    the only Zephyr module these builds use; ZEPHYR_MODULES points at it
#                       (setting ZEPHYR_MODULES skips west's module discovery; the board
#                       images are identical, byte for byte, to the source machine's)
#   bin/spike           the TACIT spike (MBP opcodes); static apart from libc/libstdc++
# The tutorial repo goes to ~/iiswc-tutorial (no out/, .git or local config).
# On the seat, a venv gets CPU torch and what Zephyr and ModelBlaster import, pinned to the
# versions of the source machine's env. The Bedrock key is handled by
# 93_bedrock_key.sh distribute; the check step reports whether it is present.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SEAT=""; SEATS=""; KEY=""; CHECK=0; SMOKE=1; JOBS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --seat)     SEAT="${2:?}"; shift 2 ;;
    --seats)    SEATS="${2:?}"; shift 2 ;;
    --ssh-key)  KEY="${2:?}"; shift 2 ;;
    --check)    CHECK=1; shift ;;
    --no-smoke) SMOKE=0; shift ;;
    --jobs)     JOBS="${2:?}"; shift 2 ;;     # with --seats: this many seats at once
    -h|--help)  sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[ -n "$SEAT$SEATS" ] || die "which seat?  --seat ubuntu@aws-N.iiswc  (or --seats FILE)"
if [ -n "$SEATS" ]; then
  need_file "$SEATS"
  # Set up --jobs N seats at a time, each with its own log, and print one line per seat.
  LOGD="$IISWC_OUT/seat_setup/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$LOGD"
  export MB_SEAT_ARGS="${KEY:+--ssh-key $KEY} $([ "$CHECK" = 1 ] && echo --check) $([ "$SMOKE" = 0 ] && echo --no-smoke)"
  info "$(sed -e 's/#.*//' "$SEATS" | awk 'NF' | wc -l) seats, $JOBS at a time; logs in $LOGD"
  sed -e 's/#.*//' "$SEATS" | awk 'NF{print $1}' | xargs -P "$JOBS" -I{} sh -c \
    '"$0" --seat "$1" $MB_SEAT_ARGS > "$2/$(echo "$1" | tr "@/" "__").log" 2>&1 \
       && echo "ok      $1" || echo "FAILED  $1   ($2/$(echo "$1" | tr "@/" "__").log)"' "$0" {} "$LOGD" \
    | sort -k2 | tee "$LOGD/summary.txt"
  ! grep -q '^FAILED' "$LOGD/summary.txt"
  exit $?
fi

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new ${KEY:+-i "$KEY" -o IdentitiesOnly=yes})
RSYNC_E="ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new${KEY:+ -i $KEY -o IdentitiesOnly=yes}"
R() { "${SSH[@]}" "$SEAT" "$@"; }

SDKV=zephyr-sdk-1.0.0-beta1
SDK="$ZCS/tools-manual/$SDKV"
PICO="$(readlink -f "$ZCS/zephyr_ws/modules")/lib/picolibc"
T=mb-tools                                   # relative to the seat user's $HOME

# ---------------------------------------------------------------------------
step "1/6  preflight  ($SEAT)"
[ -d "$ZCS/modelblaster/pipeline" ] || die "no built ZCS here; source ~/.config/iiswc/dev.env first"
need_file "$SDK/gnu/riscv64-zephyr-elf/bin/riscv64-zephyr-elf-gcc" "the RISC-V toolchain to push"
need_file "$PICO/CMakeLists.txt" "picolibc module to push"
need_exec "$TACIT_SPIKE" "the TACIT spike to push"
R true || die "cannot ssh to $SEAT"
info "seat: $(R 'echo "$(hostname), $(lsb_release -ds 2>/dev/null), python $(python3 -V 2>&1 | cut -d" " -f2), $(df -h ~ | awk "NR==2{print \$4}") free"')"

if [ "$CHECK" = 0 ]; then
  FREE_G=$(R 'df -BG --output=avail ~ | tail -1 | tr -dc 0-9')
  have=$(R "[ -x ~/$T/bin/spike ] && echo 1 || echo 0")
  [ "$have" = 1 ] || [ "${FREE_G:-0}" -ge 6 ] || die "$SEAT has ${FREE_G}G free; the bundle + venv need ~6G"

  # -------------------------------------------------------------------------
  step "2/6  push the toolchain (rsync: only what differs)"
  # tools-manual is a symlink out of the source tree; replace any copied symlink with a directory.
  R "[ -L ~/$T/zephyr-chipyard-sw/tools-manual ] && rm -f ~/$T/zephyr-chipyard-sw/tools-manual; mkdir -p ~/$T/bin ~/$T/modules ~/$T/zephyr-chipyard-sw/tools-manual/$SDKV/gnu"
  run rsync -a --delete -e "$RSYNC_E" \
      --exclude=.git --exclude=/tools --exclude=/tools-manual \
      --exclude=/zephyr_ws/modules --exclude=/zephyr_ws/tools --exclude=/zephyr_ws/bootloader \
      --exclude=__pycache__ \
      "$ZCS/" "$SEAT:$T/zephyr-chipyard-sw/"
  run rsync -a -e "$RSYNC_E" "$SDK/sdk_version" "$SDK/sdk_gnu_toolchains" "$SDK/cmake" \
      "$SEAT:$T/zephyr-chipyard-sw/tools-manual/$SDKV/"
  run rsync -a --delete -e "$RSYNC_E" "$SDK/gnu/riscv64-zephyr-elf" "$SEAT:$T/zephyr-chipyard-sw/tools-manual/$SDKV/gnu/"
  run rsync -a --delete -e "$RSYNC_E" "$PICO" "$SEAT:$T/modules/"
  run rsync -a -e "$RSYNC_E" "$TACIT_SPIKE" "$SEAT:$T/bin/spike"

  # -------------------------------------------------------------------------
  step "3/6  push the tutorial repo  (no out/, no .git, no local config)"
  run rsync -a --delete -e "$RSYNC_E" \
      --exclude=/out/ --exclude=.git --exclude='/*.local.md' --exclude=/board.conf \
      --exclude=/secrets/ --exclude=__pycache__ --exclude=/.board.lock* \
      "$IISWC_ROOT/" "$SEAT:iiswc-tutorial/"

  # -------------------------------------------------------------------------
  step "4/6  python venv on the seat  (torch CPU + Zephyr + ModelBlaster deps)"
  R 'bash -s' <<'EOF' || die "venv setup failed on the seat"
set -e
V=~/mb-tools/venv
[ -x $V/bin/python ] || { sudo -n apt-get install -y -qq python3-venv >/dev/null 2>&1 || true; python3 -m venv $V; }
$V/bin/python -m pip install -q --upgrade pip >/dev/null
# Pinned to what the tutorial conda env runs.  The CPU build of torch keeps it ~0.2 GB.
$V/bin/python -c "import torch" 2>/dev/null || $V/bin/python -m pip install -q torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
$V/bin/python -m pip install -q numpy==2.0.0 west==1.5.0 pyelftools==0.32 PyYAML==6.0.3 \
    packaging jsonschema pykwalify requests jinja2
$V/bin/python -c "import torch, numpy, yaml, requests, elftools; print('    venv ok: torch', torch.__version__, 'numpy', numpy.__version__)"
EOF

  # -------------------------------------------------------------------------
  step "5/6  the seat's ~/.config/iiswc/dev.env  (+ a guarded .bashrc line)"
  SEATNAME="$(R 'hostname')"
  R 'bash -s' <<'EOF' || die "could not write the seat's dev.env"
set -e
mkdir -p ~/.config/iiswc
cat > ~/.config/iiswc/dev.env <<ENV
# Written by scripts/96_seat_mb_setup.sh.  Source BEFORE env.sh:
#   . ~/.config/iiswc/dev.env && . ~/iiswc-tutorial/env.sh
export ZCS=\$HOME/mb-tools/zephyr-chipyard-sw
export TACIT_SPIKE=\$HOME/mb-tools/bin/spike
export IISWC_OUT=\$HOME/iiswc-tutorial/out
export ZEPHYR_MODULES=\$HOME/mb-tools/modules/picolibc
export PATH=\$HOME/mb-tools/venv/bin:\$HOME/mb-tools/bin:\$PATH
export WEST_PYTHON=\$HOME/mb-tools/venv/bin/python
ENV
# The board half, published where every board can aws_pull it (mb = the attendee's command).
mkdir -p ~/pub/mb && cp ~/iiswc-tutorial/fpga/pynq-z2/host/mb ~/iiswc-tutorial/fpga/pynq-z2/host/mb_board_run.sh ~/pub/mb/
ln -sfn ~/iiswc-tutorial/fpga/pynq-z2/host/mb ~/mb-tools/bin/mb     # mb in a Jupyter terminal too
# The lab's folder in JupyterLab (~/work): the notebooks, the walkthroughs and a place for the
# attendee's kernel, each with a README (notebooks/mb_lab/README.md shows the layout).  The
# attendee's notebooks (mb_lab.ipynb, mb_by_hand.ipynb) and kernel are never overwritten; everything
# else is refreshed.
NB=~/iiswc-tutorial/notebooks/mb_lab; L=~/work/modelblaster-llm-lab
mkdir -p $L/walkthroughs $L/your-kernel
# a seat set up by an earlier version of this lab had these loose in ~/work: keep the attendee's
# notebook and kernel (moved in), drop our own copies
[ -f ~/work/mb_lab.ipynb ] && [ ! -f $L/mb_lab.ipynb ] && mv ~/work/mb_lab.ipynb $L/
for f in maxpool2d_s8.c maxpool2d_s8.c.bak; do [ -f ~/work/$f ] && [ ! -f $L/your-kernel/$f ] && mv ~/work/$f $L/your-kernel/; done
rm -rf ~/work/mb_lab_solved ~/work/mb_lab_solved.ipynb ~/work/MB_MAXPOOL_WALKTHROUGH.md
for f in mb_lab.ipynb mb_by_hand.ipynb; do [ -f $L/$f ] || cp $NB/$f $L/; done
cp $NB/mb_lab_solved.ipynb $NB/mb_by_hand_solved.ipynb $NB/seat/README.md $L/
cp $NB/seat/walkthroughs/README.md ~/iiswc-tutorial/docs/MB_MAXPOOL_WALKTHROUGH.md ~/iiswc-tutorial/docs/MB_GELU_WALKTHROUGH.md $L/walkthroughs/
cp $NB/seat/your-kernel/README.md $L/your-kernel/
grep -q 'iiswc-mb-env' ~/.bashrc 2>/dev/null || printf '\n[ -f "$HOME/.config/iiswc/dev.env" ] && . "$HOME/.config/iiswc/dev.env" && . "$HOME/iiswc-tutorial/env.sh" >/dev/null 2>&1  # iiswc-mb-env\n' >> ~/.bashrc
EOF
  info "seat $SEATNAME: dev.env written"
fi

# ---------------------------------------------------------------------------
step "6/6  check ON the seat"
R 'bash -s' <<'EOF'
. ~/.config/iiswc/dev.env 2>/dev/null || { echo "    MISS dev.env"; exit 1; }
cd ~/iiswc-tutorial 2>/dev/null || { echo "    MISS ~/iiswc-tutorial"; exit 1; }
. ./env.sh >/dev/null 2>&1
ok() { printf '    %-4s %-22s %s\n' "$1" "$2" "$3"; }
for t in west cmake ninja dtc riscv64-zephyr-elf-gcc spike python3; do
  p=$(command -v $t) && ok ok "$t" "$p" || ok MISS "$t" ""
done
[ -n "$ZEPHYR_BASE" ] && [ -d "$ZEPHYR_BASE" ] && ok ok ZEPHYR_BASE "$ZEPHYR_BASE" || ok MISS ZEPHYR_BASE ""
[ -d "$ZEPHYR_SDK_INSTALL_DIR/cmake" ] && ok ok ZEPHYR_SDK "$ZEPHYR_SDK_INSTALL_DIR" || ok MISS ZEPHYR_SDK ""
PYTHONPATH=$ZCS python3 -c "import torch, modelblaster" 2>/dev/null && ok ok "torch+modelblaster" "" || ok MISS "torch+modelblaster" "(PYTHONPATH=\$ZCS)"
[ -f ~/.config/iiswc/bedrock.env ] && ok ok "bedrock key" "~/.config/iiswc/bedrock.env" \
  || ok MISS "bedrock key" "run scripts/93_bedrock_key.sh distribute from the key holder"
EOF

if [ "$SMOKE" = 1 ]; then
  step "smoke: the lab ON the seat, no LLM  (MB_BACKEND=reference --op gelu_s8)"
  R 'bash -lc "cd ~/iiswc-tutorial && . ~/.config/iiswc/dev.env && . ./env.sh >/dev/null 2>&1 && MB_BACKEND=reference ./scripts/95_mb_kernel_llm.sh --op gelu_s8 2>&1 | grep -E \"cycles \\(|enumerated:|speedup|verdict|\\[fail\\]\" | tail -6"' \
    || die "the smoke run failed on $SEAT"
fi
