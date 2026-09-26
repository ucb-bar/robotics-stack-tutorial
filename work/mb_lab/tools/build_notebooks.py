#!/usr/bin/env python3
"""The source of the ModelBlaster + LLM lab's two notebooks.  Edit this, re-run it, commit all three.

    python3 notebooks/mb_lab/tools/build_notebooks.py

    notebooks/mb_lab/mb_lab.ipynb          what an attendee runs, on their own seat and board
    notebooks/mb_lab/mb_lab_solved.ipynb   the same lab, redrawn from complete runs stored in
                                           notebooks/mb_lab/assets/solved_runs.tar.gz -- no board or LLM needed,
                                    for when a live run misbehaves or to compare against

Like notebooks/iiswc_tutorial.ipynb, both are committed WITHOUT outputs (an executed notebook
carries the instance's hostname); what a cell should print is described beside it instead.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]              # <repo>/notebooks/mb_lab
SOLVED = HERE / "assets" / "solved_runs.tar.gz"

IMPORT = """
import os, sys
for d in (os.getcwd(), os.path.expanduser("~/iiswc-tutorial/notebooks/mb_lab")):
    if os.path.exists(os.path.join(d, "mb_lab.py")):
        sys.path.insert(0, d)
import mb_lab as lab
"""


def md(s):
    return {"cell_type": "markdown", "metadata": {}, "source": s.strip("\n").splitlines(True)}


def code(s):
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": s.strip("\n").splitlines(True)}


INTRO = """
Your board is a **PYNQ-Z1 FPGA** running a RISC-V core (Rocket, 40 MHz) with a small accelerator built in:
**MBP**, four instructions that each work on **eight int8 values at once**. In this notebook:

1. you meet **MBP.MAX8** and do its job by hand,
2. an LLM (DeepSeek, on AWS Bedrock) rewrites a max-pool kernel to use it, **with your FPGA in the loop**,
3. you find out how much of the speedup is the accelerator itself, and
4. you write the kernel yourself and race the LLM.

Run cells with **Shift-Enter**. How it works, in depth: `walkthroughs/` (left, in the file browser).
"""

ACCEL_MD = """
## 1 · The accelerator, by hand

A 2×2 max pool outputs the largest of four neighbouring bytes. The plain C kernel does that one byte at a
time: **118 cycles per output** on the board. MBP.MAX8 compares **eight pairs of bytes in one instruction**.
Here is the trick the fast kernel uses, on random bytes: two rows in, four outputs out, two instructions.
"""

MAX8_MD = """
**✏️ Your turn.** `lab.max8(a, b)` is MBP.MAX8 in Python: eight lanes, the maximum of each pair. Pick two rows
of eight int8 values (from -128 to 127), **predict** the answer, then run the cell.
"""

LLM_MD = """
## 2 · Let the LLM do it, with your FPGA in the loop (5–8 minutes)

Each round, the LLM writes a few kernels. **Spike**, a simulator, checks each one and times it in seconds.
Then the best one of the round is built for your board and **runs on your FPGA**, and the LLM is told the
real cycle count for its next round. Blue bars are spike, orange bars are your FPGA, and the dashed outline
is the same kernel with the accelerator switched off.

**✏️ First, guess:** how many times faster will the LLM's kernel be on your FPGA?
"""

VERDICT_MD = """
## 3 · Where did the speedup come from?

The verdict splits it in two: what the **rewritten loop** bought (the same kernel with MBP off, vs the
reference), and what the **accelerator** bought (the same kernel with MBP on, vs off). Spike's number is
higher than the board's. Spike charges one cycle per instruction and knows nothing about memory, and once
MAX8 makes the compute eight times denser, memory is what's left.
"""

HOOD_MD = """
### Under the hood

The lab is the usual tools, run in order:
- **ModelBlaster** turns the PyTorch model into an int8 graph and generates C kernels. With `--backend llm`
  it asks the LLM.
- **Zephyr's `west`** builds the images.
- **spike** simulates them.
- The **board's agent** runs them on the FPGA, through its tunnel.

Here is every command the run executed, exactly. Paste any of them into a terminal.
"""

TURN_MD = """
## 4 · Your turn: race the LLM (≈2 minutes per try)

`lab.start()` puts the unoptimized kernel in **`your-kernel/maxpool2d_s8.c`** (left, in the file browser). Its header has
the rules and four hints: read one at a time. Edit, save with **Ctrl-S**, and run `lab.try_kernel()`. It checks
your kernel on spike (a wrong one never reaches the board), then runs it on your FPGA three ways.
**Goal: `ON THE ACCELERATOR` and under 10 cycles per output.**
"""

MORE_MD = """
## 5 · More to try

