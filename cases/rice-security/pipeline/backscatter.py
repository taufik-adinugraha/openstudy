"""Stage 5 · backscatter — raw acquisitions into one clean, evenly-sampled curve per cell.

This is where most of the accuracy of the whole case is won or lost, and none of it is glamorous.

SPECKLE — ALREADY HANDLED, AND HANDLED CORRECTLY
------------------------------------------------
SAR is coherent imaging: a single-look pixel carries multiplicative speckle whose standard
deviation is the same order as the signal, so a per-pixel time series is unreadable.  The fix is
spatial multi-looking, and the house rule is that the average must be taken IN LINEAR POWER and
converted to dB afterwards — averaging decibels is averaging logarithms and biases every cell low
by an amount that depends on its own variance, which correlates with land cover and therefore
looks exactly like a real spatial pattern.

Stage 4 already did this, server-side and provably: it reads overview level 8 of the remote COG,
which is a 64-look average of 10 m gamma0 in linear power, and only then converts to dB.  Checked
against the full-resolution read, the linear means agree to five decimals.  So this stage starts
from ~80 m, 64-look gamma0 in dB and does the three things that are left.

1 · ORBIT NORMALISATION
-----------------------
gamma0 depends on incidence angle, so relative orbits are not interchangeable: concatenating them
raw produces a sawtooth at the orbit-repeat frequency that mimics phenology.  Each cell's series
is therefore offset per orbit onto a common reference before the orbits are merged.  The offset
is the cell's DRY-SEASON median (Jun-Sep, when the surface is least dynamic) where the cell has
enough dry-season looks on that orbit, and its full-record median otherwise; the full-record
median is phase-independent over four years, so it cannot absorb crop signal the way a
short-window fit could.  Offsets are exported, and if they were large the method would be wrong —
they run around a decibel, which is what an incidence-angle difference should cost.

2 · RESAMPLING
--------------
Revisit is 12 days per orbit and irregular across orbits, so the merged series is interpolated
onto a regular ``config.STEP_DAYS`` grid with a bit-packed mask of which steps carry a REAL
observation.  Phenology then refuses to date an event whose defining steps sit inside a gap
longer than ``config.MAX_GAP_DAYS`` rather than inventing a date from an interpolation.

3 · SMOOTHING
-------------
Savitzky-Golay (``config.SG_WINDOW``/``config.SG_ORDER``).  It preserves peak position and
amplitude; a moving average destroys both, and peak position is the thing this case measures.
Raw and smoothed are both kept — the signature interaction draws the raw points under the fit,
because a client who can see the scatter trusts the line.

OUTPUT: data/bs/<kabupaten>.npz — steps (int16 days from SAR_START), vv/vh smoothed
(n_cells x n_steps int16, hundredths of a dB), obs (bit-packed real-observation mask),
n_obs, orbit offsets, and the raw per-observation series for the probe lattice.
"""

from __future__ import annotations

import json
from pathlib import Path

import config
import util
from util import log

BS_DIR = config.DATA_DIR / "bs"
SAR_DIR = config.DATA_DIR / "sar"
NODATA = -32768
DRY_MONTHS = (6, 7, 8, 9)


MIN_ORBIT_DATES = 20


def _load(kab: str):
    """All (orbit, date) slots for one kabupaten as int16 dB*100, plus their metadata.

    An orbit with only a handful of dates is dropped rather than normalised.  The offset that
    puts an orbit on the common reference is a median over that orbit's own observations, and a
    median over a dozen dates inside one four-month window is not phase-independent — it would
    bake a season into a calibration constant for whatever slice of the kabupaten that orbit
    happens to cover, which then reads downstream as a real spatial pattern.
    """
    import numpy as np
    import pandas as pd

    files = []
    for d in sorted((SAR_DIR / kab).iterdir()):
        if not d.is_dir():
            continue
        got = sorted(d.glob("*.npy"))
        if len(got) < MIN_ORBIT_DATES:
            log(f"{kab}: orbit T{d.name} has only {len(got)} dates — dropped "
                f"(floor {MIN_ORBIT_DATES}; too few to normalise honestly)")
            continue
        files += got
    files = sorted(files, key=lambda p: (p.stem, p.parent.name))
    util.require(len(files) > 0, f"{kab}: no SAR slots — run `make sar` first")
    dates, orbits, arrs = [], [], []
    for f in files:
        arrs.append(np.load(f))
        dates.append(pd.Timestamp(f.stem))
        orbits.append(int(f.parent.name))
    order = np.argsort(np.array([d.value for d in dates]))
    return ([dates[i] for i in order], np.array(orbits)[order],
            np.stack([arrs[i] for i in order]))       # (n_obs, 2, n_cells)


