#!/usr/bin/env python3
"""Generate `notebooks/iiswc_tutorial.ipynb` from the published attendee page.

The page -- `/scratch/dima/iiswc-site/src/data/instructions.ts` -- is the source of
truth for what the units are, what they run, and what the screen should say. This
script is the transcription of it, so a page change is a data edit here and not a
hand-edit of notebook JSON. **Edit this file, re-run it, commit both.**

    python3 notebooks/tools/build_notebook.py

Fidelity rules kept here on purpose:
  * every command, every expected-output block and every fix is the page's text;
  * nothing is invented where the page says a unit has no attendee sequence;
  * a step's `where` decides which helper runs it -- `lab.sh` on the instance,
    `lab.board` on the card, and nothing at all where the page says the step
    happens at the glass or on a laptop.

Where this is not a transcription
---------------------------------
The page describes the flow in which the attendee joins `iiswc-robotics-tutorial`, holds
a shared SSH key and reaches AWS through their card. The interface moved
(TUTORIAL_INTERFACE_NOTES.md 4e, TUTORIAL_AUTH_TLS.md): the attendee opens JupyterLab on
their own instance and the board connects to that instance. Steps 0.2, 0.5 and 1.4 are
therefore written for the flow that exists rather than transcribed from the page, and the
page's own "find your instance" step has no counterpart here at all.

The notebook does not narrate that difference: an attendee needs the flow in front of
them, not its history. The page is a separate repository and is edited separately.

Prose style, and the reason this file is the place to fix prose: ordinary sentences, the
command, and what the screen should say. Explain the technical content -- the two harts
and their extensions, what the trace encoder records, what the solver optimises -- and
not how the tutorial's own plumbing is arranged.
"""
import json
from pathlib import Path

NB = Path(__file__).resolve().parents[1] / "iiswc_tutorial.ipynb"

cells: list[dict] = []


def _lines(text: str) -> list[str]:
    """nbformat wants every line but the last to KEEP its newline.

    Splitting on "\\n" and dropping it collapses the whole cell onto one line, which
    looks right in the JSON and is a SyntaxError in the kernel. Caught by executing the
    notebook; it is not visible by reading it.
    """
    parts = text.rstrip("\n").split("\n")
    return [p + "\n" for p in parts[:-1]] + parts[-1:]


def _cell_id() -> str:
    return f"c{len(cells):03d}"


def md(text: str) -> None:
    cells.append({"cell_type": "markdown", "id": _cell_id(), "metadata": {},
                  "source": _lines(text)})


def code(text: str) -> None:
    cells.append({"cell_type": "code", "id": _cell_id(), "execution_count": None,
                  "metadata": {}, "outputs": [], "source": _lines(text)})


def fence(text: str, lang: str = "") -> str:
    return f"```{lang}\n{text}\n```"


def fixes_table(fixes) -> str:
    rows = "\n".join(f"| {s} | {a} |" for s, a in fixes)
    return "**If instead you see**\n\n| | do |\n|---|---|\n" + rows


# ======================================================================================
# Front matter -- instructions.ts `instructions`
# ======================================================================================
md("""# IISWC 2026 · Attendee bench card

## Five units, in the order you run them

Work down the page. Each step is a command, the output you should get, and what to do
when you don't.

*The page's commands and quoted outputs were executed on 2026-09-23 on bench card
`pynq-2`. The board outputs in this notebook were re-run on 2026-09-24 on card
`pynq-13` and are quoted from those runs.*

---

### Where each cell runs

| helper | runs on |
|---|---|
| `lab.sh("...")` | this instance |
| `lab.board("verb")` | your card |

`lab.board()` sends one of a fixed set of verbs to the card, listed in 0.5. Nothing
else reaches it.""")

md("""### Your seat

Take it from the OLED: the last octet of `10.42.0.N`.

| | |
|---|---|
| Your board | `10.42.0.{N}` |
| Its hostname | `pynq-{N}` |
| Your instance | `aws-{N}.iiswc` |""")

code('''import sys, pathlib
sys.path.insert(0, str(pathlib.Path.cwd()))
import iiswc_lab as lab

SEAT = "N"          # <-- put your seat number here, from the OLED

lab.where_am_i()''')

code("lab.board_status()")

# ======================================================================================
# Unit 0
# ======================================================================================
md("""---

## Unit 0 · Check your board, and its link to this instance

**Runs on your board today.** Five short steps. Every unit after this assumes them.""")

