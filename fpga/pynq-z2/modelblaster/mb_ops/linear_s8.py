"""One op, linear_s8 (an int8 GEMM, M=64 K=256 N=256), for `extract_graph --bench-file`.

See gelu_s8.py in this directory for why the lab uses single-operator models rather than a network.
The curated pext kernel (pext_row_dot8) uses MBP.DOT8, a custom-0 instruction, so a candidate
that matches it has to use pext.h's intrinsics, which only the TACIT spike and the pext
bitstream can execute.
"""

import torch
import torch.nn as nn

M = 64
K = 256
N = 256


class Model(nn.Module):
    def __init__(self):
        super().__init__()
        torch.manual_seed(0)
        self.fc = nn.Linear(K, N)

    def forward(self, x):
        return self.fc(x)


def get_inputs():
    g = torch.Generator().manual_seed(0)
    return [torch.randn(M, K, generator=g)]


def get_init_inputs():
    return []