def normalise_incidence(stack, dates, orbits):
    """Offset every orbit onto a common per-cell reference.  See section 1 of the docstring."""
    import numpy as np
    import pandas as pd

    months = np.array([d.month for d in dates])
    dry = np.isin(months, DRY_MONTHS)
    uorb = np.unique(orbits)
    n_pol, n_cell = stack.shape[1], stack.shape[2]
    ref = np.full((len(uorb), n_pol, n_cell), np.nan, "float32")
    used_dry = 0
    for oi, o in enumerate(uorb):
        sel = orbits == o
        for p in range(n_pol):
            block = stack[sel, p, :].astype("float32")
            block[block == NODATA] = np.nan
            d_block = block[dry[sel]] if dry[sel].any() else block[:0]
            with np.errstate(all="ignore"):
                med_dry = (np.nanmedian(d_block, axis=0) if len(d_block) >= 4
                           else np.full(n_cell, np.nan, "float32"))
                med_all = np.nanmedian(block, axis=0)
            take_dry = np.isfinite(med_dry) & (np.sum(np.isfinite(d_block), axis=0) >= 4)
            ref[oi, p] = np.where(take_dry, med_dry, med_all)
            used_dry += int(take_dry.sum())
    with np.errstate(all="ignore"):
        common = np.nanmean(ref, axis=0)                       # (n_pol, n_cell)
    offsets = common[None, :, :] - ref                          # (n_orb, n_pol, n_cell)
    out = stack.astype("float32")
    out[out == NODATA] = np.nan
    for oi, o in enumerate(uorb):
        sel = orbits == o
        out[sel] += offsets[oi][None, :, :]
    summary = {str(int(o)): {
        "n_dates": int((orbits == o).sum()),
        "median_offset_vv_db": round(float(np.nanmedian(offsets[oi, 0]) / 100.0), 3),
        "median_offset_vh_db": round(float(np.nanmedian(offsets[oi, 1]) / 100.0), 3),
    } for oi, o in enumerate(uorb)}
    summary["_dry_season_reference_share"] = round(
        used_dry / max(len(uorb) * n_pol * n_cell, 1), 3)
    return out, summary


def resample(series, dates, step_days: int, t0, t1):
    """Interpolate onto the regular grid; return the grid, the values and the real-obs mask."""
    import numpy as np

    grid = np.arange(0, int((t1 - t0).days) + 1, step_days)
    obs = np.array([(d - t0).days for d in dates], "float64")
    n_pol, n_cell = series.shape[1], series.shape[2]
    out = np.full((n_pol, n_cell, len(grid)), np.nan, "float32")
    for p in range(n_pol):
        block = series[:, p, :]                                # (n_obs, n_cell)
        for c0 in range(0, n_cell, 40_000):
            c1 = min(c0 + 40_000, n_cell)
            sub = block[:, c0:c1]
            good = np.isfinite(sub)
            for j in range(sub.shape[1]):
                g = good[:, j]
                if g.sum() < 6:
                    continue
                out[p, c0 + j] = np.interp(grid, obs[g], sub[g, j],
                                           left=np.nan, right=np.nan)
    # a grid step is "real" when a genuine acquisition falls within half a step of it
    real = np.zeros(len(grid), "bool")
    nearest = np.full(len(grid), 9999.0)
    for od in obs:
        k = int(round(od / step_days))
        if 0 <= k < len(grid):
            real[k] = True
        nearest = np.minimum(nearest, np.abs(grid - od))
    return grid, out, real, nearest