md("""### 0.1 Read the OLED

*At the board · nothing to type.* `up M:SS` counts from the SoC's last boot and must be
ticking; a frozen counter means the SoC has stopped.

Expected, on the glass:

""" + fence("10.42.0.{N}\niiswc-robotics-tutorial\nup 4:17") + "\n\n" + fixes_table([
    ("`up M:SS` is frozen", "Power-cycle. The counter should restart at `up 0:00`."),
    ("`NO ADDRESS`, or `wlan0 DOWN`", "Power-cycle. Nothing below works until the board has an address."),
    ("Blank, and under two minutes", "Wait. Bringing up the network takes about 37 s."),
]))

md("""### 0.2 Check your board is connected

*Nothing to type.* Your board connects to this instance by itself. `lab.board_status()`
reports whether yours has; re-run it whenever the card stops replying:""")
code("lab.board_status()")
md("""There are three answers, and it takes about three seconds:

""" + fence(
    "board connected -- pynq-13, 10.42.0.13, PL operating, MAGIC -\n"
    "board offline (nothing is listening) -- ConnectionRefusedError ...\n"
    "board offline (tunnel is stale) -- the port is bound but nothing answered within 3s") + """

""" + fixes_table([
    ("`board offline (nothing is listening)`", "Your card has not connected yet. Read the OLED (0.1); power-cycle if `up M:SS` is frozen."),
    ("`board offline (tunnel is stale)`", "The card retries on its own — wait, and re-run this cell."),
    ("It stays offline after a power-cycle", "Tell an instructor. **Every instance-side unit below runs without a board.**"),
]))

md("""### 0.3 Read the board's status from Linux

*On the board.* The same fields as the OLED, read from Linux. If the two disagree,
trust this one.""")
code('lab.board("status")')
md("""Expected. Measured on card 13 on 2026-09-24; yours reports your own seat:

""" + fence("""{
  "ok": true,
  "agent": "b176.1",
  "board": {
    "hostname": "pynq-13",
    "iface": "wlan0",
    "ipv4": "10.42.0.13",
    "ssid": "iiswc-robotics-tutorial",
    "signal_dbm": -33,
    "link_up": 1,
    "pl_state": "operating",
    "soc_magic": "-"
  },
  "incoming": 0,
  "results": 0,
  "tunnel_unit": "active",
  "uptime_s": 51910
}""", "json") + """

**`soc_magic: "-"` is not an error.** Reading the number needs privilege, and a card
that has not done a privileged read reports `-`.

""" + fixes_table([
        ("`ipv4  NO ADDRESS`", "Back to 0.1."),
        ("`pl_state` is anything but `operating`", "Power-cycle and let the boot service load the bitstream."),
        ("`soc_magic 0x5A5A0039`", "That is the trace bitstream. Units 1 and 2 need `0x5A5A0038`: reload the PL, no re-image."),
    ]))

md("""### 0.5 The verbs your card accepts

*Reference.* These are all of them:

| verb | does |
|---|---|
| `ping`, `status` | is it there, and who is it |
| `help`, `ls` | what it accepts, what results it holds |
| `put`, `get` | a file in, a result out |
| `bitstream` | load a PL variant |
| `run` | one named lab step |
| `camera`, `mic` | one capture, where the hardware is fitted |""")
code('lab.board("help")')
md("""Expected:

""" + fence("""{
  "ok": true,
  "agent": "b176.1",
  "verbs": ["help", "ping", "status", "ls", "put", "get",
            "bitstream", "run", "camera", "mic"],
  "max_put": 67108864,
  "max_get": 67108864
}""", "json"))

# ======================================================================================
# Unit 1
# ======================================================================================
md("""---

## Unit 1 · Zephyr and Chipyard: build an image on the instance, run it on your SoC

**Runs on your board today · needs the uplink.** Your card has no toolchain. Build the
image on the instance, then load it on the board.""")

md("""### 1.1 Open a shell on your instance

This notebook is that shell. Step 1.2 onward runs here; if your browser disconnects,
reconnect — the work runs on the instance, not in your session.""")

md("""### 1.2 Build a Zephyr image for the Rocket SoC

*On the instance.* About thirteen seconds. `samples/boot_info` is the guest your board is
running now.""")
code('''lab.sh("""cd /home/ubuntu/tut && source /home/ubuntu/tut/env.sh && \\
west build -p always -b chipyard_pynqz1_all_f40 \\
    -d ~/out/boot_info samples/boot_info \\
    -- -DBOARD_ROOT=/home/ubuntu/tut""", timeout=900)''')
