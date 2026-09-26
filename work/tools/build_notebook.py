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
their own instance and the board connects to that instance. Steps 0.2 and 1.3 are
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

`lab.board()` sends one of a fixed set of verbs to the card; `lab.board("help")` lists them. Nothing
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

**Runs on your board today.** Two short steps. Every unit after this assumes them.""")

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
md("Expected:\n\n" + fence("-rw-rw-r-- 1 ubuntu ubuntu 56888 ... zephyr.bin"))

md("""### 1.3 Upload the image to the PYNQ board and run it

*On the board.* Upload the image you just built to the card, load the PL and start the
guest, then read the console back.""")
code('lab.board_put("/home/ubuntu/out/boot_info/zephyr/zephyr.bin")')
md("""Expected. The md5 is computed on the card and is the one step 1.2 built, so a
truncated push shows up here rather than as a dead guest:

""" + fence("""{
  "ok": true,
  "stored": "zephyr.bin",
  "bytes": 56888,
  "md5": "c830b4b6a0d1e4f7c2b9358e6a1d70cf"
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

`soc_magic=0x5A5A0038` is the bitstream Unit 1 wants. `nonce` is yours and will
differ; the OLED restarts at `up 0:00`.

""" + fixes_table([
    ("`console_bytes: 0`", "Stop and tell an instructor. Do not retry and do not reboot."),
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

**Runs today · on this instance.** ModelBlaster takes a PyTorch model and produces C for
this SoC: it reads the network into an IR, quantises it to int8, chooses a kernel for
every operator from the ones the target has, and emits the model, the weights and the
kernels as source. This unit runs that flow on SignDetLite — a small colour sign detector
— one stage at a time, follows its dominant operator all the way down, and finishes with
the frame measured on silicon on two different backends.

The weights in a clone are deterministic random ones (`signdet/random_weights.py`, numpy
PCG64, seed 144). The graph, the kernel choices, the quantisation grid and the cycle
counts are the same either way; what random weights cannot do is detect a sign, so read
the output bytes as arithmetic and not as a detection.""")

