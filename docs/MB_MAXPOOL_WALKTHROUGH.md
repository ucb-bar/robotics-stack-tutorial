# How the maxpool2d_s8 speedup works on the accelerator

The numbers in §1 to §5 come from one test run on a PYNQ-Z1 with the 0x5A5A0038 bitstream. That run
was a `--replay`, which reruns the kernel DeepSeek wrote in a recorded live run without calling the
model again. §7 describes that recorded run. All code shown here is the real code from it. A live run
of your own will write a different kernel. Across 8 live test runs we measured 3.6 to 19.7× on the
board (6 of them above 14×), with 2.9 to 11.7× of it coming from the accelerator. `mb report <run>`
gives the numbers for your run.

## 1. The op

`maxpool2d_s8` is a 2×2 max pool with stride 2 on an int8 tensor in NCHW layout, with 16 channels of
64×64 in and 16 channels of 32×32 out. Each output is the largest of four neighbouring bytes. There
is no float arithmetic and no rounding, so this is a pure integer kernel. The "before" is
ModelBlaster's reference kernel:

```c
for (int n = 0; n < N; n++) for (int c = 0; c < C; c++)
  for (int oh = 0; oh < OH; oh++) for (int ow = 0; ow < OW; ow++) {
    int8_t m = INT8_MIN;
    for (int kh = 0; kh < KH; kh++) { int ih = oh*SH - PH + kh*DH; if (ih < 0 || ih >= IH) continue;
      for (int kw = 0; kw < KW; kw++) { int iw = ow*SW - PW + kw*DW; if (iw < 0 || iw >= IW) continue;
        int8_t v = input[((n*C + c)*IH + ih)*IW + iw];
        if (v > m) m = v; } }                     // one byte, one compare, at a time
    output[((n*C + c)*OH + oh)*OW + ow] = m;
  }
```

On the board the reference takes 118 cycles per output. Each output costs four byte loads, four
compares and a fair amount of index arithmetic.

## 2. The accelerator: MBP, a packed SIMD extension on hart 0

The Rocket core on the FPGA has four custom instructions in the RISC-V custom-0 opcode space:
MBP.DOT8, MBP.MAX8, MBP.QMUL and MBP.CLIP8. `MBP.MAX8 a, b` treats each of two 64-bit registers as
eight int8 lanes and returns the maximum of each pair of lanes, which is 8 compares in one
instruction. C code reaches it through `pext.h`:

```c
int64_t mb_pext_max8(int64_t a, int64_t b);   // r.byte[i] = max(a.byte[i], b.byte[i]), i = 0..7
```

When compiled for the board, this call becomes the single custom instruction. On the machine that
checks the LLM's kernel it compiles to a C model that gives the same result bit for bit, so the same
source is verified in both places.

## 3. The LLM's kernel

For the 2×2 window, the kernel loads 8 columns of two input rows at once:

```c
int64_t r0 = MB_PEXT_LD8(row0 + iw_start);          // 8 bytes of row 2*oh      (one ld)
int64_t r1 = MB_PEXT_LD8(row1 + iw_start);          // 8 bytes of row 2*oh + 1  (one ld)
int64_t v  = mb_pext_max8(r0, r1);                  // MAX8: 8 vertical maxima
int64_t hmax = mb_pext_max8(v, (int64_t)((uint64_t)v >> 8));   // MAX8: each byte vs its right neighbour
out[ow]     = (int8_t)hmax;         out[ow + 1] = (int8_t)(hmax >> 16);   // bytes 0, 2, 4, 6
out[ow + 2] = (int8_t)(hmax >> 32); out[ow + 3] = (int8_t)(hmax >> 48);   // are the 4 outputs
```

This produces four outputs with two loads and two MAX8s, where the reference needs 16 loads and 16
compares. The kernel takes this path only when it is safe to, which means a 2×2 window with stride 2
and no padding, `IW % 8 == 0`, and an input aligned to 8 bytes (`MB_PEXT_ALIGNED8`). In every other
case it falls back to the scalar loop, because on this core a misaligned load of 8 bytes traps instead
of being emulated. The full kernel from your run is in the file named on the last line of
`mb report <run>`.

## 4. Evidence that the kernel runs on the accelerator

We check this in three independent ways.

1. The instruction is compiled in. Before an image goes to the board, the seat disassembles it and
   counts custom-0 words. The new kernel has two MAX8s in its inner loop (4,096 passes, so 8,192
   MAX8s per run), and the reference has none. Your verdict reports this as *on the accelerator: YES*.
2. The accelerator is present and working. Every image, including the reference image and the image
   with the accelerator off, also runs a fixed test. It executes one MBP instruction on hart 0, which
   has the MBP, and the same instruction on hart 1, which does not and must trap. This is the `RESULT: PASS -- the MBP kernels ran bit-exact on hart 0
   and the same instruction is illegal on hart 1` line printed after each image. It shows that the
   hardware works, but not that your kernel used it. Points 1 and 3 establish that.
3. Switching the accelerator off costs about 9×. Your board runs a third image, built from the same
   kernel with `-DMB_PEXT_HW=0`, so that each `mb_pext_max8` becomes its C model (the seat checks
   that no MBP word is left in the image). Nothing else changes between this image and the
   accelerated one.

