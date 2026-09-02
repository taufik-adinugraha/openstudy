"""The network. Separated from core.py so the data loaders do not require torch.

control.py needs the series and the labels and no model at all; when CropNet lived
beside load_regency, importing the loaders pulled in torch and the control could not
run on a machine without it. Data loading and model definition are different
concerns and now live in different files.

MODEL. A dilated 1-D convolutional net, 50,838 parameters. Dilations 1/2/4/8/16 with
kernel 7 give a receptive field of 187 steps, about three years, so a cell's whole
cropping rhythm is inside the field of view rather than being summarised away. Global
average and max pooling are concatenated: the average carries how wet the cell
usually is, the max carries whether it ever floods hard, and both matter for telling
one cycle a year from three.

Width is 32 channels, which is a deliberate size rather than a default. At 48 the
net costs ~24M multiply-accumulates per sample and an epoch took 426 s on this
machine — 2.8 hours for six folds — because dilated 1-D convolution runs well off
peak on Metal. At 32, measured on this machine, a training step costs 660 ms at batch 4096 — about
half the per-sample cost — for a capacity reduction that a 4-class problem on a
smoothed two-channel series does not need. The dilations, and therefore the 187-step
receptive field, are unchanged.

"""

from __future__ import annotations

import torch
import torch.nn as nn


class CropNet(nn.Module):
    def __init__(self, n_classes: int = 4, width: int = 32):
        super().__init__()
        chans = [2, width, width, width * 4 // 3, width * 4 // 3, width * 4 // 3]
        blocks = []
        for i, dil in enumerate([1, 2, 4, 8, 16]):
            blocks += [
                nn.Conv1d(chans[i], chans[i + 1], kernel_size=7,
                          padding=3 * dil, dilation=dil),
                nn.BatchNorm1d(chans[i + 1]),
                nn.GELU(),
            ]
        self.trunk = nn.Sequential(*blocks)
        self.head = nn.Sequential(
            nn.Dropout(0.15),
            nn.Linear(chans[-1] * 2, 96),
            nn.GELU(),
            nn.Linear(96, n_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.trunk(x)
        pooled = torch.cat([h.mean(dim=2), h.amax(dim=2)], dim=1)
        return self.head(pooled)


def device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")