md("Expected, at the end:\n\n" + fence(
    "-- west build: building application\n...\n"
    "Memory region         Used Size  Region Size  %age Used\n"
    "             RAM:       69720 B       256 MB      0.03%"))
code('lab.sh("ls -l ~/out/boot_info/zephyr/zephyr.bin")')
md("Expected:\n\n" + fence("-rw-rw-r-- 1 ubuntu ubuntu 55536 ... zephyr.bin"))

md("""### 1.4 Upload the image to the PYNQ board and run it

*On the board.* Upload the image you just built to the card, load the PL and start the
guest, then read the console back.""")
code('lab.board_put("/home/ubuntu/out/boot_info/zephyr/zephyr.bin")')
md("""Expected. The md5 is computed on the card and is the one step 1.2 built, so a
truncated push shows up here rather than as a dead guest:

""" + fence("""{
  "ok": true,
  "stored": "zephyr.bin",
  "bytes": 55536,
  "md5": "50469e9c18e9ec24f1e9ec7d0fe45ef1"
}""", "json") + """

Measured 1.94 s for 55,536 B. Now load the PL, start the guest, and read the console:""")
code('lab.board("run", "zephyr", timeout=300)')
md("""Expected, in about 40 s:

""" + fence("""{
  "ok": true,
  "ran": "zephyr",
  "console_bytes": 373,
  "results": ["console.out", "run.log"]
}""", "json") + """

The console is a result on the card. Fetch it:""")
code('''c = lab.board("get", "console.out", binary=True, verbose=False)
print(c.stdout.decode("utf-8", "replace") if c.ok else c)''')
md("""Expected — 373 bytes, measured on card 13 on 2026-09-24:

""" + fence(
    "*** Booting Zephyr OS build 4329bf61c4fe ***\n"
    "BI_BOOT addr=0x8f000000\n"
    "BI_RAW magic=0x42533031\n"
    "BI_OLED probe addr=0x3c addr_ack_rc=0 nop_rc=0 init_rc=0 setup_rc=0\n"
    "BI_OLED state=READY addr=0x3c\n"
    "BI_STATUS state=PRESENT nonce=0xa1873611 soc_magic=0x5A5A0038\n"
    "BI_NET host=pynq-13 iface=wlan0 ipv4=10.42.0.13 ssid=iiswc-robotics-tutorial "
    "link_up=1 signal_dbm=-31\n"
    "BI_DONE") + """

`soc_magic=0x5A5A0038` is the bitstream Units 1 and 2 want. `nonce` is yours and will
differ; the OLED restarts at `up 0:00`.

""" + fixes_table([
    ("`console_bytes: 0`", "Stop and tell an instructor. Do not retry and do not reboot."),
    ("The console is garbage characters", "The guest was built for the wrong board, so the clock is wrong. Rebuild for `chipyard_pynqz1_all_f40`."),
    ("`another console reader is already on /dev/ttyPS1`", "Kill the PID it prints, not a name pattern."),
    ("Banner only, no `BI_` lines", "The reader started late. The full text is in `console.out` on the card."),
]) + """

This has run twice on the bench board and not yet on a card from the imaging flow, so
zero console bytes may be the card rather than your build.""")

# ======================================================================================
# Unit 2
# ======================================================================================
md("""---

## Unit 2 · ModelBlaster: Compiling PyTorch Models to embedded Heterogeneous SoCs

**Part runs today · on this instance.** Compile a PyTorch model to int8 kernels for this
SoC, replace one, and check that the replacement is identical rather than merely faster.

Generating the kernels has no attendee sequence yet. What runs on your seat is the gate
below, and it is how a replacement kernel is accepted: it compares every candidate
against the shipping kernel over every shape and scale the decoder dispatches. Unit 4 is
an LLM writing one of those replacements.""")

md("""### 2.1 Optional: run the kernel gate

*On the instance, with gcc.* It takes no arguments and exits with the number of
failures. About three minutes, which is why it is optional.""")
code('''lab.sh("cd /home/ubuntu/tut && fpga/pynq-z2/modelblaster/kernels/pext_nl/test/b76_gate.sh",
       timeout=600)''')
