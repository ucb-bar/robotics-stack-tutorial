"""One op, maxpool2d_s8 (2x2, stride 2, on 1x16x64x64), for `extract_graph --bench-file`.

See gelu_s8.py in this directory for why the lab uses single-operator models rather than a network.
The curated pext kernel (pext_max8_rows) takes the max of eight bytes at a time with the
packed SIMD unit.
"""

import torch
import torch.nn as nn

C = 16
H = 64
W = 64


class Model(nn.Module):
    def __init__(self):
        super().__init__()
        self.pool = nn.MaxPool2d(2)

    def forward(self, x):
        return self.pool(x)


def get_inputs():
    g = torch.Generator().manual_seed(0)
    return [torch.randn(1, C, H, W, generator=g)]


def get_init_inputs():
    return []