* `lab.go("maxpool2d_s8", "--guide", "modelblaster")`: the LLM is **not** told about the accelerator. Does it find it?
* `lab.go("linear_s8")`: an int8 matrix multiply on MBP.DOT8, eight multiply-adds per instruction.
* `lab.go("gelu_s8")`: 43–49× faster with **no** accelerator at all. What did the LLM do instead?
* `lab.runs()` lists every run on this seat. `lab.verdict("<run>")`, `lab.kernels("<run>")`,
  `lab.calls("<run>")` and `lab.commands("<run>")` open one again.
* The same lab from a terminal (File → New → Terminal): `mb doctor`, `mb`, `mb try maxpool2d_s8`.
"""


def attendee():
    return [
        md("# Put a kernel on the FPGA's accelerator, with an LLM\n" + INTRO +
           "\n> Stuck, or something not working? **`mb_lab_solved.ipynb`** shows a complete run on a real board.\n"),
        code(IMPORT + "lab.doctor()        # your seat, your board through its tunnel, and the LLM key"),
        md("*Expected:* a checklist of green `ok`s and **READY**, then a card for your board (its name, address, WiFi, "
           "`FPGA operating`). A red line names what is wrong and what to do; `go()` still works without a board "
           "(it runs on spike only) or without the LLM (it replays a verified kernel), and says so."),
        md(ACCEL_MD),
        code("lab.accelerator()   # run it again for other bytes"),
        md(MAX8_MD),
        code("""row0 = [ ... ]            # ✏️ eight numbers between -128 and 127
row1 = [ ... ]            # ✏️ eight more
my_prediction = [ ... ]   # ✏️ what will MAX8 give?
print("MBP.MAX8 says:", lab.max8(row0, row1), "  you said:", my_prediction, "  ",
      "✓" if lab.max8(row0, row1) == my_prediction else "✗")"""),
        md(LLM_MD),
        code("my_guess = ...    # ✏️ your guess: 2? 10? 50?"),
        code('lab.go("maxpool2d_s8")    # no LLM available? lab.go("maxpool2d_s8", "--replay") replays a verified kernel (2 min)'),
        md("*Expected:* a chart that grows as the LLM tries kernels, then `done`. In 8 test runs it was 3.6–19.7× faster on the FPGA; "
           "the LLM writes a different kernel each time, so if yours is slow, run it again."),
        md(VERDICT_MD),
        code('lab.verdict()\nprint(f"your guess: {my_guess}x")'),
        code("lab.kernels()       # before and after; the highlighted lines are the accelerator"),
        code("lab.calls()         # every prompt the LLM got and every answer it gave: open the cards"),
        md(HOOD_MD),
        code("lab.commands()"),
        code('lab.sh("cd $ZCS && python -m modelblaster.pipeline.generate_kernels --help | head -40")   # ModelBlaster\'s own CLI'),
        md(TURN_MD),
        code("lab.start()"),
        code("lab.try_kernel()    # run it again after every edit; your scoreboard is below it"),
        md("*Expected:* the reference and your kernel on spike and on your FPGA, the verdict, and your scoreboard. "
           "A wrong kernel stops on spike with the reason; a kernel that does not compile shows the compiler's error."),
        code("# lab.solution()    # stuck? un-comment to see a solution (15.3x on the FPGA)"),
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
        md("# Put a kernel on the FPGA's accelerator, with an LLM — solved\n" + INTRO +
           f"""
> **This is the solved copy.** Every cell draws a complete run made on a real board, stored in
> `notebooks/mb_lab/assets/solved_runs.tar.gz`, so it works with no board and no LLM, for when your own run misbehaves,
> or to compare against. The LLM run is `{llm}`, and the "your turn" run is `{mine}`.
"""),
        code(IMPORT + f"""lab.use_runs(lab.solved_runs())     # read the stored runs, not this seat's
LLM_RUN, MY_RUN = "{llm}", "{mine}\""""),
        md(ACCEL_MD),
        code("lab.accelerator(7)"),
        md(MAX8_MD),
        code("""row0 = [12, -7, 100, 3, -128, 55, 0, 9]
row1 = [-4, 20, 99, 3, -1, -55, 127, 8]
my_prediction = [12, 20, 100, 3, -1, 55, 127, 9]
print("MBP.MAX8 says:", lab.max8(row0, row1), "  you said:", my_prediction, "  ",
      "✓" if lab.max8(row0, row1) == my_prediction else "✗")"""),
        md(LLM_MD),
        code("my_guess = 10"),
        code("lab.show(LLM_RUN)   # what lab.go(\"maxpool2d_s8\") drew when this run ended"),
        md(VERDICT_MD),
        code("lab.kernels(LLM_RUN)"),
        code("lab.calls(LLM_RUN)"),
        md(HOOD_MD),
        code("lab.commands(LLM_RUN)"),
        md(TURN_MD),
        code("lab.show(MY_RUN)    # lab.try_kernel() with the solution file\nlab.scoreboard()"),
        code("lab.solution()"),
        md(MORE_MD),
    ]


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
    write("mb_lab.ipynb", attendee())
    write("mb_lab_solved.ipynb", solved())
