#!/usr/bin/env python3
"""Generate the ModelBlaster + LLM lab's four notebooks. Edit this file, rerun it, and commit all four.

    python3 notebooks/mb_lab/tools/build_notebooks.py
    python3 notebooks/mb_lab/tools/build_notebooks.py --store-outputs <executed mb_by_hand_solved.ipynb>

    notebooks/mb_lab/mb_lab.ipynb          what an attendee runs, on their own seat and board
    notebooks/mb_lab/mb_lab_solved.ipynb   the same lab, drawn from complete runs stored in
                                           notebooks/mb_lab/assets/solved_runs.tar.gz; needs no board or LLM,
                                           for when a live run fails or to compare against
    notebooks/mb_lab/mb_by_hand.ipynb      what lab.go() does, one ModelBlaster / west / spike command per cell
    notebooks/mb_lab/mb_by_hand_solved.ipynb   mb_by_hand run on a seat with a board; its outputs are copied in
                                           with --store-outputs and kept on later runs while its cells match

mb_lab.ipynb is committed without outputs, like notebooks/iiswc_tutorial.ipynb, because an
executed notebook contains the instance's hostname; the expected output is described next to
each cell. mb_lab_solved.ipynb is committed with outputs so it opens fully drawn: this script
executes its cells against the stored runs, which tools/pack_solved_runs.py has already
scrubbed of the hostname.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]              # <repo>/notebooks/mb_lab
SOLVED = HERE / "assets" / "solved_runs.tar.gz"

IMPORT = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / "iiswc-tutorial/notebooks/mb_lab"))   # the lab's helper module
import mb_lab as lab
"""


def md(s):
    return {"cell_type": "markdown", "metadata": {}, "source": s.strip("\n").splitlines(True)}


def code(s):
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": s.strip("\n").splitlines(True)}


INTRO = """
This notebook uses ModelBlaster to optimize one kernel for your board. ModelBlaster compiles a PyTorch model into C
for a RISC-V target, one kernel per operator. With its LLM backend, an LLM writes each kernel and then tries to make
it faster, and ModelBlaster checks every candidate against a reference implementation and times it.

The kernel is a 2×2 int8 max pool (`maxpool2d_s8`). The target is the Rocket core on your PYNQ-Z1's FPGA
(40 MHz), which has a small SIMD accelerator, the MBP: four instructions that each operate on eight int8 values
at once. You will:

1. see what MAX8, the MBP instruction this kernel can use, computes;
2. optimize the kernel with ModelBlaster and the LLM, with your FPGA in the loop;
3. measure how much of the speedup comes from the accelerator;
4. write the kernel yourself.

`lab.go()` runs the whole ModelBlaster flow in one call; `mb_by_hand.ipynb` runs the same steps one command at a
time. The LLM run takes 5 to 8 minutes and each attempt at your own kernel about 2. Run the cells in order with
Shift-Enter. The `walkthroughs/` folder explains each step in more detail.
"""

ACCEL_MD = """
## 1. What MAX8 computes

A 2×2 max pool writes the largest of four neighbouring bytes. The reference C kernel handles one byte at a time
and takes 118 cycles per output on the board. MBP.MAX8 takes two 64-bit registers and returns the maximum of each byte pair
of the eight pairs. The next cell shows the two MAX8 steps the fast kernel uses, on random input: two input rows
in, four outputs out.
"""

MAX8_MD = """
`lab.max8(a, b)` computes MBP.MAX8 in Python. In the next cell, replace each `...` with two rows of eight int8
values (−128 to 127) and the result you expect, then run the cell to check it.
"""

LLM_MD = """
## 2. Optimize the kernel with ModelBlaster and the LLM (5 to 8 minutes)

`lab.go` runs ModelBlaster's `generate_kernels --backend llm --optimize` in rounds. In each round the LLM proposes a
few kernels, and ModelBlaster checks each one against the reference and times it on spike, an instruction-set
simulator. The fastest kernel of the round is then built for your board and run on the FPGA, and the measured cycle
count goes into the next round's prompt as hardware in the loop feedback. The LLM learns what the MBP instructions
do from the MBP instruction guide, which the lab also adds to ModelBlaster's prompt; `lab.calls()` shows both. In the chart, blue bars are spike, orange bars are the FPGA, and the
dashed outline is the same kernel with the MBP disabled.

Before starting the run, enter your guess in the next cell: how many times faster will the LLM's kernel be on the FPGA?
"""

VERDICT_MD = """
## 3. Where the speedup comes from

The board ran three images: the reference kernel, the LLM's kernel, and the LLM's kernel with the MBP disabled.
The reference against the kernel with the MBP disabled gives the gain from the restructured loop, and MBP disabled against
MBP enabled gives the gain from the accelerator. The two factors multiply to the total.

Spike reports a larger speedup than the board. It charges one cycle per instruction and does not model memory, and
once MAX8 packs eight comparisons into one instruction, memory accesses dominate the remaining time.
"""

HOOD_MD = """
### The commands behind `lab.go`

`lab.go` runs the standard tools in sequence. ModelBlaster converts the PyTorch model to an int8 graph and
generates the C kernels (with `--backend llm`, by asking the LLM), Zephyr's `west` builds the images, spike
simulates them, and the board's agent runs them on the FPGA through its tunnel. The next cell lists every command
the run executed; each one can be pasted into a terminal. `mb_by_hand.ipynb` goes through the same commands one
at a time.
"""