def smooth(arr, window: int, order: int):
    """Savitzky-Golay along the time axis, NaN-safe (short runs are left as-is)."""
    import numpy as np
    from scipy.signal import savgol_filter

    out = arr.copy()
    n = arr.shape[-1]
    if n < window:
        return out
    flat = out.reshape(-1, n)
    ok = np.isfinite(flat).all(axis=1)
    if ok.any():
        flat[ok] = savgol_filter(flat[ok], window, order, axis=1, mode="interp")
    # rows with holes: fill by interpolation for the filter, then restore the holes
    part = ~ok & np.isfinite(flat).any(axis=1)
    if part.any():
        idx = np.arange(n)
        sub = flat[part]
        mask = np.isfinite(sub)
        for i in range(sub.shape[0]):
            m = mask[i]
            if m.sum() < window:
                continue
            filled = np.interp(idx, idx[m], sub[i, m])
            sm = savgol_filter(filled, window, order, mode="interp")
            sm[~m & (idx < idx[m][0])] = np.nan
            sm[~m & (idx > idx[m][-1])] = np.nan
            sub[i] = sm
        flat[part] = sub
    return flat.reshape(arr.shape)


def main() -> None:
    import numpy as np
    import pandas as pd

    util.guard_disk()
    BS_DIR.mkdir(parents=True, exist_ok=True)
    cells = pd.read_parquet(config.DATA_DIR / "cells.parquet")
    t0 = pd.Timestamp(config.SAR_START)
    t1 = pd.Timestamp(config.SAR_END)
    meta_all = {}

    for kab in config.SCOPE_DEEP:
        if not (SAR_DIR / kab).exists():
            log(f"{kab}: no SAR data — skipped")
            continue
        ck = cells[cells["kabupaten"] == kab].reset_index(drop=True)
        dates, orbits, stack = _load(kab)
        util.require(stack.shape[2] == len(ck),
                     f"{kab}: SAR has {stack.shape[2]} cells, index has {len(ck)}")
        log(f"{kab}: {len(dates)} acquisitions on orbits {sorted(set(orbits.tolist()))}, "
            f"{len(ck):,} cells")

        # The probe lattice, captured BEFORE normalisation frees the stack: the signature
        # interaction draws real observations as points, so it needs the genuine acquisitions,
        # not the resampled series (whose off-acquisition steps are interpolations and would be
        # a chart of invented data dressed as measurement).
        stepc = max(1, int(config.PROBE_STEP_M / config.CELL_M))
        gx = (ck["x"].to_numpy() / config.CELL_M).round().astype("int64")
        gy = (ck["y"].to_numpy() / config.CELL_M).round().astype("int64")
        probe = np.flatnonzero((gx % stepc == 0) & (gy % stepc == 0))
        probe_raw = stack[:, :, probe].copy()            # (n_obs, 2, n_probe) int16

        norm, off = normalise_incidence(stack, dates, orbits)
        del stack
        grid, res, real, nearest = resample(norm, dates, config.STEP_DAYS, t0, t1)
        del norm
        sm = smooth(res, config.SG_WINDOW, config.SG_ORDER)
        n_obs = np.isfinite(res[0]).sum(axis=1).astype("int16")

        def q(a):
            b = np.where(np.isfinite(a), np.clip(np.round(a), -4000, 2000), NODATA)
            return b.astype("int16")

        np.savez_compressed(
            BS_DIR / f"{kab}.npz",
            steps=grid.astype("int16"),
            vv=q(sm[0]), vh=q(sm[1]),
            real=np.packbits(real),
            nearest=nearest.astype("int16"),
            n_obs=n_obs,
            probe=probe.astype("int32"),
            probe_raw=probe_raw,
            obs_days=np.array([(d - t0).days for d in dates], "int16"),
            obs_orbit=orbits.astype("int16"),
        )
        meta_all[kab] = {
            "n_cells": int(len(ck)), "n_acquisitions": int(len(dates)),
            "orbits": off, "grid_steps": int(len(grid)), "step_days": config.STEP_DAYS,
            "first": str(dates[0].date()), "last": str(dates[-1].date()),
            "real_steps": int(real.sum()),
            "median_obs_per_cell": int(np.median(n_obs)),
            "sg": {"window": config.SG_WINDOW, "order": config.SG_ORDER},
        }
        log(f"{kab}: grid {len(grid)} steps, {int(real.sum())} carry a real acquisition; "
            f"median {int(np.median(n_obs))} obs/cell; orbit offsets "
            + ", ".join(f"T{o}:{v['median_offset_vv_db']:+.2f}dB"
                        for o, v in off.items() if not o.startswith("_")))
        del res, sm

    (config.DATA_DIR / "backscatter_meta.json").write_text(json.dumps(meta_all, indent=1))
    log(f"backscatter -> {BS_DIR} ({len(meta_all)} kabupaten)")


if __name__ == "__main__":
    main()