| on the FPGA, 40 MHz | cycles | per output | |
|---|---|---|---|
| before: reference kernel | 1,936,882 | 118.2 | |
| after, accelerator OFF | 1,156,062 | 70.6 | the rewritten loop alone: 1.7× |
| after, accelerator ON | 126,717 | 7.7 | the MBP: another 9.1× |
| total | | | 15.3×, identical bit for bit |

In this run most of the speedup comes from the hardware, and your verdict shows the split for your
own run. What the LLM had to do was rearrange the loop so that eight bytes of work line up in one
register, since that is the only way the instruction can be used.

## 5. Spike and the board

The LLM's candidates are scored on spike, which models MBP.MAX8 precisely. Spike reports
1,872,464 → 80,239 cycles, or 23.3×, while the board measures 15.3×. The difference is memory.
Spike charges one cycle per instruction and has no memory timing. Once the compute is 8× denser,
loads and stores make up most of what remains, and on the real core they cost more. The reference
is limited by compute, and for it spike and the board agree to within 4%.

Correctness is checked at three levels: ModelBlaster's host verify with random inputs, the spike
golden (a real tensor through the model), and the board. On the board the guest compares its output
with the golden (`max_abs_err=0`), and the seat compares it again byte for byte. A max pool involves
no rounding, so the whole domain check that gelu gets does not apply here.

## 6. The FPGA in the loop

Scoring a candidate on spike takes seconds, which is why the LLM's candidates are scored there.
After each round, though, the round's best kernel is built for the board and run on your FPGA, once
with the MBP and once without. The next round's prompt includes what the board measured, along with
a note that the board pays for memory accesses that spike does not charge for. This is the hardware
in the loop feedback. At the end, the lab keeps the round whose kernel was fastest on the FPGA rather
than on spike. You can watch this in your terminal or in the notebook as it happens. Each kernel
tried appears as a bar, and its FPGA column fills in once the board has run it.

## 7. What the LLM was told

ModelBlaster's own prompt describes the op and its reference and says nothing about MBP. The lab
adds two things to it. The first is the MBP instruction guide (`--guide isa`), two pages that
describe the four instructions, `pext.h`, the alignment rule, and a sketch of how MAX8 applies to a
2×2 pool. You can read it with `mb show fpga/pynq-z2/modelblaster/mb_ops/pext_isa_guide.md`, or find
it in `mb calls <run>`, since it is part of each prompt. It is the same guide an engineer new to this
core would get. The second is the hardware in the loop feedback described in §6.

In the recorded run, the LLM's first kernel was correct but barely faster than the reference
(1,831,467 cycles). Round 1's two optimize candidates reached 149,452 and 80,241 cycles, and round 2
did not improve on that (80,239). The run took 7 calls in total. `mb calls <run>` lists every prompt
and answer from your own run.

## Your turn: write the accelerator kernel yourself (≈2 min per try)

In this exercise you start from the unoptimized kernel in a file and edit it, and every try of your
kernel runs on your FPGA. The board runs the reference, your kernel, and your kernel with the MBP
switched off, then reports whether your kernel uses the accelerator and how fast it is.

    mb start maxpool2d_s8
    mb edit maxpool2d_s8
    mb try maxpool2d_s8

- `start` copies the starting kernel to `your-kernel/maxpool2d_s8.c` in the lab's folder. That file
  is ModelBlaster's reference (118 cycles per output), and its header lists the MBP rules and four
  hints, one at a time.
- `edit` opens it in `nano` (Ctrl-O saves, Ctrl-X exits).
- `try` checks the kernel and runs it. A kernel that does not compile, or whose output differs from
  the reference on spike, is stopped in about 30 s, before it reaches the board, with a message
  saying why.

Aim for `on the accelerator: YES` and under 10 cycles per output (15× or more).

If you would rather start from the LLM's kernel and improve it, `mb start maxpool2d_s8 <run>` copies
the kernel from one of your LLM runs (`mb list` shows them). Can you beat it?

A solution is in `fpga/pynq-z2/modelblaster/mb_ops/exercises/maxpool2d_s8_solution.c` and runs at
15.3× on the FPGA. Read it with `mb show …`, or run it with
`mb try maxpool2d_s8 fpga/pynq-z2/modelblaster/mb_ops/exercises/maxpool2d_s8_solution.c`.
The starting kernel, `maxpool2d_s8_start.c`, is in the same directory.

## More to try (5 to 8 minutes each)

1. Run without the MBP instruction guide. `mb go maxpool2d_s8 --guide modelblaster` sends only
   ModelBlaster's prompt. In a test run the LLM stayed at 1.0×, because it cannot use an instruction
   it has not been told about. Check the *on the accelerator* line in your verdict.
2. A kernel that leans harder on the accelerator. `mb go linear_s8` is an int8 matrix multiply built
   on MBP.DOT8 (eight multiply and add operations per instruction), QMUL and CLIP8. It reached 11.8×
   on the board. How much of that is the accelerator? Your verdict will say.
3. A speedup with no accelerator. `mb go gelu_s8` gets 43 to 49× without a single MBP instruction,
   because the LLM replaces 16,384 `erff` calls with 256. `mb show docs/MB_GELU_WALKTHROUGH.md`
   explains how. After reading it, think about where the accelerator could help.