TURN_MD = """
## 4. Write the kernel yourself (about 2 minutes per attempt)

`lab.start()` copies the reference kernel to `your-kernel/maxpool2d_s8.c`. The comment at the top of the file lists
the rules and four hints; read the hints one at a time. Edit and save the file (Ctrl-S), then run
`lab.try_kernel()`. It checks your kernel on spike first, so an incorrect kernel never reaches the board, and then
runs the reference, your kernel, and your kernel with the MBP disabled on the FPGA. The goal is a kernel that runs
on the accelerator (the verdict shows `ON THE ACCELERATOR`) at under 10 cycles per output.
"""

MORE_MD = """
## 5. Further exercises

* `lab.go("maxpool2d_s8", "--guide", "modelblaster")` runs the LLM without the MBP instruction guide. Check
  whether the resulting kernel uses MAX8.
* `lab.go("linear_s8")` optimizes an int8 matrix multiply, which can use MBP.DOT8 (eight multiply and add operations per
  instruction).
* `lab.go("gelu_s8")` reaches 43 to 49× without the accelerator. Compare its kernel with the reference to see what
  changed.
* `lab.runs()` lists the runs on this seat; `lab.verdict("<run>")`, `lab.kernels("<run>")`, `lab.calls("<run>")`
  and `lab.commands("<run>")` reopen one of them.
* The same lab runs in a terminal (File → New → Terminal): `mb doctor`, `mb`, `mb try maxpool2d_s8`.
"""


def attendee():
    return [
        md("# Optimizing a kernel for the board with ModelBlaster and an LLM\n" + INTRO +
           "\n> If something does not work, `mb_lab_solved.ipynb` is a recorded run of this notebook on a real board.\n"),
        code(IMPORT + "lab.doctor()        # your seat, your board through its tunnel, and the LLM key"),
        md("*Expected:* a checklist of green `ok`s ending in READY, then a card for your board with its name, address, "
           "WiFi and `FPGA operating`. A red line says what is wrong and what to do about it. `go()` still works without "
           "a board (it runs on spike only) or without the LLM (it replays a verified kernel), and it tells you so."),
        md(ACCEL_MD),
        code("lab.accelerator()   # rerun for different random input"),
        md(MAX8_MD),
        code("""row0 = [ ... ]            # fill in: eight numbers between -128 and 127
row1 = [ ... ]            # fill in: eight more
my_prediction = [ ... ]   # fill in: what will MAX8 give?
lab.check_max8(row0, row1, my_prediction)"""),
        md(LLM_MD),
        code("my_guess = ...    # fill in: your guess, 2? 10? 50?"),
        code('lab.go("maxpool2d_s8")    # without LLM access, lab.go("maxpool2d_s8", "--replay") replays a verified kernel (2 min)'),
        md("*Expected:* a chart that grows as the LLM tries kernels, then `done`. In 8 test runs the kernel was 3.6 to 19.7× faster "
           "on the FPGA, and a later run stopped at 1.0×. The LLM writes a different kernel each time, so if yours comes out slow, you can run the cell again."),
        md(VERDICT_MD),
        code('lab.verdict()\nlab.compare_guess(my_guess)'),
        code("lab.kernels()       # reference and LLM kernel; highlighted lines use the MBP"),
        code("lab.calls()         # each prompt and response; click a card to expand it"),
        md(HOOD_MD),
        code("lab.commands()"),
        md(TURN_MD),
        code("lab.start()"),
        code("lab.try_kernel()    # rerun after each edit; your scoreboard is shown below"),
        md("*Expected:* the reference and your kernel, on spike and on your FPGA, then the verdict and your scoreboard. "
           "A wrong kernel stops on spike with the reason, and a kernel that does not compile shows the compiler's error."),
        code("# lab.solution()    # uncomment to show a reference solution (15.3x on the FPGA)"),
        md(MORE_MD),
    ]


def solved():
    import tarfile
    with tarfile.open(SOLVED) as tar:
        runjs = {m.name.split("/")[0]: json.load(tar.extractfile(m)) for m in tar.getmembers()
                 if m.name.endswith("/run.json") and m.name.count("/") == 1}
    llm = next((r for r, j in sorted(runjs.items()) if not j.get("kernel_file")), None)
    mine = next((r for r, j in sorted(runjs.items()) if j.get("kernel_file")), None)
    return [
        md("# Optimizing a kernel for the board with ModelBlaster and an LLM (solved copy)\n" + INTRO +
           f"""
> **Recorded run.** Every output below comes from two complete runs on a real board, stored in
> `notebooks/mb_lab/assets/solved_runs.tar.gz`: a live run of ModelBlaster's LLM backend (`{llm}`) and, for
> section 4, the example solution run as your kernel (`{mine}`). Rerunning a cell redraws the same output without a
> board or the LLM. Use it when your own run misbehaves, or to compare against yours.
"""),
        code(IMPORT + f"""lab.use_runs(lab.solved_runs())     # read the stored runs, not this seat's
LLM_RUN, MY_RUN = "{llm}", "{mine}\""""),
        md("In `mb_lab.ipynb`, `lab.doctor()` checks your seat, board and LLM key at this point. "
           "The next cell shows the board these runs were measured on."),
        code("lab.board_of(LLM_RUN)"),
        md(ACCEL_MD),
        code("lab.accelerator(7)"),
        md(MAX8_MD),
        code("""row0 = [12, -7, 100, 3, -128, 55, 0, 9]
row1 = [-4, 20, 99, 3, -1, -55, 127, 8]
my_prediction = [12, 20, 100, 3, -1, 55, 127, 9]
lab.check_max8(row0, row1, my_prediction)"""),
        md(LLM_MD),
        code("my_guess = 10"),
        code("lab.show(LLM_RUN)   # what lab.go(\"maxpool2d_s8\") drew when this run ended\nlab.compare_guess(my_guess, LLM_RUN)"),
        md(VERDICT_MD),
        code("lab.kernels(LLM_RUN)"),
        code("lab.calls(LLM_RUN)"),
        md(HOOD_MD),
        code("lab.commands(LLM_RUN)"),
        md(TURN_MD),
        code("lab.starting_kernel()   # what lab.start() puts in your-kernel/maxpool2d_s8.c"),
        code("lab.show(MY_RUN)    # lab.try_kernel() with the solution file\nlab.scoreboard()"),
        code("lab.solution()"),
        md(MORE_MD),
    ]