md("""### 2.1 Lower the model, and read the IR

*On the instance, about five seconds.* `scripts/84_signdet_lower.sh` runs the three
ModelBlaster stages — `extract_graph`, `generate_skeleton`, `generate_kernels` — and
writes the IR and the generated C under `out/signdet/`.""")
code('''import json, hashlib, pathlib
repo = lab.repo_root()
r = lab.sh(f"cd {repo} && ./scripts/84_signdet_lower.sh", timeout=900, head=0, tail=14)
GEN = pathlib.Path(next(l.split()[-1] for l in reversed(r.stdout.splitlines())
                        if l.strip().startswith("gen ")))
IR = GEN.parent / "ir"''')
md("""Expected, at the end:

""" + fence(
    "==> op coverage\n"
    "    conv2d_s8_pc       curated[pext]        pext_patch_dot8_pc\n"
    "    permute4_s8        curated[pext_nl]     pext_block\n"
    "    softmax_s8         curated[pext_nl]     pext_int_memo2\n"
    "    MAC 7532544   est 9.61 M cycles   est 240.2 ms @ 40 MHz  (1.2755 cycles per MAC, measured)\n"
    "    all ops curated, no reference-C fallback") + """

The first stage parsed the PyTorch module into `ir/graph.json`. That file is the whole
network: the tensors with their shapes and quantisation scales, the operators with their
shapes, and the order they run in.""")
code('''g = json.load(open(IR / "graph.json"))
print(f'{g["name"]}  {g["quant"]}  {len(g["ops"])} operators, '
      f'{len(g["dispatches"])} of them dispatched to a kernel')
print(f'input  {g["input"]["tensor"]:<8} {g["tensors"][g["input"]["tensor"]]["shape"]}')
print(f'output {g["output"]["tensor"]:<8} {g["tensors"][g["output"]["tensor"]]["shape"]}')
print()
for n in g["ops"]:
    shape = " ".join(f"{k}={v}" for k, v in n.get("shape", {}).items())
    print(f'  {n["name"]:<9} {n["op"]:<14} {n["inputs"][0]:>8} -> {n["outputs"][0]:<9} {shape}')
print()
print("graph.json md5", hashlib.md5((IR / "graph.json").read_bytes()).hexdigest())''')
md("""Expected:

""" + fence(
    "signdet_b144  int8  9 operators, 8 of them dispatched to a kernel\n"
    "input  x        [1, 3, 64, 64]\n"
    "output softmax  [64, 3]\n"
    "\n"
    "  conv1     conv2d_s8_pc          x -> relu      N=1 IC=3 IH=64 IW=64 OC=16 OH=32 OW=32 ...\n"
    "  conv2     conv2d_s8_pc       relu -> relu_1    N=1 IC=16 IH=32 IW=32 OC=32 OH=16 OW=16 ...\n"
    "  conv3     conv2d_s8_pc     relu_1 -> relu_2    N=1 IC=32 IH=16 IW=16 OC=32 OH=16 OW=16 ...\n"
    "  conv4     conv2d_s8_pc     relu_2 -> relu_3    N=1 IC=32 IH=16 IW=16 OC=64 OH=8 OW=8 ...\n"
    "  conv5     conv2d_s8_pc     relu_3 -> relu_4    N=1 IC=64 IH=8 IW=8 OC=64 OH=8 OW=8 ...\n"
    "  head      conv2d_s8_pc     relu_4 -> head      N=1 IC=64 IH=8 IW=8 OC=3 OH=8 OW=8 ...\n"
    "  permute   permute4_s8        head -> permute   d0=1 d1=3 d2=8 d3=8 p0=0 p1=2 p2=3 p3=1\n"
    "  reshape   view            permute -> reshape   n=192\n"
    "  softmax   softmax_s8      reshape -> softmax   M=64 K=3\n"
    "\n"
    "graph.json md5 70a0ac6d37a0bf99a6733f40e5e64073") + """

Nine operators, eight dispatches: `reshape` is a `view`, which changes how the bytes are
read and does not move them, so no kernel is generated for it. The ReLUs are gone too —
they were folded into the convolutions' activation clamp, which is why `conv1` writes a
tensor called `relu`.

`conv2d_s8_pc` is the per-channel int8 convolution, and it is the operator the rest of
this unit follows: six of the nine are one.""")

