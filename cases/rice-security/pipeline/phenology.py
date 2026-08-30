"""Stage 6 · phenology — the crop calendar, read out of the radar curve.

THE PHYSICS, IN THREE SENTENCES — the dashboard has to explain it and so does the code
--------------------------------------------------------------------------------------
A paddy field about to be transplanted is flooded, and a sheet of water is a specular reflector
that sends the pulse away from the satellite, so backscatter collapses to a sharp minimum —
strongest in VV, at ``config.FLOOD_DB`` and below.  As the crop tillers, the canopy becomes a
volume of vertical scatterers standing in water and VH climbs steeply, several dB over a few
weeks, to a maximum around heading.  After heading the canopy dries, the crop is cut, and the
signal falls again.  Minimum, then steep rise, then peak is close to unique to flooded rice —
which is why radar rice mapping works at all, and why it works through the exact monsoon cloud
that makes optical methods fail in this country.

THE DETECTOR, FIVE STEPS, VECTORISED OVER EVERY CELL AT ONCE
------------------------------------------------------------
  1  local minima of the smoothed VV below ``config.FLOOD_DB``          -> candidate transplant
  2  a VH rise of at least ``config.RISE_DB`` within
     ``config.RISE_WINDOW_DAYS`` of that minimum                        -> it is a crop, not a
                                                                           pond, a tidal flat or
                                                                           a reservoir
  3  the following VH maximum                                           -> heading
  4  harvest = heading + ``config.HEAD_TO_HARVEST_DAYS``, refined by
     the subsequent VH drop where one is observable before the next
     flooding                                                           -> harvest
  5  reject any event whose defining steps fall inside an observation
     gap longer than ``config.MAX_GAP_DAYS``                            -> undated beats
                                                                           confidently wrong

THE THRESHOLDS ARE LITERATURE VALUES AND ARE NOT FITTED
-------------------------------------------------------
-17 dB, 4 dB, 45 days, 30 days, and a minimum cycle of ``config.MIN_CYCLE_DAYS``.  Fitting any of
them on the KSA benchmark would guarantee agreement with the benchmark and destroy the gate that
is supposed to test it.  They are stated on the methodology page, and stage 9 exports the
sensitivity of every headline number to each of them.

CONFUSERS, NAMED
----------------
Aquaculture ponds, tidal flats, reservoirs and freshly-ploughed wet fields all produce a low
minimum.  Only rice produces the minimum FOLLOWED BY the steep VH rise, so step 2 is a
requirement and not a refinement.  Permanent water is excluded by requiring the cell to leave the
low state at all.  Sugarcane and some vegetables can mimic the rise; the rice prior is carried as
a covariate and the residual confusion is a stated error term, not an assumed zero.

CROPPING INTENSITY FALLS OUT FREE — detected cycles per year IS the intensity, and gate G-I3
checks it against agronomy and against Open-SEA-Rice-10's own single/double/triple classes.

OUTPUT: data/phenology.parquet — one row per detected cycle: cell, kabupaten, kecamatan, season,
transplant/heading/harvest dates, depth and rise amplitudes, confidence, max gap.
"""

from __future__ import annotations

import json

import config
import util
from util import log

BS_DIR = config.DATA_DIR / "bs"
OUT = config.DATA_DIR / "phenology.parquet"
NODATA = -32768