# ---- mb_by_hand: what lab.go() does, one ModelBlaster / west / spike command per cell -----------
# The first cell (Python) loads the seat's toolchain environment into the kernel and sets OP and
# RUN; every %%bash cell inherits that environment and runs from the repository root.


def bash(s):
    return code("%%bash\n" + s.strip("\n"))


BH_INTRO = """
# Inside `lab.go()`: optimizing a kernel for the board with ModelBlaster

`lab.go("maxpool2d_s8")` in `mb_lab.ipynb` runs a complete optimization in one call. This notebook runs the
same steps one command at a time, so you can see what each tool does and change any of it.

ModelBlaster compiles a PyTorch model into C for a RISC-V target: it lowers the model to an int8 graph and then
generates one kernel per operator. The kernels can come from a reference implementation, from a library of
handwritten kernels, or from an LLM, which ModelBlaster asks to write a kernel and then to make it faster. Every
candidate is checked bit for bit against the reference and timed on spike, an instruction set simulator. The
notebook adds the step that matters most for this board, hardware in the loop feedback: the LLM's kernel is
measured on the FPGA, and the measurement goes into the next round's prompt.

1. Lower the PyTorch op to an int8 graph (`extract_graph`).
2. Generate the reference kernel and time it on spike (`generate_kernels --backend reference`).
3. Let the LLM write and optimize a kernel (`generate_kernels --backend llm --optimize`).
4. Build both kernels for the board and measure them on the FPGA.
5. Give the LLM the FPGA's numbers (hardware in the loop feedback) and optimize again.
6. Build the best kernel with the MBP switched off, to see how much of the speedup is the accelerator.

The code cells are shell commands (`%%bash`), the same ones you would type in a terminal. Each run of the
setup cell creates a new folder under `by-hand/` for everything the later steps write; you can browse it on
the left. The notebook takes about 10 minutes (7 in our test run), most of it in the two LLM rounds.
"""

BH_SETUP_MD = """
## 0. Setup

The next cell loads the seat's toolchain environment into this notebook (in a terminal you would run
`. ~/.config/iiswc/dev.env && . ~/iiswc-tutorial/env.sh`), picks the op, creates the run folder, and sets the
few variables ModelBlaster needs:

| variable | why |
|---|---|
| `PYTHONPATH` | ModelBlaster is run as `python -m modelblaster.pipeline.<tool>` from its checkout, `$ZCS` |
| `CPATH` | the kernels include `pext.h`, which exposes the MBP instructions as C functions |
| `MODELBLASTER_EXTRA_CMAKE_ARGS` | ModelBlaster times candidates on spike; this builds them without an FPU, like the board |
| `SPIKE_VERIFY_TIMEOUT` | a candidate that hangs on spike is dropped after 90 s instead of 300 s |
| `LLM_PROVIDER` | `--backend llm` calls `$MODEL` (DeepSeek) on AWS Bedrock with the seat's key |
"""

BH_SETUP = """
import os, subprocess, time
from pathlib import Path

env = subprocess.run(["bash", "-c", ". ~/.config/iiswc/dev.env && . ~/iiswc-tutorial/env.sh >/dev/null && env -0"],
                     capture_output=True, check=True).stdout.decode()
os.environ.update(line.split("=", 1) for line in env.split("\\0") if "=" in line)

OP = "maxpool2d_s8"                                   # the op to optimize; section 1 lists the others
REPO = Path(os.environ["IISWC_ROOT"])
RUN = Path.home() / "work/modelblaster-llm-lab/by-hand" / time.strftime(f"{OP}-%m%d-%H%M%S")
(RUN / "logs").mkdir(parents=True)
os.chdir(REPO)                                        # the commands below use paths relative to the repository

os.environ.update({
    "OP": OP,
    "RUN": str(RUN),
    "PYTHONPATH": os.environ["ZCS"],
    "CPATH": str(REPO / "fpga/pynq-z2/sw"),
    "MODELBLASTER_EXTRA_CMAKE_ARGS": "-DCONFIG_FPU=n;-DCONFIG_FLOAT_HARD=n",
    "SPIKE_VERIFY_TIMEOUT": "90",
    "LLM_PROVIDER": "bedrock",
})
print("run folder:", RUN)
"""

BH_CHECK_MD = """
`mb doctor` checks the rest of the setup: the toolchain, the LLM key (with one short call), and your board. The
board keeps an ssh tunnel open to this seat, and the seat runs images on it through a small agent on the board.
"""

BH_OP_MD = """
## 1. The op

ModelBlaster starts from PyTorch. A bench file defines a `Model` and `get_inputs()`; this one is a single 2×2
max pool over a 16×64×64 int8 tensor.
"""

