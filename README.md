# A robotics stack on an open RISC-V SoC, on a $200 FPGA board

This repository is the material for a hands-on tutorial: you take a PyTorch network, push it
through a quantising compiler, run it on a **Rocket** RISC-V SoC that you load into the PL of a
**PYNQ-Z1/Z2**, and watch what it costs — with a hardware tracer, a packed-SIMD extension and a
decoupled accelerator you can switch in and out to see each one pay for itself.

Everything here runs on one board plus a Linux workstation. The bitstreams are prebuilt and
committed, so **you do not need a Vivado licence** to do any of the hardware labs.

---

## What is in the stack

```
  PyTorch model
        |  ModelBlaster: quantise to int8/q16, generate C kernels
        v
  Zephyr application  ──────────────┐
        |  riscv64 build             │  samples/ — 35 apps
        v                            │
  Rocket SoC in the PYNQ's PL  <─────┘
   ├─ big.LITTLE dual-core RV64GC
   ├─ MBP packed-SIMD ("P-ext") on hart 0
   ├─ RoccMoon — a decoupled RoCC matmul/attention engine with LayerNorm, softmax and LUT lanes
   ├─ TACIT — a hardware branch-trace encoder, traced back on the host with Spike + a decoder
   └─ peripherals: PDM microphone, RGB LEDs, buttons, SSD1306 OLED, HM01B0 camera
```

The worked example that ties it together is **Moonshine Tiny**, a small speech transformer: it is
quantised, code-generated, run on the board, fed from the board's own microphone, and profiled
dispatch by dispatch.

## Hardware you need

| | |
|---|---|
| **Board** | PYNQ-Z1 or PYNQ-Z2 (Zynq-7020). The bitstreams here are Z1 pinouts; a Z2 works for everything that does not use the Z1-only shield headers. |
| **SD card** | 16 GB+, written with the stock **PYNQ v3.1.1** image. See `fpga/pynq-z2/sdcard/README.md`. |
| **Network** | Ethernet from your workstation to the board. Point-to-point is fine and is what most people use. |
| **Optional** | An HM01B0 camera on a shield (`fpga/pynq-z2/docs/CAMERA_PCB_SPEC.md`), a 0.96" SSD1306 OLED on the same I²C bus, a USB-UART for console. |
| **Workstation** | Linux, ~60 GB free for the toolchains. Vivado only if you want to *rebuild* a bitstream. |

## Getting started

```bash
git clone --recursive https://github.com/ucb-bar/robotics-stack-tutorial
cd robotics-stack-tutorial

scripts/00_bootstrap.sh          # Zephyr SDK, conda env, west modules  (long, once)
source env.sh
scripts/01_doctor.sh             # says exactly what is missing and what to run
```

`01_doctor.sh` is the thing to run whenever anything is confusing. It checks the toolchains, the
patched trees, the bitstream manifest and the board, and every failure it reports names the script
that fixes it.

### Tell it which board is yours

**Nothing in this repository defaults to a board address.** A lab that silently connects to
whatever answers at a hard-coded IP is worse than one that refuses to start — on a shared network
that is somebody else's FPGA, and the first thing a lab does is load a bitstream into it.

```bash
cp board.conf.example board.conf      # then edit: PYNQ_HOST=xilinx@<your board>
```

or set it per command: `PYNQ_HOST=xilinx@10.0.0.42 scripts/20_rocket_run.sh ...`

Before your first lab, put an SSH key on the board and give it passwordless sudo — the labs run
non-interactively (`ssh -o BatchMode=yes`), so a password prompt is not something they can answer.
`fpga/pynq-z2/docs/BRINGUP.md` §1b is the five-line version; `scripts/provision_board.sh` checks it.

### Run something

```bash
scripts/10_tacit_hello.sh                     # Lab A: no board — Spike, TACIT trace, decoder
scripts/11_pext_selftest.sh                   # Lab A2: the packed-SIMD ISA on Spike
scripts/with_board.sh scripts/20_rocket_run.sh   # Lab B: Zephyr on Rocket, on the FPGA
```

`scripts/with_board.sh` takes a `flock` so two people cannot drive one board at once. Every board
lab goes through it.

## The labs

**A-series — host only, no board.** `scripts/10`, `11`, `12`: the Spike + TACIT + decoder flow, the
packed-SIMD instructions proved in simulation before any silicon, and the co-location solve —
scheduling two networks across the two harts from board-measured per-dispatch costs.

**RTL gates — Verilator, seconds, no licence.** `scripts/27`, `52`, `55`, `63`, `65`, `72`: the
P-ext ALU, the RoCC engine, the OLED I²C path, the fast multiplier and the camera capture path, each
simulated in the SoC's *own* RTL. Three hardware bugs in this project reached a board through RTL
that had never been simulated; these gates are why the build scripts run them first.