md("""### 2.2 See which kernel each operator got

*On the instance.* `generate_kernels` matches every operator in the IR against the kernels
curated for this target and writes what it chose to `kernel_picks.json`. That file is the
compilation decision: the operator, where the kernel came from, which algorithm, and the
file that will be compiled in.""")
code('''picks = json.load(open(GEN / "kernel_picks.json"))["picks"]
for op in sorted(picks):
    n = sum(1 for o in g["ops"] if o["op"] == op)
    print(f'{op:<14} {picks[op]["source"]:<18} {picks[op]["algorithm"]}')
    print(f'{"":<14} serves {n} of the {len(g["ops"])} operators')
    print(f'{"":<14} {picks[op]["path"]}')
mac = sum(o["shape"]["OH"] * o["shape"]["OW"] * o["shape"]["OC"] * o["shape"]["IC"]
          * o["shape"]["KH"] * o["shape"]["KW"]
          for o in g["ops"] if o["op"] == "conv2d_s8_pc")
print(f'\\nconv2d_s8_pc carries {mac:,} multiply-accumulates -- every one in the graph.')''')
md("""Expected:

""" + fence(
    "conv2d_s8_pc   curated[pext]      pext_patch_dot8_pc\n"
    "               serves 6 of the 9 operators\n"
    "               .../out/signdet/kernels_board/pext/pext_conv2d_s8_pc_pext_patch_dot8_pc.c\n"
    "permute4_s8    curated[pext_nl]   pext_block\n"
    "               serves 1 of the 9 operators\n"
    "               .../out/signdet/kernels_board/pext_nl/pext_nl_permute4_s8_pext_block.c\n"
    "softmax_s8     curated[pext_nl]   pext_int_memo2\n"
    "               serves 1 of the 9 operators\n"
    "               .../out/signdet/kernels_board/pext_nl/pext_nl_softmax_s8_pext_int_memo2.c\n"
    "\n"
    "conv2d_s8_pc carries 7,532,544 multiply-accumulates -- every one in the graph.") + """

**Following `conv2d_s8_pc` down.** It started as a `torch.nn.Conv2d`. `extract_graph` read
it as a convolution, quantised it per output channel — which is what the `_pc` means — and
wrote it into the IR with its shape and its scales. Selection then offered that operator
every algorithm registered for it on this target, and `pext_patch_dot8_pc` won: a kernel
that gathers each output pixel's input patch into a contiguous vector, repacks the weights
to match, and reduces them eight bytes at a time with the SoC's `MBP.DOT8` instruction.

This is the loop it reduces in. Four output channels are accumulated at once against one
gathered patch, so the patch is loaded once and used four times:""")
code('''src = pathlib.Path(picks["conv2d_s8_pc"]["path"]).read_text().splitlines()
first = next(i for i, l in enumerate(src) if "for (b = 0; b < quads" in l)
for i, line in enumerate(src[first:first + 17], start=first + 1):
    print(f"{i:>4}  {line}")''')
md("""Expected:

""" + fence(
    "281      for (b = 0; b < quads; b++) {\n"
    "282          const int8_t *w = wp + (size_t)b * G * 32;\n"
    "283          const int8_t *pp = p;\n"
    "284          int64_t a0 = bias ? bias[i + 0] : 0;\n"
    "285          int64_t a1 = bias ? bias[i + 1] : 0;\n"
    "286          int64_t a2 = bias ? bias[i + 2] : 0;\n"
    "287          int64_t a3 = bias ? bias[i + 3] : 0;\n"
    "288  \n"
    "289          for (g = 0; g < G; g++) {\n"
    "290              const int64_t x = MB_PEXT_LD8(pp);\n"
    "291              a0 += mb_pext_dot8(x, MB_PEXT_LD8(w));\n"
    "292              a1 += mb_pext_dot8(x, MB_PEXT_LD8(w + 8));\n"
    "293              a2 += mb_pext_dot8(x, MB_PEXT_LD8(w + 16));\n"
    "294              a3 += mb_pext_dot8(x, MB_PEXT_LD8(w + 24));\n"
    "295              pp += 8;\n"
    "296              w += 32;\n"
    "297          }", "c") + """

`mb_pext_dot8` is one instruction. No assembler knows the encoding, so it is written out
with GAS's `.insn` directive:""")
code('''pext_h = lab.repo_file("fpga/pynq-z2/sw/pext.h")
lines = pext_h.read_text().splitlines()
first = next(i for i, l in enumerate(lines) if "mb_pext_dot8(" in l) - 2
for i, line in enumerate(lines[first:first + 8], start=first + 1):
    print(f"{i:>4}  {line}")''')
md("""Expected:

""" + fence(
    "173  #if MB_PEXT_HW\n"
    "174\n"
    "175  static inline int64_t mb_pext_dot8(int64_t a, int64_t b)\n"
    "176  {\n"
    "177  \tint64_t r;\n"
    "178  \n"
    '179  \t__asm__(".insn r 0x0b, 0, 0, %0, %1, %2" : "=r"(r) : "r"(a), "r"(b));\n'
    "180  \treturn r;", "c") + """

Opcode `0x0b` is RISC-V's custom-0 space. Two 64-bit registers hold eight int8 values
each; the instruction multiplies them pairwise and sums the eight products. The full files
are `fpga/pynq-z2/modelblaster/kernels/pext/pext_conv2d_s8_pc_pext_patch_dot8_pc.c` and
`fpga/pynq-z2/sw/pext.h`.""")