BH_SPECS_MD = """
ModelBlaster knows how to generate a kernel for an op from its entry in `KERNEL_SPECS`
(`modelblaster/pipeline/reference_kernels.py`). An entry holds the C signature, a plain reference implementation
that every generated kernel is checked against, and a list of algorithms. An algorithm is a short description,
with an example, that the LLM is given when it writes the kernel; each one names the targets it suits. The next
cell lists the ops that have an algorithm for this board's target, `pext` (the Rocket core with the MBP), and
prints the description the LLM will get for `maxpool2d_s8`.

To optimize another op, set `OP` in the setup cell to `linear_s8` or `gelu_s8` (they have bench files here) and
run the notebook again from the top.
"""

BH_SPECS = """
python - <<'EOF'
import os
from modelblaster.pipeline.reference_kernels import KERNEL_SPECS

for op, spec in sorted(KERNEL_SPECS.items()):
    algorithms = [a.name for a in spec.algorithms if "pext" in a.target_affinity]
    if algorithms:
        print(f"{op:16} {', '.join(algorithms)}")

for a in KERNEL_SPECS[os.environ["OP"]].algorithms:
    if "pext" in a.target_affinity:
        print(f"\\n{a.name}:\\n{a.description}")
EOF
"""

BH_IR_MD = """
## 2. Lower the op to an int8 graph

`extract_graph` runs the model once, quantizes it to int8 with one calibration batch, and fuses what the target
can fuse. It writes the graph (`graph.json`: ops, shapes and scales), the weights, and `io.npz`: an input and
PyTorch's output for it, which every generated kernel has to reproduce exactly.
"""

BH_IR = """
python -m modelblaster.pipeline.extract_graph --bench-file fpga/pynq-z2/modelblaster/mb_ops/$OP.py \\
    --out-dir $RUN/ir --quant int8 --num-calibration 1 --fusion-target pext > $RUN/logs/extract_graph.log
ls $RUN/ir
"""

BH_GRAPH = """
import json

graph = json.loads((RUN / "ir/graph.json").read_text())
for op in graph["ops"]:
    print(op["op"], op.get("shape"))
"""

BH_REF_MD = """
## 3. The reference kernel

`generate_skeleton` writes the C for the model around its kernels: buffers, weights and the test data.
`generate_kernels` then fills in one kernel per op. It looks in a library of handwritten kernels first
(`--global-curated-dir`); a kernel found there is used as is, and the log says `curated HIT`. Otherwise it uses the
op's reference implementation (`--backend reference`) or asks the LLM (`--backend llm`).

This repository's library already has a fast `maxpool2d_s8`, so the run works on a copy with that kernel removed:
the reference kernel is then ModelBlaster's plain C, and a faster one has to come from the LLM. The run also gets
a copy of ModelBlaster's spike harness, which lacks a configuration file for the `pext` target.
"""

BH_PREP = """
cp -r fpga/pynq-z2/modelblaster/kernels $RUN/curated
rm $RUN/curated/pext*/pext*_${OP}_*.c                 # remove the handwritten kernels for this op
cp -r $ZCS/modelblaster/harness $RUN/harness && chmod -R u+w $RUN/harness
touch $RUN/harness/backends/pext.conf
"""

BH_REF = """
python -m modelblaster.pipeline.generate_skeleton --ir $RUN/ir/graph.json --weights $RUN/ir/weights.npz \\
    --io $RUN/ir/io.npz --backend pext --out-dir $RUN/reference > $RUN/logs/skeleton-reference.log
cp $RUN/ir/graph.json $RUN/reference/                # the harness build reads the graph from here

python -m modelblaster.pipeline.generate_kernels --ir $RUN/ir/graph.json --io $RUN/ir/io.npz \\
    --target pext --quant int8 --backend reference --global-curated-dir $RUN/curated \\
    --out-dir $RUN/reference --cache-dir $RUN/cache-reference --build-dir $RUN/build/candidates \\
    --repo-root $ZCS/modelblaster --harness-dir $RUN/harness > $RUN/logs/generate-reference.log 2>&1
cat $RUN/reference/kernel_picks.json
"""

BH_SPIKE_MD = """
To time the reference kernel, build ModelBlaster's harness for spike with `west`, Zephyr's build tool, and run it.
`-DMB_PEXT_HW=1` compiles the MBP functions in `pext.h` to the real instructions. The image runs the model once,
compares the output with PyTorch's (`MODELBLASTER_VERIFY`) and prints one line per op with its cycle count. Spike
counts one cycle per instruction.
"""

BH_SPIKE = """
west build -p always -b spike_riscv64 $RUN/harness -d $RUN/build/spike-reference -- \\
    -DMODEL_DIR=$RUN/reference -DMODELBLASTER_BACKEND=pext -DMODELBLASTER_KERNEL_CFLAGS=-DMB_PEXT_HW=1 \\
    -DCONFIG_FPU=n -DCONFIG_FLOAT_HARD=n > $RUN/logs/build-spike-reference.log 2>&1
spike $RUN/build/spike-reference/zephyr/zephyr.elf | tee $RUN/spike-reference.txt | grep -E "VERIFY|,$OP,"
"""