def detect_cycles(vv, vh, nearest, step_days, params=None):
    """The five-step detector, vectorised over cells.

    ``vv``/``vh`` are (n_cells, n_steps) float32 dB with NaN for unobserved; ``nearest`` is the
    distance in days from each grid step to the closest real acquisition.  Returns flat arrays,
    one entry per detected cycle.
    """
    import numpy as np

    p = dict(flood_db=config.FLOOD_DB, flood_drop_db=config.FLOOD_DROP_DB,
             baseline_pctl=config.FLOOD_BASELINE_PCTL, rise_db=config.RISE_DB,
             rise_window=config.RISE_WINDOW_DAYS, head_to_harvest=config.HEAD_TO_HARVEST_DAYS,
             min_cycle=config.MIN_CYCLE_DAYS, max_gap=config.MAX_GAP_DAYS)
    p.update(params or {})
    n_cell, n_step = vv.shape
    w_rise = max(1, int(round(p["rise_window"] / step_days)))
    w_cycle = max(1, int(round(p["min_cycle"] / step_days)))

    # step 1 — local minima of VV that fall far enough below the cell's OWN dry baseline.
    # See the long note in config: the literature's absolute -17 dB is a single-plot value and a
    # 100 m cell that holds several asynchronously-transplanted plots never reaches it.  The
    # baseline is the cell's own high percentile of VV, i.e. what it looks like when it is not
    # flooded; the absolute minimum is still carried per event so the literature comparison stays
    # available rather than being quietly dropped.
    with np.errstate(all="ignore"):
        baseline = np.nanpercentile(vv, p["baseline_pctl"], axis=1)
    drop = baseline[:, None] - vv
    left = np.full_like(vv, np.inf)
    right = np.full_like(vv, np.inf)
    left[:, 1:] = vv[:, :-1]
    right[:, :-1] = vv[:, 1:]
    is_min = ((vv < left) & (vv <= right) & (drop >= p["flood_drop_db"])
              & np.isfinite(vv) & np.isfinite(drop))
    if p.get("flood_db_absolute") is not None:      # the literature rule, for the sweep only
        is_min &= vv < p["flood_db_absolute"]

    # step 2 — CONFIRM the rise: VH must climb by rise_db somewhere inside rise_window.
    # step 3 — LOCATE heading: the VH maximum in the (longer) heading window.
    # These are two different windows and merging them silently caps every cycle at
    # rise_window + head_to_harvest days.  See the note in config.HEADING_WINDOW_DAYS.
    w_head = max(w_rise, int(round(p.get("heading_window", config.HEADING_WINDOW_DAYS)
                                   / step_days)))
    vh_f = np.where(np.isfinite(vh), vh, -np.inf)
    cols = np.arange(n_step)[None, :]

    def forward(win):
        best = np.full_like(vh_f, -np.inf)
        arg = np.zeros(vh_f.shape, "int32")
        for k in range(1, win + 1):
            cand = np.full_like(vh_f, -np.inf)
            cand[:, :n_step - k] = vh_f[:, k:]
            better = cand > best
            np.copyto(arg, np.minimum(cols + k, n_step - 1), where=better)
            best = np.where(better, cand, best)
        return best, arg

    rise_max, _ = forward(w_rise)
    head_max, head_arg = forward(w_head)
    rise = rise_max - vh
    ok = is_min & np.isfinite(rise) & (rise >= p["rise_db"])

    ci, si = np.nonzero(ok)
    fut_arg = head_arg
    fut_max = head_max
    if ci.size == 0:
        return {k: np.array([]) for k in
                ("cell", "transplant", "heading", "harvest", "depth", "drop", "rise", "peak",
                 "gap", "conf")}

    head = fut_arg[ci, si]
    # step 4 — harvest at heading + duration, refined by the subsequent VH drop
    dur = int(round(p["head_to_harvest"] / step_days))
    harv = np.minimum(head + dur, n_step - 1)
    drop_to = np.full(ci.size, np.nan, "float32")
    win = max(1, dur * 2)
    for i in range(ci.size):
        a, b = head[i] + 1, min(head[i] + win + 1, n_step)
        if b <= a:
            continue
        seg = vh[ci[i], a:b]
        peak = vh[ci[i], head[i]]
        below = np.flatnonzero(np.isfinite(seg) & (seg <= peak - 3.0))
        if below.size:
            harv[i] = a + int(below[0])
            drop_to[i] = seg[below[0]]

    # step 5 — refuse to date an event whose defining steps sit inside a long gap
    gap = np.maximum.reduce([nearest[si], nearest[head], nearest[np.minimum(harv, n_step - 1)]])
    keep = gap <= p["max_gap"]

    depth = vv[ci, si]
    dropamp = drop[ci, si]
    riseamp = rise[ci, si]
    peakvh = vh[ci, head]

    # one cycle per cell per MIN_CYCLE window — keep the deepest minimum in each cluster
    order = np.lexsort((depth, si, ci))
    ci, si, head, harv, depth, dropamp, riseamp, peakvh, gap, keep = (
        a[order] for a in (ci, si, head, harv, depth, dropamp, riseamp, peakvh, gap, keep))
    sel = np.ones(ci.size, "bool")
    last_cell, last_step = -1, -10 ** 6
    for i in range(ci.size):
        if ci[i] != last_cell:
            last_cell, last_step = ci[i], -10 ** 6
        if si[i] - last_step < w_cycle:
            sel[i] = False
        else:
            last_step = si[i]
    sel &= keep

    # confidence: how deep the flood, how steep the rise, how close a real acquisition was
    conf = np.clip((dropamp - p["flood_drop_db"]) / 4.0, 0, 1) * 0.35 \
        + np.clip((riseamp - p["rise_db"]) / 6.0, 0, 1) * 0.35 \
        + np.clip(1.0 - gap / p["max_gap"], 0, 1) * 0.30
    return dict(cell=ci[sel], transplant=si[sel], heading=head[sel], harvest=harv[sel],
                depth=depth[sel], drop=dropamp[sel], rise=riseamp[sel], peak=peakvh[sel],
                gap=gap[sel], conf=conf[sel].astype("float32"))


def cropping_intensity(df, cells):
    """Detected cycles per calendar year per cell — the free plausibility test (gate G-I3)."""
    import pandas as pd

    per = (df.groupby(["kabupaten", "cell_i", "year"]).size().rename("cycles").reset_index())
    return (per.groupby(["kabupaten", "year"])["cycles"].mean().rename("ci").reset_index(),
            per)


