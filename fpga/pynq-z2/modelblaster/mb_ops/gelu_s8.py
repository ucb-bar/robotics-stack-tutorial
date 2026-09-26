"""One op, gelu_s8, as a file in KernelBench style for `extract_graph --bench-file`.

The lab uses single-operator models rather than a network such as ffn_block because
ModelBlaster's generate_kernels cannot be limited to one op: every op in the IR gets a kernel,
and --optimize scores each candidate by building and running the whole model on spike. In
ffn_block the GELU is 131,072 elements behind two GEMMs, so each candidate would cost about
400 M instructions of float erff plus the GEMMs. With a single gelu_s8 only the kernel under study
changes, and a spike run takes seconds.

16,384 elements is well above the curated kernel's guard for small n (n < 32 uses the reference
expression per element), so the memo table is what gets measured, and small enough that the
float reference finishes on spike in seconds. The input is seeded so every get_inputs() call
(calibration, golden, verify) sees the same tensor, and scaled to cover GELU's curved region,
like the fc1 output ahead of a transformer GELU.
"""

import torch
import torch.nn as nn

ROWS = 16
COLS = 1024


class Model(nn.Module):
    def __init__(self):
        super().__init__()
        self.act = nn.GELU()

    def forward(self, x):
        return self.act(x)


def get_inputs():
    g = torch.Generator().manual_seed(0)
    return [torch.randn(ROWS, COLS, generator=g) * 2.0]


def get_init_inputs():
    return []