md("""**Expect a lot of `FAIL` lines, and expect the gate to pass anyway.** The gate
proves each route is live by running a *poisoned* copy of it and
checking that the gate rejects the poison, so every `FAIL` line is a poisoned arm being
caught. A passing run prints **26 `FAIL` lines and 260 `MISMATCH` lines**. What you
compare against is the four gates and the verdict:

""" + fence(
    "b76 permute gate:   ... max_abs_err=0 fails=0  PASS\n"
    "b76 mul gate:       ... max_abs_err=0 fails=0  PASS\n"
    "b66 layernorm gate: ... max_abs_err=0 fails=0  PASS\n"
    "b66 softmax gate:   ... max_abs_err=0 fails=0  PASS\n\n"
    "B76 GATE PASSED: all four treatments byte-identical to the shipping kernels over\n"
    "every shape and scale the decoder dispatches, and all ten new routes proved live by\n"
    "a poisoned arm that the same gate rejects.") + """

and `rc=0`. The full text is shipped beside this notebook, so you can compare without
spending the three minutes:""")
code('print(open("assets/b76_gate.expected.txt").read())')

# ======================================================================================
# Unit 3
# ======================================================================================
md("""---

## Unit 3 · TACIT: instruction-level tracing of two heterogeneous harts on one timeline

**Part runs today.** A trace encoder in the Rocket core writes retired instructions to
memory; a decoder turns them into a timeline.

Capture has no attendee sequence: your card carries `0x5A5A0038`, TACIT needs
`0x5A5A0039`, and the on-board decoder is not shipped. Capture is shown from the
front.""")

md("""### 3.1 Cache the trace viewer

*On your laptop · do this first · needs the uplink.* Open it once, now, and let it
finish loading.

<https://ui.perfetto.dev>

Expected: the Perfetto UI, with an **Open trace file** button in the left sidebar.

The first load needs the internet; after that it runs in your browser. There is no
offline copy in the room.

""" + fixes_table([
    ("The uplink is already down", "Borrow a neighbour's cached tab, or watch from the front."),
]))

md("""### Where the SoC spent its cycles, from a capture taken on silicon

*On the instance.* One capture is shipped beside this notebook, so the viewer you just
cached has something to open.""")
code("t = lab.unpack_trace()")
md("""Expected: about 1.99 MB, 534,990 instructions, 2.021 bits per instruction, captured
2026-09-16 on a Rocket SoC.

The same summary in Python, without leaving the notebook:""")
code("lab.trace_summary(t)")
md("""Expected, at the top:

""" + fence(
    "function                    self ticks   share    calls\n"
    "__muldf3                       210,583   31.3%    1,515\n"
    "__subdf3                        99,067   14.7%    1,146\n"
    "__mulsf3                        56,400    8.4%      600") + """

Six of the top eight are `__muldf3`, `__subdf3`, `__mulsf3`, `__addsf3`, `__subsf3` and
`__adddf3`: compiler soft-float. This core has no FPU, so a field-oriented-control loop
written in `float` and `double` spends most of its retired instructions emulating
arithmetic.

**To open the trace in Perfetto:** in the JupyterLab file browser on the left,
right-click `rocket_tacit_trace.perfetto.json`, choose Download, and drag the file into
the Perfetto tab you cached in 3.1.""")
code("""lab.budget("trace, decoded JSON", 1_989_396)
lab.budget("trace, gzip -9", 96_680)""")
md("""20.6x, which is why a trace travels compressed: the room has one shared 2.4 GHz
channel, 4.021 MiB/s measured for all thirty seats together (B170 / L412).""")