md("""### 2.3 Read the float-to-int conversion the compiler chose

*On the instance.* The model trained in float32 and this SoC has no FPU, so every tensor
and every weight becomes an int8 and a scale. Activation scales are per tensor and sit in
the IR; weight scales are per output channel and are folded into a fixed-point multiply
the kernel applies to each accumulator. That multiply is the compiled form of the float
arithmetic, and it is in `weights.c` as two int32 arrays per convolution:""")
code('''import re
w = (GEN / "weights.c").read_text().splitlines()
arrays, total, values, i = [], 0, 0, 0
while i < len(w):
    m = re.search(r"(\\w+_output_(?:multiplier|shift)_per_oc_\\w+)\\[(\\d+)\\]", w[i])
    if m:
        j = i
        while "};" not in w[j]:
            j += 1
        arrays.append((m.group(1), int(m.group(2)), i + 1, j + 1))
        total += j - i + 1
        values += int(m.group(2))
        i = j + 1
    else:
        i += 1
print(f"weights.c is {len(w):,} lines. {total} of them are the requantise grid: "
      f"{len(arrays)} arrays, {values} int32 values.\\n")
for name, n, a, b in arrays:
    print(f"  line {a:>5}  {name:<52} [{n}]")
print()
print("\\n".join(w[arrays[0][2] - 1:arrays[1][3]]))''')
md("""Expected, ending in `conv1`'s two arrays:

""" + fence(
    "weights.c is 4,477 lines. 52 of them are the requantise grid: 12 arrays, 422 int32 values.\n"
    "\n"
    "  line    10  signdet_b144_conv1_output_multiplier_per_oc_roccmoon [16]\n"
    "  line    14  signdet_b144_conv1_output_shift_per_oc_roccmoon      [16]\n"
    "  line    53  signdet_b144_conv2_output_multiplier_per_oc_roccmoon [32]\n"
    "  ...\n"
    "  line  4460  signdet_b144_head_output_shift_per_oc_roccmoon       [3]\n"
    "\n"
    "const int32_t signdet_b144_conv1_output_multiplier_per_oc_roccmoon[16] = {\n"
    "    1591242990, 1736033205, 1084498289, 1308506831, ...\n"
    "};\n"
    "\n"
    "const int32_t signdet_b144_conv1_output_shift_per_oc_roccmoon[16] = {\n"
    "    8, 9, 8, 8, 9, 9, 8, 9, 9, 9, 8, 9, 8, 9, 9, 9,\n"
    "};") + """

Those two numbers per channel are the float multiply. The convolution accumulates in
int32, and the kernel then computes `(acc * multiplier + 2^30) >> 31 >> shift`, which is
the fixed-point form of multiplying by the real number *M* = input scale × weight scale ÷
output scale. Reversing it recovers the scale the quantiser picked for each channel:""")
code('''import numpy as np
z = np.load(IR / "weights.npz")
mult, shift = z["conv1.output_multiplier_per_oc"], z["conv1.output_shift_per_oc"]
s_in = g["tensors"]["x"]["quant"]["scale"]
s_out = g["tensors"]["relu"]["quant"]["scale"]
print(f"conv1   input scale {s_in:.12f} (= 1/{1/s_in:.0f})   "
      f"output scale {s_out:.12f}\\n")
print("  oc   multiplier  shift          M   implied weight scale   max |W| that fits")
for oc in range(4):
    M = float(mult[oc]) / 2**31 / 2**int(shift[oc])
    s_w = M * s_out / s_in
    print(f"  {oc:>2}   {mult[oc]:>10}  {shift[oc]:>5}   {M:.8f}         {s_w:.8f}   "
          f"{s_w * 127:.6f}")
print(f"\\n  ... and 12 more channels. Every channel saturates at "
      f"{int(np.abs(z['conv1.weight_q']).max())}, which is what per-channel means: the "
      f"multiplier absorbs the range, not the weights.")''')