**B-series — on the board.** `scripts/20` … `scripts/87`, and `scripts/90`. Roughly in order:
bring-up and Zephyr, TACIT off real silicon, dual-core, the memory hierarchy, packed-SIMD kernels,
the peripherals (microphone, LEDs, camera, OLED, buttons), then the speech stack — encoder,
decoder, live microphone, and the accelerator A/Bs — and last the sign-detection stack:
`scripts/82`–`87` train, lower, bake and run SignDetLite and the grey-scale classifier before it
(`scripts/91` installs a real lowered detector; see `docs/SIGNDET_WEIGHTS.md`).

**ModelBlaster + LLM (an AWS seat and the board).** In `scripts/95_mb_kernel_llm.sh --op <op>`,
ModelBlaster's LLM backend (Bedrock, `BACKEND=llm`) has an LLM rewrite one int8 kernel. Spike scores
each kernel, and each is checked bit for bit against the reference (over the whole input domain where
the op allows). The board runs each round's best kernel, and the cycles it measures go into the next
round's prompt as hardware in the loop feedback. At the end it runs the reference, the new kernel and, when the new kernel uses
the MBP, the new kernel with the accelerator switched off, so the verdict says how much of the speedup
comes from the accelerator itself. At the tutorial, attendees work in JupyterLab on their AWS seat
(`notebooks/mb_lab/`, or `mb` in a terminal, which is `fpga/pynq-z2/host/mb`), and the seat drives their
board through the board's reverse ssh tunnel. This needs the tutorial's card image and an AWS seat
(`docs/MB_INSTRUCTOR.md`).

Each lab writes a run directory under `out/`, a `run.json` naming the bitstream md5 and the clocks
as read back, and a results row. `expected/` holds the golden outputs the labs check themselves
against.

### The demos

Some of these are less a lab than a thing to show someone, and each stands on its own — what it
needs, what it produces, what a correct result looks like, and how to tell a wrong one.

**`scripts/90_b156_tacit_window.sh` — both harts, both busy, one wall clock.**
`samples/tacit_duo` runs the camera → detector → OLED pipeline on hart 0 and the microphone →
MFCC → keyword spotter on hart 1, traces *both* from the reset vector into their own DMA sinks,
and merges them into one Perfetto timeline. The interesting part is the bound. Counting units —
N camera frames and M seconds of audio — cannot work, because frames cost ~0.97 s and audio blocks
~0.22 s and the two counts can only be made to agree by predicting both; the run that did it that
way produced a 55 s, 48 MB artefact whose every structural gate passed and which was **80 % a
recording of an idle hart 0**. `DUO_WINDOW_MS` bounds both harts with one wall clock measured from
the reset vector and stops both encoders while both workloads are still mid-unit, so the two lanes
end at the same cycle by construction: **0.10 s apart, 0.7 % of a 13.309 s window**, against
43.75 s. Seven gates, two of which fail on the old artefact — that is what makes them gates. It
also re-measures the number that sizes the trace buffers, and finds a busy lane emits **5.3×** the
rate the buffers had been sized from. `expected/tacit_duo_window.json`.

> **The detector's trained weights are not in this repository, and the demo runs anyway.**
> SignDetLite is trained on GTSDB scenes, and GTSDB's licence could not be established — its
> canonical site does not resolve from our network and the mirrors state none — so no
> artefact embedding those weights is published here or anywhere else by this project.
> `./scripts/84_signdet_lower.sh` instead lowers a **deterministic random-weight model**
> (`signdet/random_weights.py`, numpy PCG64, **seed 144**) with the same architecture, the
> same tensor shapes and the same interface quantisation scales, so the lowering, the kernel
> selection, the guest build, both TACIT lanes and the whole scheduling result are exactly
> what the real model produces. What a random network cannot do is *detect*: **gate 4,
> REPLAY, is the one gate of the seven that depends on the weights**, and in random mode it
> is reported **not applicable** — not quietly passed, not counted as a failure — while the
> other six still gate. The real weights ship with the tutorial image and are installed by
> `scripts/91_signdet_install_model.sh` from a local directory, with a checksum manifest and
> no network. `docs/SIGNDET_WEIGHTS.md` is the whole account, including which gates are
> meaningful in which mode. GTSDB is cited in `signdet/make_data.py` and must be obtained
> from its own source.