md("""### Two heterogeneous harts on one timeline (Lab B156, `L401`)

**Read-and-inspect, not runnable.** `scripts/90_b156_tacit_window.sh` needs the lowered
SignDetLite tree, its eight replay frames and a board carrying bitstream `0x5A5A0039`,
none of which is in this repository, and the 45.7 MB merged trace it produced is not
shipped either. What is shipped is the measured lane table the gates were computed from:
`assets/b156_lanes.json`, 9 KB.

The two harts hold different extensions — hart 0 has the packed-SIMD path the convolution
kernels use, hart 1 is scalar — so the same frame costs them very different amounts of
time. One wall clock bounds both lanes, and the question is whether both work through the
whole window and stop together.""")
code('''import json
lanes = json.load(open("assets/b156_lanes.json"))
for pid, lane in lanes["lanes"].items():
    print(f'pid {pid}  {lane["name"]}')
    print(f'    {lane["events"]:>7,} events, {lane["distinct"]} distinct frames')
    print(f'    in the model {lane["model_time_pct"]:5.1f}% of the window, '
          f'spin/console/idle {lane["idle_time_pct"]:5.1f}%')
print(f'\\nmodel work on the two lanes ends {lanes["ends_together_s"]:.2f} s apart '
      f'= {lanes["ends_together_pct"]:.1f}% of the window')
print("gates:", lanes["gates"])''')
md("""Expected:

""" + fence(
    "pid 0  hart 0 (BIG, MBP) signdet_live\n"
    "        258,393 events, 179 distinct frames\n"
    "        in the model  22.4% of the window, spin/console/idle  53.9%\n"
    "pid 1  hart 1 (LITTLE, scalar) kws_live\n"
    "        109,712 events, 103 distinct frames\n"
    "        in the model  75.8% of the window, spin/console/idle   6.1%\n\n"
    "model work on the two lanes ends 0.10 s apart = 0.7% of the window") + """

One wall clock, 13.309 s from reset, 368,105 events. The two encoders close 94 and 248
cycles from the end of the traced window.""")
code("lab.b156_figure(lanes)")
md("""Both lanes carry model work in every bucket after the boot, and the two dashed
rules at the right are where each lane's last `mb_pext_conv` frame ends.

**Sizing a trace buffer from this.** A busy hart 0 emits **0.0443 bytes per core cycle**
(1.77 MB/s). The same lane measured over a run where it idled 80 % of the window gives
**0.0084**, five times lower, because a spin loop is cheap in trace bytes. Size a TACIT
buffer from a window the lane worked through.""")

# ======================================================================================
# Units 4 and 5
# ======================================================================================
md("""---

## Unit 4 · Agentic Optimization in ModelBlaster

**Part runs today · on this instance.** An LLM rewrites one int8 kernel for the board's
MBP instructions, Spike scores every candidate bit-exact against the reference, and the
board then runs the round's best kernel three ways — the reference, the new kernel, and
the new kernel with MBP switched off — so the accelerator is priced apart from the
rewritten loop.

This unit is its own pair of notebooks on your seat, `mb_lab.ipynb` and
`mb_lab_solved.ipynb`. The solved copy redraws a complete run from the recorded runs
shipped beside it: the LLM's rounds, the kernels it wrote, the conversation it had, and
the verdict. It needs nothing but the seat. Running it live on your own board and LLM key
is not ready yet.""")

md("""---

## Unit 5 · XPU-RT: Scheduling Multi-Model Workloads to Heterogeneous SoCs

**Part runs today · on this instance.** You run the scheduler. RiskyBird, the robot these
schedules are for, is one or two boards shown from the front.""")

md("""### 5.1 Solve a real schedule on your instance

*On the instance.* XPU-RT places every operator of a network onto the devices of a
heterogeneous machine. Eight dispatches, solved to optimality in under a second.""")
code('''lab.sh("""source /etc/profile.d/xpurt.sh && cd $XPURT_ROOT && \\
XPURT_CPSAT_WORKERS=1 $XPURT_PYTHON scripts/run_xpurt_schedule.py \\
  --networks-json data/toplevel/networks_b154_gate.json \\
  --solver cpsat --profiled --cpsat-time-limit 60""", timeout=300)''')
md("Expected, on the last line but one:\n\n" + fence(
    "makespan_us=237.87  op_deadline_miss=0 (dispatches, NOT instances)  cross_dev=0  solver_s=0.377")
   + """

The operator durations are measured on this silicon, so `makespan_us` is 237.87 on four
vCPUs and on a 48-core workstation alike. `solver_s` is this machine's wall clock and
will differ.""")
code("""import json, os
root = os.environ.get("XPURT_ROOT", "/opt/xpurt/XPU-RT")
m = json.load(open(os.path.join(
    root, "schedules/scheduled_networks_b154_gate_cpsat_profiled_metrics.json")))
lab.expect("makespan_us", round(m["makespan_us"], 2), 237.87)""")
md("""And the schedule it found — every operator placed on a device of the machine:""")
code("""from IPython.display import Image
Image(filename=os.path.join(root, "plots/networks_b154_gate_cpsat_profiled.png"))""")
md(fixes_table([
    ("Nothing in the log for minutes", "Normal: Python buffers its output. The solve is under three seconds once it starts."),
]))