md("""Expected:

""" + fence(
    "conv1   input scale 0.007874015748 (= 1/127)   output scale 0.014415336406\n"
    "\n"
    "  oc   multiplier  shift          M   implied weight scale   max |W| that fits\n"
    "   0   1591242990      8   0.00289445         0.00529902   0.672975\n"
    "   1   1736033205      9   0.00157891         0.00289059   0.367105\n"
    "   2   1084498289      8   0.00197269         0.00361150   0.458660\n"
    "   3   1308506831      8   0.00238016         0.00435747   0.553399\n"
    "\n"
    "  ... and 12 more channels. Every channel saturates at 127, which is what per-channel\n"
    "  means: the multiplier absorbs the range, not the weights.") + """

Channel 0's filter reaches 0.673 and channel 1's reaches 0.367, and both use the whole
int8 range. One scale for the whole tensor would have given channel 1 about half the
resolution.

**Where the grid comes from.** The weight scales follow from the weights, but the
activation scales do not: they are observed, by running calibration frames through the
float model and recording the range each tensor visits. So the grid moves when the number
of calibration frames moves. `scripts/84` calibrates on 64 frames; lower the same
checkpoint again on 256 and compare:""")
code('''r256 = lab.sh(f"cd {repo} && ./scripts/84_signdet_lower.sh --name signdet_cal256 --ncal 256",
              timeout=900, quiet=True)
GEN256 = pathlib.Path(next(l.split()[-1] for l in reversed(r256.stdout.splitlines())
                           if l.strip().startswith("gen ")))
IR256 = GEN256.parent / "ir"
print("graph.json md5   64 frames", hashlib.md5((IR / "graph.json").read_bytes()).hexdigest())
print("                256 frames", hashlib.md5((IR256 / "graph.json").read_bytes()).hexdigest())
a, b = np.load(IR / "weights.npz"), np.load(IR256 / "weights.npz")
print(f"\\n{'array':<34}{'entries':>9}{'differ':>9}")
for k in a.files:
    if "output_" in k:
        print(f"{k:<34}{a[k].size:>9}{int((a[k] != b[k]).sum()):>9}")
print("\\nweight_q arrays identical:",
      all((a[k] == b[k]).all() for k in a.files if k.endswith("weight_q")))''')
md("""Expected:

""" + fence(
    "graph.json md5   64 frames 70a0ac6d37a0bf99a6733f40e5e64073\n"
    "                256 frames 5e3ac0705fe5ef1beb7a35afc2b15138\n"
    "\n"
    "array                               entries   differ\n"
    "conv1.output_multiplier_per_oc           16       16\n"
    "conv1.output_shift_per_oc                16        3\n"
    "conv2.output_multiplier_per_oc           32       32\n"
    "conv2.output_shift_per_oc                32        1\n"
    "conv3.output_multiplier_per_oc           32       32\n"
    "conv3.output_shift_per_oc                32        0\n"
    "conv4.output_multiplier_per_oc           64       64\n"
    "conv4.output_shift_per_oc                64       20\n"
    "conv5.output_multiplier_per_oc           64       64\n"
    "conv5.output_shift_per_oc                64        4\n"
    "head.output_multiplier_per_oc             3        3\n"
    "head.output_shift_per_oc                  3        1\n"
    "\n"
    "weight_q arrays identical: True") + """

Every int8 weight is the same and every multiplier changed. The requantise grid is a
property of the calibration set as much as of the network, so the calibration count
belongs with the artifact: two trees that differ only in it produce different `graph.json`
md5s, which is how you tell them apart afterwards.""")

