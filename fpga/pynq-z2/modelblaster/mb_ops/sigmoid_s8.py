"""One op, sigmoid_s8, as a file in KernelBench style for `extract_graph --bench-file`.

See gelu_s8.py in this directory for why the lab uses single-operator models rather than a network.
ModelBlaster has no pext algorithm for sigmoid_s8, so there is nothing to describe to the LLM
and no curated kernel to compare against.
"""

import torch
import torch.nn as nn

ROWS = 16
COLS = 1024


class Model(nn.Module):
    def __init__(self):
        super().__init__()
        self.act = nn.Sigmoid()

    def forward(self, x):
        return self.act(x)


def get_inputs():
    g = torch.Generator().manual_seed(0)
    return [torch.randn(ROWS, COLS, generator=g) * 2.0]


def get_init_inputs():
    return []