BH_LLM_MD = """
## 4. The LLM writes a kernel, and optimizes it

With `--backend llm`, `generate_kernels` gives the LLM the op's reference implementation and an algorithm
description, and asks for a kernel. Each answer is compiled here and checked against the reference, bit for bit;
if it fails, the error goes back to the LLM. `--optimize` then runs a small beam search: it times the first
correct kernel on spike, asks the LLM for `--expansions` faster variants of each of the `--beam` best kernels,
checks and times each, and keeps the fastest. Every correct kernel is stored in `--cache-dir`.

ModelBlaster's prompt does not describe the MBP, so the call goes through `scripts/lib/mb_llm_tap.py`, a small
wrapper that passes everything after `--` to `generate_kernels` unchanged. Each `--system-append` file is added to
ModelBlaster's system prompt: here the MBP instruction guide (`pext_isa_guide.md`, what the four MBP
instructions do) and `idea_line.md` (a rule asking the LLM to state each candidate's idea in one line). The
wrapper also records every prompt and answer in `llm-calls.jsonl` and stops after `--max-calls`. ModelBlaster's
own output goes to the log, and the cell shows one line per LLM call. A round takes one to three minutes.
"""

BH_ROUND1 = """
python -m modelblaster.pipeline.generate_skeleton --ir $RUN/ir/graph.json --weights $RUN/ir/weights.npz \\
    --io $RUN/ir/io.npz --backend pext --out-dir $RUN/round1 > $RUN/logs/skeleton-round1.log
cp $RUN/ir/graph.json $RUN/round1/

python scripts/lib/mb_llm_tap.py --transcript $RUN/llm-calls.jsonl --max-calls 6 --round 1 \\
    --system-append fpga/pynq-z2/modelblaster/mb_ops/pext_isa_guide.md \\
    --system-append fpga/pynq-z2/modelblaster/mb_ops/idea_line.md \\
    --log $RUN/logs/generate-round1.log -- \\
  --ir $RUN/ir/graph.json --io $RUN/ir/io.npz --target pext --quant int8 \\
  --backend llm --optimize --beam 2 --expansions 2 --global-curated-dir $RUN/curated \\
  --out-dir $RUN/round1 --cache-dir $RUN/cache --build-dir $RUN/build/candidates \\
  --repo-root $ZCS/modelblaster --harness-dir $RUN/harness
"""

BH_KERNEL_MD = """
The kernel ModelBlaster kept is the newest file in the cache. The lines that call `mb_pext_max8` are the MBP
instruction.
"""

BH_KERNEL = """
kernel = max((RUN / "cache").glob("*.c"), key=os.path.getmtime)
print(kernel.name, "\\n")
print(kernel.read_text())
"""

BH_CALLS_MD = """
Every call to the LLM, from `llm-calls.jsonl`, with the start of the first prompt (what ModelBlaster asks for) and of its answer:
"""

BH_CALLS = """
calls = [json.loads(line) for line in (RUN / "llm-calls.jsonl").read_text().splitlines()]
for c in calls:
    added = [name for name, mark in [("MBP instruction guide", "packed-SIMD integer extension (MBP)"),
                                      ("hardware in the loop feedback", "### Hardware in the loop feedback")]
             if mark in (c["system"] or "")]
    print(f"#{c['n']}  round {c['round']}  {c['phase']:<24} {c['latency_s']:4.0f} s  "
          f"{c['input_tokens']:,} tokens in, {c['output_tokens']:,} out   added: {', '.join(added)}")

print("\\n--- prompt of call #1 (first 25 lines) ---")
print("\\n".join(calls[0]["user"].splitlines()[:25]))
print("\\n--- answer of call #1 (first 15 lines) ---")
print("\\n".join((calls[0]["response"] or "").splitlines()[:15]))
"""

BH_BOARD_MD = """
## 5. Measure on the FPGA

The board runs a fixed bitstream: a Rocket core at 40 MHz with the MBP on hart 0 (MAGIC `0x5A5A0038`). Images for
it use Zephyr's board `chipyard_pynqz1_all_f40` and this repository's sample `samples/modelblaster_pext`, which
runs the model on hart 0 `MB_ITERS` times after one warmup, checks the output against PyTorch's, and reports the
cycles of each op. The next cell builds the reference and the round-1 kernel.
"""

BH_BOARD_BUILD = """
for model in reference round1; do
  west build -p always -b chipyard_pynqz1_all_f40 samples/modelblaster_pext -d $RUN/build/board-$model -- \\
      -DBOARD_ROOT=$PWD -DMODEL_DIR=$RUN/$model -DMODELBLASTER_KERNEL_CFLAGS=-DMB_PEXT_HW=1 -DMB_ITERS=3 \\
      > $RUN/logs/build-board-$model.log 2>&1
done
cd $RUN/build && ls -l board-*/zephyr/zephyr.bin
"""

BH_RUN_MD = """
`mb run-image` runs one image on your board and prints its console. It uses the board agent's three commands
(`put` the image, `run` it, `get` the console) and waits if another run is using your board:

    ssh -p 19022 -i ~/.ssh/iiswc-board-agent xilinx@localhost "put zephyr.bin <bytes> <md5>" < zephyr.bin
    ssh -p 19022 -i ~/.ssh/iiswc-board-agent xilinx@localhost "run zephyr"
    ssh -p 19022 -i ~/.ssh/iiswc-board-agent xilinx@localhost "get console.out"

Each run takes about 30 seconds.
"""

BH_RUN = """
mkdir -p $RUN/fpga
mb run-image $RUN/build/board-reference/zephyr/zephyr.bin > $RUN/fpga/reference.txt
mb run-image $RUN/build/board-round1/zephyr/zephyr.bin > $RUN/fpga/round1.txt
grep -h -E "^(MB_PEXT_OP|RESULT)" $RUN/fpga/reference.txt $RUN/fpga/round1.txt
"""