**`scripts/12_xpurt_coloc_sweep.sh` — two networks, two harts, one schedule.** Host only, and
every duration in it was measured on a board. Moonshine has to finish; the detector must not miss
its 1 fps frame; the two harts hold different instruction sets, so a kernel curated for one *traps*
on the other; and they share a memory system. 44 cells — nine heuristics and two CP-SAT paths,
across contention on/off and a left-shift compaction post-pass on/off. Under measured DRAM
contention that post-pass is what crosses the window: **5,412.25 → 3,992.30 ms, 1,419.95 ms
recovered**, with `op_deadline_miss_count` 0 in both, so no detector frame was traded for it. And
**none of the nine heuristics clears both objectives at all** — the best makespan misses three of
the four windows, and the only policy that lands all four ends 2,076 ms past the reference. The
lab also refuses four of its own cells: one solver path builds its model without reading the hard
machine exclusions and placed 74 dispatches on harts whose instruction set would trap on them.
`expected/xpurt_coloc2m_b157.json`.

## Bitstreams

**Ten bitstreams are committed to this repository** (~4 MB each, `fpga/pynq-z2/build_*_z1/*.bit`).
That is a deliberate choice: an attendee should be able to do every hardware lab on the first
morning, and building one takes a Vivado licence, a Chipyard tree and a couple of hours. They cover
the tutorial path end to end — DRAM self-test, single-core Rocket + TACIT, dual-core big.LITTLE,
packed-SIMD, the microphone and RGB builds, and the AXI interface-ceiling variants.

`fpga/pynq-z2/bitstreams.csv` is the manifest: every file, its md5, its size, the SoC magic word it
self-identifies with, and what it is for. **The flow fails loudly, never silently:**

```bash
scripts/check_bitstreams.sh      # verifies every row; --write recomputes the md5s
```

and every board lab calls `bitstream_require()` (`scripts/lib/bitstream_id.sh`), which refuses a
missing file *and* refuses a file whose md5 disagrees with the manifest. A bitstream that is not the
one the goldens in `expected/` were measured against is not a smaller problem than no bitstream at
all, so it is treated as the same problem.

**Five bitstreams are listed but not shipped**, marked `untracked` in the manifest
(`0x5A5A0035`–`0x5A5A0039`). They are the measurement builds for the speech workstream — the ones
behind the published accelerator numbers — not tutorial content, and shipping another 20 MB of
binaries for labs nobody runs on day one is not a good trade. The labs that want them
(`scripts/56`, `57`, `66`, `79`, `80`, `81`, `90`) fail with the md5, the filename and where to look.
To supply them, point `IISWC_BIT_DIR` at a directory holding the files, or drop them in
`/opt/iiswc/bit`; both are searched. To rebuild one, `fpga/pynq-z2/scripts/build_*_z1.sh` with
Vivado 2023.1 and a Chipyard tree.

## How the tree is laid out

| | |
|---|---|
| `scripts/` | every lab and flow driver, numbered in the order you meet them; `scripts/lib/` is the shared shell library (locking, board identity, bitstream gating, feature gates) |
| `samples/` | 35 Zephyr applications — `moonshine_live`, `cam_capture`, `mic_*`, `oled_status`, `panel_buttons`, `modelblaster_pext`, the benches and the smoke tests |
| `boards/chipyard/` | Zephyr board definitions, one per SoC variant and clock |
| `patches/` | the patch series applied into Chipyard, rocket-chip, Zephyr, Spike and the TACIT decoder; `scripts/02_verify_patches.sh` reconstructs each tree byte-for-byte and reports drift |
| `expected/` | golden outputs — what each lab's console is supposed to say |
| `notebooks/` | the attendee notebook, and the tool that generates it from the published page. `iiswc_tutorial.ipynb` is **generated — do not hand-edit it**; edit `tools/build_notebook.py` and re-run |
| `fpga/pynq-z2/src/`, `tcl/` | the RTL and constraints, and the Vivado build flows |
| `fpga/pynq-z2/chipyard/` | the Chisel configs, plus the **vendored generated Verilog** for every pinned SoC — `scripts/08_gensrc.sh` unpacks it, so rebuilding a bitstream needs Vivado but not a Chipyard install |
| `fpga/pynq-z2/host/` | what runs on the workstation to drive the board: bitstream loading, clocks, console, captures |
| `fpga/pynq-z2/sw/` | the on-target C: the accelerator runtime, the P-ext kernels, the integer non-linearities |
| `fpga/pynq-z2/modelblaster/` | the quantise + codegen pipeline and its curated kernels, for speech, vision and keyword spotting |
| `fpga/pynq-z2/rtl_study/` | the Verilator testbenches and out-of-context area/timing flows behind the RTL gates |
| `fpga/pynq-z2/xpurt/` | everything the co-location solve reads: the spec, the dispatch graphs carrying the hard machine exclusions, the board-measured per-dispatch cost tables and the DRAM contention model. XPU-RT itself is not vendored — see that directory's README |
| `fpga/pynq-z2/sdcard/` | preparing a card, and `per_board_setup.sh`, which also scrubs the credential the stock PYNQ image leaves in `/boot/REVISION` |

## Documentation

The docs kept here are the ones that tell you how to *do* something:

* `docs/REPRODUCING.md` — rebuilding the whole environment from nothing
* `docs/SIGNDET_WEIGHTS.md` — the sign detector's weights: why the trained ones are not here,
  what the random-weight default demonstrates and what it cannot, which of `scripts/90`'s
  gates are meaningful in which mode, and how an operator installs the real ones
* `docs/BEDROCK.md` — the Bedrock key for `BACKEND=llm`, which can call only one model, and how to create
  it, test it, put it on every seat, rotate it and revoke it (`scripts/93_bedrock_key.sh`)
* `docs/MB_ATTENDEE.md` — the attendee's card (a single page) for the ModelBlaster + LLM lab (open `notebooks/mb_lab/mb_lab.ipynb`, or run `mb`)
* `docs/MB_MAXPOOL_WALKTHROUGH.md`, `docs/MB_GELU_WALKTHROUGH.md` — what the LLM's kernel does, how it is
  proven correct and proven to run on the accelerator, with exercises
* `docs/MB_INSTRUCTOR.md` — setting that lab up for a room (the key, the seats, the boards, the fallbacks)
* `fpga/pynq-z2/docs/BRINGUP.md` — day one with a board: card, network, SSH key, passwordless sudo
* `fpga/pynq-z2/docs/PROGRAMMING_AND_LOADING.md` — getting bitstreams and binaries into the part
* `fpga/pynq-z2/docs/UART.md` — console options, and what the on-board FTDI can and cannot do
* `fpga/pynq-z2/docs/ROCKET.md`, `ZEPHYR_ON_ROCKET.md` — the SoC, and how Zephyr boots on it
* `fpga/pynq-z2/docs/MICROPHONE.md`, `RGB_LEDS.md`, `OLED_SSD1306.md`, `CAMERA_Z1.md`,
  `CAMERA_PCB_SPEC.md` — each peripheral: the electrical facts, the pins, the RTL, the driver, and
  how to wire and run the thing

Comments in the code cite a larger set of design documents by name — `MEMORY_BANDWIDTH.md §7`,
`ROCC_DECOUPLED.md §8`, and so on — and occasionally a raw run under `out/` or `archive/`. Those are
the project's internal research and evidence record and are **not part of this repository**. The
citations are left in place because they say *why* a constant is what it is, and that is worth more
than a tidy comment; treat them as a pointer to the reasoning, not a file you are expected to open.

## The attendee notebook

`notebooks/iiswc_tutorial.ipynb` is the tutorial's published attendee page as a notebook, for
the per-seat JupyterLab an attendee actually sits in front of. It is **generated** from
`notebooks/tools/build_notebook.py` — the page is the source of truth, so a page change is a
data edit in that file and never a hand-edit of notebook JSON. Re-run it and the notebook
reproduces byte for byte.

It is committed **without outputs**, deliberately: an executed notebook carries the instance's
hostname. Expected output lives in markdown beside each cell instead, quoted from a bench run,
so an attendee can tell whether theirs matched. Two units are pre-seeded in `assets/` rather
than built live — a TACIT capture taken on silicon, because the cards do not carry the
bitstream that can take one, and a kernel gate's expected text, because the gate takes three
minutes and prints `FAIL` lines by design (they are its poisoned control arms).

`notebooks/mb_lab/` holds the ModelBlaster + LLM lab in the same form. Its notebooks are generated, and the one an
attendee runs, `mb_lab.ipynb`, is committed without outputs. `mb_lab_solved.ipynb` is committed already drawn, from
complete recorded runs stored in its `assets/` and scrubbed of hostnames, so it can be read without a board or LLM when a
live run misbehaves. `mb_by_hand.ipynb` (and its solved copy) runs the same steps as `lab.go()` one
ModelBlaster, `west` or `spike` command at a time. `notebooks/mb_lab/README.md` describes the folder an attendee sees.

Every cell says where it runs — `lab.sh()` on the instance, `lab.board()` on the card, and
nothing else touches a board. `notebooks/README.md` says what the board half needs that this
repository does not ship, and why a missing transport reports itself as a stub rather than
inventing output.

`deps.lock` pins every external tree this repo builds against — Spike, the TACIT decoder, the Zephyr
workspace — with the exact revision and the reason it is pinned there. It is the file to read when a
submodule checkout does not do what you expect.

## A note on the board password

A stock PYNQ card ships with a well-known default login, published by the vendor, and a number of
lab scripts still carry it as a literal for a `sudo -S` fallback. Install the passwordless-sudo
drop-in from `fpga/pynq-z2/docs/BRINGUP.md` §1b and `sudo` never reads stdin, so that fallback is
never exercised. Change the password on any card that will sit on a shared network, and wipe cards
after a tutorial: the stock image also runs a root Jupyter with a fixed password and a terminal.

Never commit a credential here. `board.conf` is gitignored precisely so that the one machine-local
thing you have to write down does not travel.