md("""### 5.2 Two networks sharing two harts and one memory system (Lab B157, `L402`)

5.1 scheduled eight dispatches. This is the same solver on the problem a robot actually
has: a speech model that must finish, a detector that must not miss its frame, two harts
that hold different instruction sets, and a memory system they share. SignDetLite is
**periodic at 1000 ms** (1.00 fps, 4 instances); Moonshine is the non-periodic job
measured around it. 44 cells: 9 heuristics + 2 CP-SAT paths × {none, dram} × {plain,
compact}.

Everything below reads the committed golden — all 44 rows, the four refusals and the
compaction table — so it needs no XPU-RT, no solve and no artifacts. Solving a cell
yourself is 5.3.""")
code('''import json
golden_path = lab.repo_file("expected/xpurt_coloc2m_b157.json")
if golden_path is None:
    raise SystemExit("This checkout does not ship expected/xpurt_coloc2m_b157.json -- "
                     "the B157 golden lives in the curated public tree.")
g = json.load(open(golden_path))
for name, cell in g["headline_cells"].items():
    if name.startswith("_"):
        continue
    print(f'{name:<32} {cell["moonshine_end_ms"]:>9,.2f} ms   '
          f'windows {cell["windows_landed"]:<6} '
          f'{"PASSES" if cell["real_time_ok"] else "FAILS"}')
print()
print("cells", g["totals"]["cells"], "| dispatches per schedule",
      f'{g["totals"]["dispatches_per_schedule"]:,}',
      "| machine overlaps", g["totals"]["machine_overlaps"])''')
md("""Expected:

""" + fence(
    "best_heuristic_makespan           4,251.12 ms   windows 1 of 4  FAILS\n"
    "only_heuristic_landing_all_four   6,076.00 ms   windows 4 of 4  FAILS\n"
    "cpsat_none_plain                  3,649.06 ms   windows 4 of 4  PASSES\n"
    "cpsat_none_compact                3,272.91 ms   windows 4 of 4  PASSES\n"
    "cpsat_dram_plain                  5,412.25 ms   windows 4 of 4  FAILS\n"
    "cpsat_dram_compact                3,992.30 ms   windows 4 of 4  PASSES\n\n"
    "cells 44 | dispatches per schedule 2,285 | machine overlaps 0"))
code("lab.b157_figure(g)")
md("""**The top panel.** Under measured DRAM contention CP-SAT lands Moonshine at
5,412.25 ms, past the 4,000 ms reference. The left-shift compaction post-pass takes it to
**3,992.30 ms — 1,419.95 ms recovered, with `op_deadline_miss_count` 0 in both**, so no
detector frame was traded for it. On the uncontended arm the same pass is worth
376.15 ms.

**The bottom panel is why the solver is needed.** All 36 heuristic cells fail the
real-time test, and they fail it in two different ways: the fastest (`heft` =
`critical_path`, 4,251.12 ms) misses three of the four detector windows, and `edf` — the
only policy landing all four — ends at 6,076.00 ms, 2,076 ms past the reference.
Compaction recovers **0.000 ms and moves 0 of 2,285 dispatches in all 18
plain-vs-compact pairs**: a list scheduler already places each op at its earliest
feasible instant, so there is nothing to left-shift. The pass only pays where a solver
leaves joint idle.

The three rates together: **0.25 fps heuristic, 1.0 fps achieved, 2.62 fps capacity.**""")
code('''for c in g["compaction"][:4]:
    print(f'{c["scheduler"]:<22} {c["contention"]:<5} '
          f'{c["recovered_ms"]:>10,.3f} ms  {c["dispatches_moved"]:>5,} moved  {c["result"]}')
print(f'\\n{len(g["refused"])} cells were REFUSED, not reported:')
for r in g["refused"]:
    print(f'  {r["scheduler"]} {r["contention"]}/{r["compaction"]}: '
          f'{r["exclusion_violations"]} exclusion violations, '
          f'withheld makespan {r["withheld_makespan_ms"]:,.2f} ms')''')