BH_CYCLES = """
import re

def fpga(model):
    \"\"\"The op's cycles on the FPGA, and cycles per output, from a board console.\"\"\"
    text = (RUN / f"fpga/{model}.txt").read_text(errors="replace")
    cycles = int(re.search(rf"^MB_PEXT_OP .* op={OP} .*cycles=(\\d+)", text, re.M).group(1))
    outputs = len(re.search(r"^MB_PEXT_OUT (.*)$", text, re.M).group(1).split())
    return cycles, cycles / outputs

reference, round1 = fpga("reference"), fpga("round1")
print(f"reference kernel   {reference[1]:6.1f} cycles per output")
print(f"round 1 kernel     {round1[1]:6.1f} cycles per output   {reference[0] / round1[0]:.1f}x faster on your FPGA")
"""

BH_FEEDBACK_MD = """
## 6. Hardware in the loop feedback: tell the LLM what the FPGA measured, and optimize again

Spike charges one cycle per instruction and does not model memory, so a kernel that is fast on spike can be
slower than expected on the board. `lab.go` therefore runs each round's best kernel on the FPGA and adds the
measurement to the next round's system prompt. This is the only information the LLM gets that ModelBlaster and
the MBP instruction guide do not give it. Here is that step by hand: write the numbers to a file, and run round 2
with the file as one more `--system-append`. Round 2 uses the same `--cache-dir`, so it starts from
round 1's kernel. When round 1 is already close to what the board allows, round 2 may not find anything faster;
ModelBlaster then keeps round 1's kernel.
"""

BH_FEEDBACK = """
feedback = RUN / "fpga-feedback.md"
feedback.write_text(f\"\"\"### Hardware in the loop feedback: cycles measured on the FPGA
Round 1's kernel was run on the real board (a Rocket core at 40 MHz with the MBP; cycles from rdcycle).
Spike, which scores your candidates, charges one cycle per instruction and has no memory timing, so a kernel
with fewer, wider memory accesses gains more on the board than spike shows. Optimize for these numbers:
- reference kernel: {reference[1]:.1f} cycles per output
- round 1's kernel: {round1[1]:.1f} cycles per output ({reference[0] / round1[0]:.1f}x faster than the reference)
\"\"\")
print(feedback.read_text())
"""

BH_ROUND2 = """
python -m modelblaster.pipeline.generate_skeleton --ir $RUN/ir/graph.json --weights $RUN/ir/weights.npz \\
    --io $RUN/ir/io.npz --backend pext --out-dir $RUN/round2 > $RUN/logs/skeleton-round2.log
cp $RUN/ir/graph.json $RUN/round2/

python scripts/lib/mb_llm_tap.py --transcript $RUN/llm-calls.jsonl --max-calls 6 --round 2 \\
    --system-append fpga/pynq-z2/modelblaster/mb_ops/pext_isa_guide.md \\
    --system-append fpga/pynq-z2/modelblaster/mb_ops/idea_line.md \\
    --system-append $RUN/fpga-feedback.md \\
    --log $RUN/logs/generate-round2.log -- \\
  --ir $RUN/ir/graph.json --io $RUN/ir/io.npz --target pext --quant int8 \\
  --backend llm --optimize --beam 2 --expansions 2 --global-curated-dir $RUN/curated \\
  --out-dir $RUN/round2 --cache-dir $RUN/cache --build-dir $RUN/build/candidates \\
  --repo-root $ZCS/modelblaster --harness-dir $RUN/harness
"""

BH_ROUND2_BOARD = """
west build -p always -b chipyard_pynqz1_all_f40 samples/modelblaster_pext -d $RUN/build/board-round2 -- \\
    -DBOARD_ROOT=$PWD -DMODEL_DIR=$RUN/round2 -DMODELBLASTER_KERNEL_CFLAGS=-DMB_PEXT_HW=1 -DMB_ITERS=3 \\
    > $RUN/logs/build-board-round2.log 2>&1
mb run-image $RUN/build/board-round2/zephyr/zephyr.bin > $RUN/fpga/round2.txt
grep -E "^(MB_PEXT_OP|RESULT)" $RUN/fpga/round2.txt
"""

BH_ROUND2_CMP = """
round2 = fpga("round2")
for name, (cycles, per_output) in [("reference", reference), ("round 1", round1), ("round 2", round2)]:
    print(f"{name:10} {per_output:6.1f} cycles per output   {reference[0] / cycles:5.1f}x")

best = "round2" if round2[0] < round1[0] else "round1"
os.environ["BEST"] = best
print("\\nfastest on the FPGA:", best)
"""

BH_MBP_MD = """
## 7. How much of the speedup is the accelerator?

`objdump` shows the MBP instructions in the best kernel: the assembler does not know them, so they appear as
`.insn` words. Then the same kernel is built with `-DMB_PEXT_HW=0`, which makes `pext.h` compile each MBP
instruction to its C equivalent, and run on the board again. The difference between the two is what the
accelerator contributes.
"""

BH_MBP = """
riscv64-zephyr-elf-objdump -d --disassemble=kernel_${OP}_kb_${OP} $RUN/build/board-$BEST/zephyr/zephyr.elf \\
    | grep insn || echo "no MBP instructions in this kernel"

west build -p always -b chipyard_pynqz1_all_f40 samples/modelblaster_pext -d $RUN/build/board-mbpoff -- \\
    -DBOARD_ROOT=$PWD -DMODEL_DIR=$RUN/$BEST -DMODELBLASTER_KERNEL_CFLAGS=-DMB_PEXT_HW=0 -DMB_ITERS=3 \\
    > $RUN/logs/build-board-mbpoff.log 2>&1
mb run-image $RUN/build/board-mbpoff/zephyr/zephyr.bin > $RUN/fpga/mbpoff.txt
grep -E "^(MB_PEXT_OP|RESULT)" $RUN/fpga/mbpoff.txt
"""