def main() -> None:
    import numpy as np
    import pandas as pd

    util.guard_disk()
    cells = pd.read_parquet(config.DATA_DIR / "cells.parquet")
    t0 = pd.Timestamp(config.SAR_START)
    frames, diag = [], {}

    for kab in config.SCOPE_DEEP:
        f = BS_DIR / f"{kab}.npz"
        if not f.exists():
            log(f"{kab}: no backscatter — skipped")
            continue
        z = np.load(f)
        ck = cells[cells["kabupaten"] == kab].reset_index(drop=True)
        steps = z["steps"].astype("int32")
        nearest = z["nearest"].astype("float32")

        def deq(a):
            b = a.astype("float32")
            b[a == NODATA] = np.nan
            return b / 100.0

        vv, vh = deq(z["vv"]), deq(z["vh"])
        # Chunked: the detector allocates several (cells x steps) working arrays and a whole
        # kabupaten at once is ~1 GB, which the 3 GB unit cap will not survive alongside pandas.
        parts = []
        CH = 30_000
        for c0 in range(0, vv.shape[0], CH):
            c1 = min(c0 + CH, vv.shape[0])
            r = detect_cycles(vv[c0:c1], vh[c0:c1], nearest, config.STEP_DAYS)
            if len(r["cell"]):
                r["cell"] = r["cell"] + c0
                parts.append(r)
        res = {k: np.concatenate([p[k] for p in parts]) if parts else np.array([])
               for k in ("cell", "transplant", "heading", "harvest", "depth", "drop", "rise",
                         "peak", "gap", "conf")}
        n = len(res["cell"])
        log(f"{kab}: {n:,} candidate cycles over {len(ck):,} cells "
            f"({n / max(len(ck), 1):.2f} per cell across {len(config.SEASONS)} seasons)")
        if n == 0:
            continue
        idx = res["cell"]
        d = pd.DataFrame({
            "kabupaten": kab,
            "kab_bps": ck["kab_bps"].to_numpy()[idx],
            "province": ck["province"].to_numpy()[idx],
            "kecamatan": ck["kecamatan"].to_numpy()[idx],
            "kec_id": ck["kec_id"].to_numpy()[idx],
            "cell_i": idx.astype("int32"),
            "ha": ck["ha"].to_numpy()[idx],
            "mask_class": (ck["mask_class"].to_numpy()[idx]
                           if "mask_class" in ck else np.zeros(n, "uint8")),
            "transplant": t0 + pd.to_timedelta(steps[res["transplant"]], "D"),
            "heading": t0 + pd.to_timedelta(steps[res["heading"]], "D"),
            "harvest": t0 + pd.to_timedelta(steps[np.minimum(res["harvest"],
                                                             len(steps) - 1)], "D"),
            "vv_min_db": res["depth"].astype("float32"),
            "vv_drop_db": res["drop"].astype("float32"),
            "vh_rise_db": res["rise"].astype("float32"),
            "vh_peak_db": res["peak"].astype("float32"),
            "max_gap_days": res["gap"].astype("float32"),
            "confidence": res["conf"],
        })
        d["year"] = d["harvest"].dt.year
        d["month"] = d["harvest"].dt.month
        d["season"] = d["harvest"].map(util.season_of)
        d["plant_season"] = d["transplant"].map(util.season_of)
        d["cycle_days"] = (d["harvest"] - d["transplant"]).dt.days
        frames.append(d)
        diag[kab] = {
            "cells": int(len(ck)), "cycles": int(n),
            "cycles_per_cell": round(float(n / len(ck)), 3),
            "median_cycle_days": int(d["cycle_days"].median()),
            "median_vv_min_db": round(float(d["vv_min_db"].median()), 2),
            "median_vv_drop_db": round(float(d["vv_drop_db"].median()), 2),
            "median_vh_rise_db": round(float(d["vh_rise_db"].median()), 2),
            # the bridge back to the literature: how many detected floods ALSO satisfy the
            # single-plot -17 dB rule.  Published rather than dropped.
            "share_events_below_literature_flood_db":
                round(float((d["vv_min_db"] < config.FLOOD_DB).mean()), 4),
            "median_confidence": round(float(d["confidence"].median()), 3),
            "cells_with_any_cycle": int(d["cell_i"].nunique()),
            "share_cells_with_any_cycle": round(float(d["cell_i"].nunique() / len(ck)), 3),
        }
        del z, vv, vh

    util.require(bool(frames), "phenology: no cycles detected anywhere — the detector or the "
                               "backscatter is broken; refusing to write an empty table")
    ph = pd.concat(frames, ignore_index=True)
    ph.to_parquet(OUT, index=False)
    ci, per = cropping_intensity(ph, cells)
    ci.to_parquet(config.DATA_DIR / "cropping_intensity.parquet", index=False)
    (config.DATA_DIR / "phenology_diag.json").write_text(json.dumps(diag, indent=1))
    log(f"phenology -> {OUT} ({len(ph):,} cycles); cropping intensity by kabupaten-year:")
    for _, r in ci.iterrows():
        log(f"    {r['kabupaten']:11s} {int(r['year'])}  {r['ci']:.2f} cycles/cell")


if __name__ == "__main__":
    main()