md("""Four cells placed 74 dispatches on machines their dispatch graph forbids —
`linear_s8` on a core the graph marks infeasible, `permute4_s8` on the hart with no
P-extension. **Both are an illegal instruction on silicon**, and nothing in the artifact
reads as wrong: the emitted durations look ordinary. This CP-SAT path builds its model
from a context that never reads `infeasible_combinations`, so the forbidden cells arrive
as cheap legal options.

Refusing them cost nothing in makespan: the invalid cells were mostly slower as well as
illegal (warmbest is shorter in only one of four pairs), and the shortest Moonshine end
in all 44 cells, 3,272.91 ms, is valid. Invalidity is invisible in the makespan column,
in either direction.""")

md("""### 5.3 Solve one cell yourself

*Optional, and it needs more than the repository.* The full 44-cell sweep is hours of
CP-SAT; one heuristic cell is about half a minute and reproduces its golden row exactly.

You need an XPU-RT checkout (`XPURT_ROOT`) and an interpreter with `ortools`
(`XPURT_PY`), neither of which is vendored here. Without them, 5.2 already carries the
whole result.

The sweep as shipped does not produce a schedule. It gives each cell a symlink farm of
XPU-RT with this repository's data laid over it and solves inside that farm, but
`run_xpurt_schedule.py` takes its base path from the script's own location rather than
from the working directory, so the solve looks for the data in the XPU-RT checkout and
every network fails with `dispatch_deps_path not found at ''`. The sweep exits 0 either
way, so read the cell's output and not the exit code.""")
code('''import os, glob
sweep = lab.repo_file("scripts/12_xpurt_coloc_sweep.sh")
root, py = os.environ.get("XPURT_ROOT"), os.environ.get("XPURT_PY")
cell_dir = None
if not sweep:
    print("STUB: no scripts/12_xpurt_coloc_sweep.sh in this checkout -- nothing was run.")
elif not (root and py and os.path.isdir(root) and os.access(py, os.X_OK)):
    print("Not run: set XPURT_ROOT to an XPU-RT checkout and XPURT_PY to an "
          "interpreter that has ortools. 5.2 above needs neither.")
else:
    repo = sweep.parent.parent
    lab.sh(f"cd {repo} && STAGE=heur POLICIES=fifo CONT_ARMS=none COMPACT_ARMS=plain "
           f"JOBS=1 {sweep}", timeout=900, quiet=True)
    got = glob.glob(f"{repo}/out/b157/schedules/none/plain/*_metrics.json")
    cell_dir = f"{repo}/out/b157/work/fifo_none_plain"
    print("the sweep produced a schedule" if got else
          "the sweep produced NO schedule -- the base-path trap above. "
          "The next cell runs the same solve the way that works.")''')
md("""The farm the sweep built is still there, and the only thing wrong with it was the
path the interpreter was handed. Naming the copy of the script **inside the cell** makes
the base path the cell, where the data is:""")
code('''if cell_dir and os.path.isdir(cell_dir):
    spec = lab.repo_file(
        "fpga/pynq-z2/xpurt/networks_pynqz1_coloc2m_sdp_b4_T1000.json")
    inject = lab.repo_file("scripts/lib/b157_inject")
    r = lab.sh(
        f"cd {cell_dir} && env -u XPURT_NO_COMPACT -u XPURT_COMPACT "
        f"PYTHONPATH={inject} XPURT_CPSAT_PYTHON={py} XPURT_CPSAT_WORKERS=1 "
        f"{py} {cell_dir}/scripts/run_xpurt_schedule.py "
        f"--networks-json {spec} --scheduler fifo --profiled",
        timeout=900, quiet=True)
    for line in r.stdout.splitlines():
        if "makespan_us" in line:
            print(line.strip())
    row = next(x for x in g["rows"] if x["policy"] == "fifo"
               and x["contention"] == "none" and x["compaction"] == "plain")
    print(f'golden says moonshine_end_ms={row["moonshine_end_ms"]}, '
          f'late_detector_dispatches={row["late_detector_dispatches"]}')
else:
    print("No cell farm to run in -- 5.2 carries the result without it.")''')
md("""Expected, and it is the golden row to the digit:

""" + fence("makespan_us=6339.28  op_deadline_miss=21 (dispatches, NOT instances)  "
            "cross_dev=1438  solver_s=0.484\n"
            "golden says moonshine_end_ms=6339.28, late_detector_dispatches=21") + """

Same interpreter, same spec and same data as the cell above it; only the path the script
was named by differs.""")

nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}
NB.write_text(json.dumps(nb, indent=1) + "\n")
print(f"wrote {NB}  ({len(cells)} cells)")
