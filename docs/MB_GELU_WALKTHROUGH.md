# How the gelu_s8 speedup works

Every number in §1 to §5 comes from one test run on a PYNQ-Z1 with the 0x5A5A0038 bitstream, and
every code line is the real code from that run. That run was a `--replay`, which reruns the kernel
DeepSeek wrote in a recorded live run without calling the model again. §6 describes the recorded run.
The spike numbers in §2 and §4 come from a spike built like the card (soft float). The run's own
report says 8.8× because it was scored on the older spike built with a hardware FPU, and §2 explains
the difference. A live run of your own writes a slightly different kernel, so expect 43 to 49× on the
board rather than the numbers below.

## 1. The op

`gelu_s8` is GELU on an int8 tensor with symmetric quantisation per tensor. Each int8 value
represents `x × scale_in`, and the float result is requantised with `scale_out` and clamped to
int8. In your model n = 16,384, `scale_in = 0.0684` and `scale_out = 0.0646`. The "before" is
ModelBlaster's reference kernel:

```c
for (int i = 0; i < n; i++) {
    float f = (float)input[i] * scale_in;
    float y = 0.5f * f * (1.0f + erff(f * kInvSqrt2));    // one erff PER ELEMENT
    int32_t v = (int32_t)roundf(y / scale_out);
    if (v < activation_min) v = activation_min;
    if (v > activation_max) v = activation_max;
    output[i] = (int8_t)v;
}
```

## 2. Why it is slow on the board

The board's Rocket core has no FPU, so every float operation in `erff` runs as software emulation,
which takes thousands of instructions per element. The lab's spike is built the same way as the
board image (soft float, `rv64imac`), so spike sees the same cost:

| | spike, built like the card (instructions per element) | board, 40 MHz (cycles per element) |
|---|---|---|
| before (float reference) | 4,590 | 5,524 (2.26 s for the whole tensor) |

Built with a hardware FPU, spike reported 192. An optimizer scored that way would treat `erff` as
cheap and could keep code that is slow on the real core, which is why the lab does not score that
way.

## 3. Only 256 possible inputs

An int8 input can take only 256 values. For a fixed `scale_in` and `scale_out`, the whole op is
therefore a function from 256 bytes to 256 bytes, and there is no need to call `erff` 16,384 times.
Calling it once per value that actually occurs is enough. The LLM's kernel does this in three steps:

```c
uint32_t seen[8] = {0};                                     // 1. mark which of the 256 bytes occur
for (int i = 0; i < n; ++i) { uint8_t v = (uint8_t)input[i]; seen[v >> 5] |= 1U << (v & 31U); }

int8_t table[256];                                          // 2. erff once per DISTINCT byte
for (int idx = 0; idx < 256; ++idx)
    if (seen[idx >> 5] & (1U << (idx & 31U))) {
        float f = ((int8_t)idx) * scale_in;
        float y = 0.5f * f * (1.0f + erff(f * sqrt_half));
        int32_t v = (int32_t)roundf(y * inv_scale_out);     // note: * (1/s), not / s -- see §5
        /* clamp */  table[idx] = (int8_t)v;
    }

for (int i = 0; i < n; ++i) output[i] = table[(uint8_t)input[i]];   // 3. a byte gather
```

For `n ≤ 256` the kernel keeps the loop over elements, since the table would not pay for itself at
that size. The full kernel from your run is in the file named on the last line of `mb report <run>`.

## 4. What it bought

| | spike (built like the card) | board |
|---|---|---|
| before, per element | 4,590 | 5,524 |
| after, per element | 96.5 | 112 |
| speedup | 47.6× | 49.3× (2.26 s → 46 ms) |

Here spike predicts the board to within a few percent. The real core spends about 1.2 cycles per
instruction, and the extra is memory time, which spike does not model. For comparison, the
handwritten kernel in the repo reached 31 cycles per element on the board at n = 131,072. With more
elements, the cost of building the table is spread more thinly.

## 5. Checking that the output is identical bit for bit

Every table entry is computed with the reference's own expression in float32, so the output should
be identical bit for bit. The lab checks this in three ways:

1. ModelBlaster's host verify, with random inputs;
2. the spike golden, one real tensor through the whole model;
3. the whole domain check, which compares all 256 inputs × 577 scale pairs = 147,712 cases against
   the reference expression. Result: 0 differ.

The third check exists because of the comment in §3. The LLM replaced `y / scale_out` with
`y * (1/scale_out)`. The two can differ in the last float bit, and when that bit sits on a rounding
boundary, `roundf` changes the output by 1. The whole domain check found no such case for this
kernel over 577 scale pairs. That is evidence, not a proof for every possible scale.

## 6. What the LLM was told

ModelBlaster's prompt for this op names an algorithm from its database, `pext_memo_lut`, and
includes its description word for word:

> *One marking pass over the input, erff for each of the at most 256 DISTINCT byte values that
> actually occur, then a byte gather. Bit-exact by construction…*

The LLM implemented this described idea in 1 call and then spent 4 more calls making it faster.
The cycle counts in this section come from that run's spike, which was still built with a hardware
FPU, so they are lower than those in the §4 table; what matters is the order of the steps. The
first call's kernel took 446,944 cycles. Round 1 switched the marking to a 32-bit bitset and
replaced the division with a multiply, which brought it to 414,126 cycles. That multiply is the
change §5 is concerned with, and it was introduced by an optimize step, not by the first draft.
Round 2 unrolled the loops 4×, for 356,805 cycles.

`mb calls <run>` shows every prompt and answer from your run.

## Try it (3 to 5 minutes each)

1. Remove the hint. `mb go gelu_s8 --hint none` sends only the float reference, with no algorithm
   description. In a test run the LLM stayed at 1.02×. It tuned the loop and never found the
   table, so the speedup comes from the idea in the description rather than from the search.
2. Spot the bug. `fpga/pynq-z2/modelblaster/mb_ops/exercises/gelu_s8_broken.c` is the kernel above
   with one "harmless" change. At your model's scales it matches the reference on all 256 inputs,
   so the spike golden passes. Read it, then check it:

       mb show fpga/pynq-z2/modelblaster/mb_ops/exercises/gelu_s8_broken.c
       mb check fpga/pynq-z2/modelblaster/mb_ops/exercises/gelu_s8_broken.c

   The check reports 33 of 147,712 cases differing by 1 LSB, across 28 scale pairs. Find the
   change, and explain why one test tensor could never have caught it.
3. The same idea without a hint. `sigmoid_s8` is also a map over 256 values, but ModelBlaster's
   database has no fast algorithm for it, so the LLM gets no description. A test run stayed at
   1.0×. Can you write the description that makes it converge? Adapt the text quoted in §6, write it
   in a file on your seat (from a JupyterLab terminal), and pass it with `--guide`:

       nano ~/sigmoid_hint.md
       mb go sigmoid_s8 --guide ~/sigmoid_hint.md

   One description that worked, with which the LLM reached 51.8× on the FPGA, can be read with
   `mb show fpga/pynq-z2/modelblaster/mb_ops/exercises/sigmoid_hint_solution.md`.