md("""### 2.4 Compile the same graph for the other backend

*On the instance, about five seconds.* Kernel selection is where ModelBlaster targets one
machine rather than another: the same IR reaches a different set of kernels and binds to
whichever the target has. `--scalar` lowers the arm with no accelerator kernels available,
so every operator falls to ModelBlaster's own reference C.""")
code('''rs = lab.sh(f"cd {repo} && ./scripts/84_signdet_lower.sh --name signdet_scalar --scalar",
            timeout=900, head=0, tail=8)
GENS = pathlib.Path(next(l.split()[-1] for l in reversed(rs.stdout.splitlines())
                         if l.strip().startswith("gen ")))
scalar = json.load(open(GENS / "kernel_picks.json"))["picks"]
print()
for op in sorted(picks):
    print(f'{op:<14} pext    {picks[op]["source"]:<18} {picks[op]["algorithm"]}')
    print(f'{"":<14} scalar  {scalar[op]["source"]:<18} {scalar[op]["algorithm"]}')
print()
for f in ("ir/graph.json", "gen/weights.c", "gen/kernels.c"):
    x = hashlib.md5((GEN.parent / f).read_bytes()).hexdigest()
    y = hashlib.md5((GENS.parent / f).read_bytes()).hexdigest()
    print(f'{f:<16} {"same" if x == y else "differs"}   {x}  {y}')
print(f'\\nkernels.c   {len(open(GEN / "kernels.c").read().splitlines()):>5} lines on pext, '
      f'{len(open(GENS / "kernels.c").read().splitlines()):>4} on scalar')''')
md("""Expected:

""" + fence(
    "conv2d_s8_pc   pext    curated[pext]      pext_patch_dot8_pc\n"
    "               scalar  reference          None\n"
    "permute4_s8    pext    curated[pext_nl]   pext_block\n"
    "               scalar  reference          None\n"
    "softmax_s8     pext    curated[pext_nl]   pext_int_memo2\n"
    "               scalar  reference          None\n"
    "\n"
    "ir/graph.json    same   70a0ac6d37a0bf99a6733f40e5e64073  70a0ac6d37a0bf99a6733f40e5e64073\n"
    "gen/weights.c    same   e706082b34c472af1e9ad072eb5b88b4  e706082b34c472af1e9ad072eb5b88b4\n"
    "gen/kernels.c    differs   19d585027f49cc808b2b0339e2dfaf24  3c9bcaf92dec52668773cfaf41045209\n"
    "\n"
    "kernels.c    1382 lines on pext,  119 on scalar") + """

Same graph, same weights, same requantise grid — 1,382 lines of kernel against 119. This
is the convolution the scalar arm bound, the six nested loops any C implementation would
write:""")
code('''ref = (GENS / "kernels.c").read_text().splitlines()
first = next(i for i, l in enumerate(ref) if "acc +=" in l) - 11
for i, line in enumerate(ref[first:first + 16], start=first + 1):
    print(f"{i:>4}  {line}")''')
md("""Expected:

""" + fence(
    " 25                  for (int ow = 0; ow < OW; ow++) {\n"
    " 26                      int32_t acc = bias ? bias[oc] : 0;\n"
    " 27                      for (int ic = 0; ic < IC; ic++) {\n"
    " 28                          for (int kh = 0; kh < KH; kh++) {\n"
    " 29                              int ih = oh * SH - PH + kh;\n"
    " 30                              if (ih < 0 || ih >= IH) continue;\n"
    " 31                              for (int kw = 0; kw < KW; kw++) {\n"
    " 32                                  int iw = ow * SW - PW + kw;\n"
    " 33                                  if (iw < 0 || iw >= IW) continue;\n"
    " 34                                  int32_t iv = "
    "(int32_t)input[((n*IC + ic)*IH + ih)*IW + iw] + input_offset;\n"
    " 35                                  int32_t wv = "
    "(int32_t)weight[((oc*IC + ic)*KH + kh)*KW + kw] + filter_offset;\n"
    " 36                                  acc += iv * wv;\n"
    " 37                              }\n"
    " 38                          }\n"
    " 39                      }\n"
    " 40                      int64_t prod = ((int64_t)acc * (int64_t)mult + (1LL << 30)) >> 31;",
    "c") + """

One `acc += iv * wv` per multiply-accumulate, against one `mb_pext_dot8` per eight. The
generated file is `out/signdet_scalar/gen/kernels.c`; the accelerated one it replaces is
`fpga/pynq-z2/modelblaster/kernels/pext/pext_conv2d_s8_pc_pext_patch_dot8_pc.c`.""")

