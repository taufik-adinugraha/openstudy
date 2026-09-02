"""Data for the cropping-intensity network. No torch here — see net.py.

INPUT. Each 100 m cell carries a Sentinel-1 series already reduced by the ingest
pipeline onto a regular 6-day grid: 254 steps from 2022-07-07 to 2026-08-25, VV and
VH, Savitzky-Golay smoothed (window 5, order 2), stored as int16 decibels x 100.
Steps 0 and 253 are missing for every cell in every regency, so the usable window is
252 steps and there is no per-cell missingness to impute. The model reads (2, 252).

TARGET. Open-SEA-Rice-10 (Zenodo 10.5281/zenodo.14627003, CC BY 4.0, 2021, 10 m):
0 non-rice, 1 single-crop, 2 double, 3 triple. Joined per cell through
data/cells.parquet, whose row order within a kabupaten matches the npz row order —
verified by signal rather than assumed: with the real labels, mean VH amplitude rises
2.15 -> 3.02 -> 3.31 dB across non-rice, single and double crop, and permuting the
labels collapses every class to 2.52. A misaligned join could not produce that.

Why not a transformer: 1.2M sequences of length 252 on an 8 GB laptop, and a
convolution is the right prior for a signal whose information is local shape repeated
at a known period. There is no long-range dependency here that dilation does not
already reach.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = pathlib.Path(__file__).resolve().parents[2]
BS = ROOT / "data" / "bs"
CELLS = ROOT / "data" / "cells.parquet"

REGENCIES = ["Bojonegoro", "Grobogan", "Indramayu", "Karawang", "Lamongan", "Subang"]
CLASSES = {0: "non-rice", 1: "single", 2: "double", 3: "triple"}
STEP0, STEP1 = 1, 253          # the two endpoints are empty for every cell
N_STEPS = STEP1 - STEP0        # 252
SEED = 7


def load_regency(name: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(series int16 [N,2,252], labels uint8 [N], real-step mask bool [252])."""
    z = np.load(BS / f"{name}.npz", allow_pickle=True)
    vv = z["vv"][:, STEP0:STEP1]
    vh = z["vh"][:, STEP0:STEP1]
    x = np.stack([vv, vh], axis=1)                      # [N, 2, 252], int16
    real = np.unpackbits(z["real"])[:254].astype(bool)[STEP0:STEP1]

    t = pq.read_table(CELLS, columns=["kabupaten", "mask_class"])
    y = (t.filter(pc.equal(t.column("kabupaten"), name))
          .column("mask_class").to_numpy())
    assert len(y) == x.shape[0], f"{name}: {len(y)} labels for {x.shape[0]} cells"
    return x, np.asarray(y, dtype=np.uint8), real


def to_db(x: np.ndarray) -> np.ndarray:
    """int16 decibels x100 -> float32 decibels."""
    return x.astype(np.float32) / 100.0


class Norm:
    """Channel statistics taken from the TRAINING folds only."""

    def __init__(self, mean: np.ndarray, std: np.ndarray):
        self.mean, self.std = mean.astype(np.float32), std.astype(np.float32)

    @classmethod
    def fit(cls, chunks: list[np.ndarray], cap: int = 40_000) -> "Norm":
        rng = np.random.default_rng(SEED)
        sample = []
        for c in chunks:
            idx = rng.choice(c.shape[0], size=min(cap, c.shape[0]), replace=False)
            sample.append(to_db(c[idx]))
        s = np.concatenate(sample, axis=0)
        return cls(s.mean(axis=(0, 2)), s.std(axis=(0, 2)) + 1e-6)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        d = to_db(x)
        return (d - self.mean[None, :, None]) / self.std[None, :, None]

    def state(self) -> dict:
        return {"mean": self.mean.tolist(), "std": self.std.tolist()}


def thin_series(x: np.ndarray, real: np.ndarray, keep_every: int) -> np.ndarray:
    """Drop acquisitions and re-fill the grid from the ones that survive.

    The case's own thinning experiment removed acquisitions and re-ran ingest. This
    is the inference-time analogue: the surviving real steps keep their values and
    every other step takes the nearest survivor's, so the model is given only
    information derivable from the thinned record. It is an approximation of
    re-running ingest — it cannot reproduce the Savitzky-Golay pass over a shorter
    series — and it is the same approximation for every rung of the ladder, which is
    what the comparison needs.
    """
    real_idx = np.flatnonzero(real)
    keep = real_idx[::keep_every]
    if len(keep) == 0:
        raise ValueError("thinning removed every acquisition")
    # nearest surviving acquisition for each of the 252 grid steps
    pos = np.arange(x.shape[2])
    nearest = keep[np.abs(pos[:, None] - keep[None, :]).argmin(axis=1)]
    return x[:, :, nearest]