BH_SUMMARY_MD = """
## 8. Results

The speedup on the FPGA splits into two factors that multiply: the rewritten loop (the reference against the
LLM's kernel with the MBP off) and the accelerator (the LLM's kernel with the MBP off against on). `lab.verdict()`
in `mb_lab.ipynb` reports the same numbers.
"""

BH_SUMMARY = """
spike_reference = int(re.search(rf"^\\d+,\\w+,{OP},[^,]*,(\\d+)$", (RUN / "spike-reference.txt").read_text(), re.M).group(1))
spike = {"reference": spike_reference}
for r in ("round1", "round2"):
    spike[r] = json.loads((RUN / r / "optimize_summary.json").read_text())[OP]["best"]
board = {"reference": reference, "round1": round1, "round2": round2, "mbpoff": fpga("mbpoff")}
outputs = reference[0] / reference[1]

print(f"{'':22}{'spike':>10}{'FPGA':>10}   cycles per output")
for name in ("reference", "round1", "round2"):
    print(f"{name:22}{spike[name] / outputs:10.1f}{board[name][1]:10.1f}")
print(f"{best + ', MBP off':22}{'':>10}{board['mbpoff'][1]:10.1f}")

total = reference[0] / board[best][0]
loop = reference[0] / board["mbpoff"][0]
accelerator = board["mbpoff"][0] / board[best][0]
print(f"\\n{total:.1f}x faster on your FPGA = {loop:.1f}x from the rewritten loop x {accelerator:.1f}x from the accelerator")
"""

BH_SAME_MD = """
## 9. The same thing in one command

`mb go maxpool2d_s8` in a terminal, or `lab.go("maxpool2d_s8")` in `mb_lab.ipynb`, runs these steps. It also
checks exactness over the whole input range where the op allows it, falls back to a recorded kernel when the
LLM is unavailable and to spike alone when the board is, and writes a report. `mb commands <run>` lists every
command a run executed.
"""

BH_EX_MD = """
## 10. Exercises

1. Run `generate_kernels --backend llm` with the full library of handwritten kernels. The next cell does this;
   look for `curated HIT` in its output. Was the LLM called?
2. Run round 1 again without the MBP instruction guide (remove its `--system-append` line), with a new
   `--cache-dir` and `--out-dir`. Does the LLM find MBP.MAX8 from ModelBlaster's own algorithm description?
3. Compare spike's and the FPGA's cycles per output in section 8. Why is the gap larger for the LLM's kernels
   than for the reference?
4. Set `OP = "linear_s8"` or `OP = "gelu_s8"` in the setup cell and run the notebook again.
"""

BH_EX1 = """
cp -r $RUN/reference $RUN/with-curated
python -m modelblaster.pipeline.generate_kernels --ir $RUN/ir/graph.json --io $RUN/ir/io.npz \\
    --target pext --quant int8 --backend llm --global-curated-dir fpga/pynq-z2/modelblaster/kernels \\
    --out-dir $RUN/with-curated --cache-dir $RUN/cache-with-curated --build-dir $RUN/build/candidates \\
    --repo-root $ZCS/modelblaster --harness-dir $RUN/harness > $RUN/logs/generate-with-curated.log 2>&1
grep "curated HIT" $RUN/logs/generate-with-curated.log
cat $RUN/with-curated/kernel_picks.json
"""

BH_ANSWERS_MD = """
### Answers

1. The log shows `curated HIT`, and no LLM call is made. `kernel_picks.json` names the library kernel's algorithm
   (`pext_max8_rows`) but still says `llm`, because under `--backend llm` a library kernel is recorded that way;
   the log line is the reliable sign. This is why the notebook works on a copy of the library without it.
2. In our runs without the MBP instruction guide, the LLM's `maxpool2d_s8` stayed at 1.0×: it wrote plain C and never
   used MBP.MAX8. The algorithm description names MBP.MAX8 but does not say what the instruction does or how to
   call it.
3. The reference kernel does one byte at a time, so its time is mostly instructions, which spike counts. MAX8
   packs eight comparisons into one instruction, so what remains is mostly memory access, which spike does not
   model.
4. `linear_s8` uses MBP.DOT8, and its build with the MBP off is much slower. The fast `gelu_s8` kernel is a
   lookup table with no MBP instruction, so its build with the MBP off runs at the same speed.
"""


def by_hand(solved=False):
    note = ("\n> **Recorded run.** Every cell was run on a seat with a real board and the LLM, and its output is stored "
            "below it. The only edits are removed hostnames, IP addresses and terminal colour codes.\n" if solved else
            "\n> `mb_by_hand_solved.ipynb` is a recorded run of this notebook on a real board, with all of its output.\n")
    cells = [
        md(BH_INTRO + note),
        md(BH_SETUP_MD),
        code(BH_SETUP),
        md(BH_CHECK_MD),
        bash("mb doctor"),
        md(BH_OP_MD),
        bash("cat fpga/pynq-z2/modelblaster/mb_ops/$OP.py"),
        md(BH_SPECS_MD),
        bash(BH_SPECS),
        md(BH_IR_MD),
        bash(BH_IR),
        code(BH_GRAPH),
        md(BH_REF_MD),
        bash(BH_PREP),
        bash(BH_REF),
        md(BH_SPIKE_MD),
        bash(BH_SPIKE),
        md(BH_LLM_MD),
        bash(BH_ROUND1),
        md(BH_KERNEL_MD),
        code(BH_KERNEL),
        md(BH_CALLS_MD),
        code(BH_CALLS),
        md(BH_BOARD_MD),
        bash(BH_BOARD_BUILD),
        md(BH_RUN_MD),
        bash(BH_RUN),
        code(BH_CYCLES),
        md(BH_FEEDBACK_MD),
        code(BH_FEEDBACK),
        bash(BH_ROUND2),
        bash(BH_ROUND2_BOARD),
        code(BH_ROUND2_CMP),
        md(BH_MBP_MD),
        bash(BH_MBP),
        md(BH_SUMMARY_MD),
        code(BH_SUMMARY),
        md(BH_SAME_MD),
        md(BH_EX_MD),
        bash(BH_EX1),
    ]
    if solved:
        cells.append(md(BH_ANSWERS_MD))
    return cells