md("""### 2.5 The two images on the board, and where the frame went

Both arms were built with `scripts/86_signdet_board.sh` and run on a PYNQ-Z1 carrying
bitstream `0x5A5A0038` at 40 MHz. The harness prints one `MB_PEXT_OP` line per dispatch
with that dispatch's cycle count, diffs all 192 output bytes against a golden computed on
the host, and then issues one `MBP.DOT8` on hart 1 — which has no P-extension — so the
illegal-instruction trap is part of the pass.

Both runs are shipped beside this notebook in `assets/signdet_board_cycles.json`, 12 KB.
This is the console the accelerated image returned, with one line held back:""")
code('''board = json.load(open("assets/signdet_board_cycles.json"))
print(f'{board["script"]}, {board["measured"]} on {board["measured_on"]},')
print(f'bitstream {board["soc_magic"]} at {board["clk_hz"] // 10**6} MHz, '
      f'median of {board["iters"]} inferences.\\n')
for line in board["arms"]["pext"]["console"]:
    print(line[:200] + " ..." if len(line) > 200 else line)''')
md("""Expected, in part:

""" + fence(
    "MB_PEXT_BUILD model=signdet_b144 quant=int8 ops=8 iters=11 hw=1\n"
    "MB_PEXT_RUN cpu=0 mhartid=0 median=9508851 min=9504987 max=9514870 warm=9519734 "
    "mtime=9505 max_abs_err=0\n"
    "MB_PEXT_OP id=0 name=conv1 op=conv2d_s8_pc shape=N=1;IC=3;IH=64;... cycles=998077\n"
    "MB_PEXT_OP id=1 name=conv2 op=conv2d_s8_pc shape=N=1;IC=16;IH=32;... cycles=1197067\n"
    "...\n"
    "MB_PEXT_OP id=7 name=softmax op=softmax_s8 shape=M=64;K=3 cycles=51859\n"
    "   hart 0 (big): median 237.721 ms   max_abs_err=0\n"
    "MB_PEXT_NEG starting -- one MBP.DOT8 pinned to CPU 1; an illegal-instruction trap "
    "here is the PASS\n"
    "\n"
    " mcause: 2, Illegal instruction\n"
    "  mtval: d7070b\n"
    "...\n"
    "   hart 1 refused 0x00d7070b -- custom-0, funct3=0: MBP is on hart 0 only, as specified\n"
    "RESULT: PASS -- the MBP kernels ran bit-exact on hart 0 and the same instruction is "
    "illegal on hart 1") + """

`median` is the frame, taken over eleven inferences after a warm-up, so it is
steady-state and not a first-call number. The per-dispatch rows come from the same run.
Both arms, side by side:""")
code('''p, s = board["arms"]["pext"], board["arms"]["scalar"]
print(f'{"":<9}{"":<15}{"MBP kernels":>14}{"reference C":>15}{"factor":>9}')
for a, b in zip(p["ops"], s["ops"]):
    print(f'{a["name"]:<9}{a["op"]:<15}{a["cycles"]:>14,}{b["cycles"]:>15,}'
          f'{b["cycles"] / a["cycles"]:>8.2f}x')
print(f'{"frame":<24}{p["cycles"]["median"]:>14,}{s["cycles"]["median"]:>15,}'
      f'{s["cycles"]["median"] / p["cycles"]["median"]:>8.2f}x')
print(f'{"ms at 40 MHz":<24}{p["ms_at_clk"]:>14,.2f}{s["ms_at_clk"]:>15,.2f}')
print()
for arm in (p, s):
    print(f'{arm["run_name"]:<14} custom-0 instructions in the image '
          f'{arm["custom0_instructions_in_elf"]:>3}   '
          f'output vs golden: {arm["gate"]["board_vs_golden_bytes_differ"]} of 192 bytes '
          f'differ, max |d| = {arm["gate"]["max_abs_err"]}')''')
md("""Expected:

""" + fence(
    "                           MBP kernels    reference C   factor\n"
    "conv1    conv2d_s8_pc          998,077     10,687,289   10.71x\n"
    "conv2    conv2d_s8_pc        1,197,067     26,614,336   22.23x\n"
    "conv3    conv2d_s8_pc        2,764,055     50,937,793   18.43x\n"
    "conv4    conv2d_s8_pc        1,322,570     25,645,999   19.39x\n"
    "conv5    conv2d_s8_pc        3,080,706     48,684,531   15.80x\n"
    "head     conv2d_s8_pc           86,788        454,883    5.24x\n"
    "permute  permute4_s8             2,117          9,697    4.58x\n"
    "softmax  softmax_s8             51,859        889,603   17.15x\n"
    "frame                        9,508,851    163,923,052   17.24x\n"
    "ms at 40 MHz                     237.72       4,098.08\n"
    "\n"
    "pext      custom-0 instructions in the image  49   output vs golden: 0 of 192 "
    "bytes differ, max |d| = 0\n"
    "scalar    custom-0 instructions in the image   1   output vs golden: 0 of 192 "
    "bytes differ, max |d| = 0") + """

The scalar image still contains one custom-0 instruction: the negative control, the
deliberate `MBP.DOT8` on hart 1. No kernel in it issues one.

Both images returned the same 192 bytes, so 17.24x is a speed result and not a different
computation. The figure draws the same two records — the frame to scale, and each
dispatch's share of its own frame:""")
code("lab.kernel_speedup_figure(board)")
md("""**The five 3x3 convolutions are the frame on both backends** — 98.46 % of it
accelerated, 99.17 % not — so the accelerator does not change what to optimise next. It
does change the shape. `conv1` is 10.50 % of the accelerated frame and 6.52 % of the
scalar one, because it gains 10.71x where `conv2` gains 22.23x: `conv1` reduces over 3
input channels and `MBP.DOT8` consumes 8 lanes at a time, so a 3-channel 3x3 patch is 27
values, the vector is padded, and three of every eight lanes do nothing. `conv2` onward
reduce over 16, 32 and 64 channels, which pack exactly.

`head` gains 5.24x and `permute` 4.58x for a different reason. At 86,788 and 2,117 cycles
they are 0.91 % and 0.02 % of the frame, and per-dispatch setup is a larger share of what
they do than the loop is.""")

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

md("""### Two heterogeneous harts on one timeline

**Read-and-inspect, not runnable.** `scripts/90_b156_tacit_window.sh` needs the lowered
SignDetLite tree, its eight replay frames and a board carrying bitstream `0x5A5A0039`,
none of which is in this repository, and the 45.7 MB merged trace it produced is not
shipped either. What is shipped is the measured lane table the gates were computed from:
`assets/lane_timeline.json`, 9 KB.

The two harts hold different extensions — hart 0 has the packed-SIMD path the convolution
kernels use, hart 1 is scalar — so the same frame costs them very different amounts of
time. One wall clock bounds both lanes, and the question is whether both work through the
whole window and stop together.""")
code('''import json
lanes = json.load(open("assets/lane_timeline.json"))
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
code("lab.lane_timeline_figure(lanes)")
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

md("""### 5.2 Two networks sharing two harts and one memory system

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
                     "that golden lives in the curated public tree.")
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
code("lab.schedule_comparison_figure(g)")
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