def store_outputs(executed, cells):
    """Copy the outputs of an executed mb_by_hand_solved.ipynb (jupyter nbconvert --execute on a
    seat) into its cells, scrubbed of what identifies the machine: ANSI codes, the instance's
    hostname, IP addresses.  Long outputs keep their head and tail."""
    import re
    nb = json.loads(Path(executed).read_text())
    ran = [c for c in nb["cells"] if c["cell_type"] == "code"]
    mine = [c for c in cells if c["cell_type"] == "code"]
    if [c["source"] for c in ran] != [c["source"] for c in mine]:
        raise SystemExit(f"{executed} is not this generator's mb_by_hand_solved.ipynb: regenerate, run it again")
    scrub = [(re.compile(r"\x1b\[[0-9;]*[A-Za-z]"), ""),
             (re.compile(r"\bip-\d+-\d+-\d+-\d+\b"), "<seat>"),
             (re.compile(r" ?\(key [^)]*\)"), ""),                  # the LLM key's id
             (re.compile(r"\b(ACCA|AKIA|ASIA)[A-Z0-9]{12,}\b"), "<key id>"),
             (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b"), "<ip>")]
    for r, m in zip(ran, mine):
        outs = []
        for o in r.get("outputs", []):
            if o["output_type"] == "error":
                raise SystemExit(f"{executed}: a cell failed ({o['ename']}): not storing it")
            text = "".join(o.get("text", ""))
            for rx, s in scrub:
                text = rx.sub(s, text)
            lines = text.splitlines(True)
            if len(lines) > 160:
                lines = lines[:100] + [f"... ({len(lines) - 150} lines not shown) ...\n"] + lines[-50:]
            outs.append({"output_type": "stream", "name": o.get("name", "stdout"), "text": lines})
        m["outputs"], m["execution_count"] = outs, r.get("execution_count")
    return cells


def execute(cells):
    """Run each code cell, in order and in one namespace, as Jupyter would, and store what it
    displayed or printed as the cell's outputs.  Uses IPython's display if it is installed;
    otherwise a stand-in that records the same HTML (the helpers only display HTML)."""
    import contextlib
    import io
    import sys
    import types
    shown = []
    try:
        import IPython.display as ipd
    except ImportError:
        ipd = types.ModuleType("IPython.display")
        sys.modules.setdefault("IPython", types.ModuleType("IPython")).display = ipd
        sys.modules["IPython.display"] = ipd
    class HTML:
        def __init__(self, data):
            self.data = data
    class Handle:
        def update(self, obj):
            shown[-1] = obj.data
    def display(obj, display_id=False):
        shown.append(obj.data)
        return Handle() if display_id else None
    ipd.HTML, ipd.display = HTML, display
    sys.modules.pop("mb_lab", None)
    sys.path.insert(0, str(HERE))                      # this checkout's mb_lab.py, wherever it is
    ns = {}
    for n, c in enumerate(x for x in cells if x["cell_type"] == "code"):
        shown.clear()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec("".join(c["source"]), ns)
        outs = []
        if out.getvalue():
            outs.append({"output_type": "stream", "name": "stdout", "text": out.getvalue().splitlines(True)})
        for h in shown:
            outs.append({"output_type": "display_data", "metadata": {},
                         "data": {"text/html": h.splitlines(True), "text/plain": ["<drawn by mb_lab>"]}})
        c["outputs"], c["execution_count"] = outs, n + 1
    return cells


def write(name, cells):
    for i, c in enumerate(cells):
        c["id"] = f"cell-{i:02d}"
    nb = {"cells": cells,
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                       "language_info": {"name": "python"}},
          "nbformat": 4, "nbformat_minor": 5}
    (HERE / name).write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
    print("wrote", HERE / name)


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["--store-outputs"]:
        # python3 tools/build_notebooks.py --store-outputs <mb_by_hand_solved.ipynb, executed on a seat>
        write("mb_by_hand_solved.ipynb", store_outputs(sys.argv[2], by_hand(solved=True)))
        sys.exit(0)
    write("mb_lab.ipynb", attendee())
    write("mb_lab_solved.ipynb", execute(solved()))
    write("mb_by_hand.ipynb", by_hand())
    old = HERE / "mb_by_hand_solved.ipynb"             # keep its stored outputs, when its cells did not change
    try:
        write("mb_by_hand_solved.ipynb", store_outputs(old, by_hand(solved=True)))
    except (SystemExit, FileNotFoundError):
        write("mb_by_hand_solved.ipynb", by_hand(solved=True))
        print("  (mb_by_hand_solved.ipynb has no outputs: run it on a seat, then --store-outputs)")
