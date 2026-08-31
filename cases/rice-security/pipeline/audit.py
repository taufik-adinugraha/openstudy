"""Stage R · audit — the review's own tests, run against the case's published output.

Nothing here re-uses a number the dashboard already printed.  Every quantity is recomputed
from ``data/*.parquet`` so the review can disagree with the case, and does.

THE CLAIM UNDER TEST
--------------------
The case's headline is "we find the rice; we find one crop where there are two" — that is, the
shortfall against BPS KSA is a CROPPING-INTENSITY failure, and paddy EXTENT is essentially
right.  That claim is arithmetically decomposable, because

    harvested area (a FLOW) = paddy extent (a STOCK) x cycles per cell per year

so the ratio of our harvested area to KSA's factors exactly into an extent recall and an
intensity recall.  If the diagnosis is right, the extent term is near one and the intensity term
carries the deficit.  Test A measures both.  Tests B and C ask where any intensity deficit sits —
in the cells an independent map calls double-cropping, and in the second (gadu) season — which
is what "one crop where there are two" predicts.  Test D asks whether revisit, not agronomy,
sets the detection rate, and test E settles that by thinning a dense kabupaten's own record.

OUTPUT: data/audit.json
"""

from __future__ import annotations

import json
import math

import config
import util
from util import log

OUT = config.DATA_DIR / "audit.json"
FULL_YEARS = (2023, 2024, 2025)
# Open-SEA-Rice-10 classes 1/2/3 are single/double/triple crop, so the class map is itself an
# independent statement about cropping intensity, published by someone else, at 10 m.
CLASS_CROPS = {0: 0, 1: 1, 2: 2, 3: 3}


def _r2(y, yh):
    import numpy as np

    y, yh = np.asarray(y, "float64"), np.asarray(yh, "float64")
    ok = np.isfinite(y) & np.isfinite(yh)
    if ok.sum() < 3:
        return None
    ss_res = float(((y[ok] - yh[ok]) ** 2).sum())
    ss_tot = float(((y[ok] - y[ok].mean()) ** 2).sum())
    return round(1 - ss_res / ss_tot, 4) if ss_tot else None


def _load():
    import pandas as pd

    D = config.DATA_DIR
    return dict(
        ph=pd.read_parquet(D / "phenology.parquet"),
        cells=pd.read_parquet(D / "cells.parquet"),
        model=pd.read_parquet(D / "model.parquet"),
        extent=pd.read_parquet(D / "extent_year.parquet"),
        area_y=pd.read_parquet(D / "area_year.parquet"),
        ksa_y=pd.read_parquet(D / "bps_kab_year.parquet"),
        ksa_m=pd.read_parquet(D / "bps_kab_month.parquet"),
    )


# ── A · the deficit, factored into extent and intensity ──────────────────────────────
def test_a(d):
    """Does the shortfall live in the stock or in the flow multiplier?"""
    import numpy as np
    import pandas as pd

    cells = d["cells"]
    prior = (cells.assign(crops=cells["mask_class"].map(CLASS_CROPS).fillna(0))
             .groupby("kabupaten", observed=True)
             .apply(lambda g: pd.Series({
                 "prior_extent_ha": float(g.loc[g["mask_class"] > 0, "ha"].sum()),
                 "prior_harvest_ha": float((g["crops"] * g["ha"]).sum()),
                 "kab_ha": float(g["ha"].sum()),
             }), include_groups=False)
             .reset_index())
    prior["prior_ci"] = prior["prior_harvest_ha"] / prior["prior_extent_ha"]

    ksa = d["ksa_y"].rename(columns={"kab": "kabupaten", "ha": "ksa_ha"})
    m = (d["extent"][["kabupaten", "year", "paddy_extent_ha"]]
         .merge(d["area_y"][["kabupaten", "year", "harvested_ha"]], on=["kabupaten", "year"])
         .merge(ksa[["kabupaten", "year", "ksa_ha", "benchmark_usable"]],
                on=["kabupaten", "year"], how="left")
         .merge(prior, on="kabupaten", how="left"))
    m = m[m["year"].isin(FULL_YEARS)].copy()
    m["det_ci"] = m["harvested_ha"] / m["paddy_extent_ha"]
    m["recall_extent"] = m["paddy_extent_ha"] / m["prior_extent_ha"]
    m["recall_ci"] = m["det_ci"] / m["prior_ci"]
    m["ratio_ksa"] = m["harvested_ha"] / m["ksa_ha"]
    m["ratio_map"] = m["harvested_ha"] / m["prior_harvest_ha"]

    def shares(r):
        le = -math.log(max(r["recall_extent"], 1e-9))
        lc = -math.log(max(r["recall_ci"], 1e-9))
        tot = le + lc
        return pd.Series({"log_deficit_extent": le, "log_deficit_ci": lc,
                          "share_extent": le / tot if tot > 0 else float("nan")})

    m = pd.concat([m, m.apply(shares, axis=1)], axis=1)

    rows = [{k: (None if (isinstance(v, float) and not np.isfinite(v)) else
                 (round(float(v), 4) if isinstance(v, (int, float, np.floating)) else v))
             for k, v in r.items()}
            for r in m[["kabupaten", "year", "paddy_extent_ha", "harvested_ha", "ksa_ha",
                        "prior_extent_ha", "prior_harvest_ha", "prior_ci", "det_ci",
                        "recall_extent", "recall_ci", "ratio_ksa", "ratio_map",
                        "share_extent", "benchmark_usable"]].to_dict("records")]

    use = m[m["benchmark_usable"].fillna(False)]
    agg = []
    for y, g in use.groupby("year"):
        ext, harv = g["paddy_extent_ha"].sum(), g["harvested_ha"].sum()
        pe, phh = g["prior_extent_ha"].sum(), g["prior_harvest_ha"].sum()
        ksa_s = g["ksa_ha"].sum()
        re_, rc = ext / pe, (harv / ext) / (phh / pe)
        le, lc = -math.log(re_), -math.log(rc)
        ext, harv, pe, phh, ksa_s = (float(v) for v in (ext, harv, pe, phh, ksa_s))
        agg.append({"year": int(y), "n_units": int(len(g)),
                    "detected_extent_ha": round(ext, 0), "detected_harvested_ha": round(harv, 0),
                    "map_extent_ha": round(pe, 0), "map_harvest_ha": round(phh, 0),
                    "ksa_ha": round(ksa_s, 0),
                    "det_ci": round(harv / ext, 3), "map_ci": round(phh / pe, 3),
                    "recall_extent": round(re_, 4), "recall_ci": round(rc, 4),
                    "ratio_ksa": round(harv / ksa_s, 4), "ratio_map": round(harv / phh, 4),
                    "map_vs_ksa": round(phh / ksa_s, 4),
                    "share_extent": round(le / (le + lc), 4)})
    return {"by_kabupaten_year": rows, "aggregate": agg,
            "note": ("harvested = extent x cycles/cell, so the ratio to the benchmark factors "
                     "exactly. share_extent is the extent term's share of the log deficit.")}


# ── B · stratify by the independent map's own single/double/triple class ─────────────
def test_b(d):
    """The decisive test of 'one crop where there are two'.

    If the detector finds one crop where an independent 10 m map says two, its cycles-per-year
    should be near 1 on class-2 cells and clearly LOWER on class-1 cells — that is, it should
    still separate single-croppers from double-croppers, just with the top clipped.  If instead
    it returns the same number on both, it carries no cropping-intensity information at all and
    the deficit is not a second-season deficit.
    """
    import numpy as np
    import pandas as pd

    ph, cells = d["ph"], d["cells"]
    tot = (cells.groupby(["kabupaten", "mask_class"], observed=True)["ha"]
           .agg(["size", "sum"]).reset_index()
           .rename(columns={"size": "n_cells", "sum": "cells_ha"}))
    out_cls, out_kab = [], []
    for y in FULL_YEARS:
        p = ph[ph["year"] == y]
        per = (p.groupby(["kabupaten", "mask_class", "cell_i"], observed=True)
               .size().rename("cyc").reset_index())
        g = (per.groupby(["kabupaten", "mask_class"], observed=True)
             .agg(cells_det=("cell_i", "nunique"), cycles=("cyc", "sum"),
                  cond_ci=("cyc", "mean")).reset_index()
             .merge(tot, on=["kabupaten", "mask_class"], how="right").fillna(
                 {"cells_det": 0, "cycles": 0}))
        g["year"] = y
        g["detect_rate"] = g["cells_det"] / g["n_cells"]
        g["uncond_ci"] = g["cycles"] / g["n_cells"]
        out_kab.append(g)
    kab = pd.concat(out_kab, ignore_index=True)
    for (cls, y), g in kab.groupby(["mask_class", "year"]):
        n = g["n_cells"].sum()
        out_cls.append({"mask_class": int(cls), "map_crops": CLASS_CROPS[int(cls)],
                        "year": int(y), "n_cells": int(n),
                        "cells_detected": int(g["cells_det"].sum()),
                        "detect_rate": round(float(g["cells_det"].sum() / n), 4),
                        "cycles": int(g["cycles"].sum()),
                        "cond_cycles_per_cell": round(
                            float(g["cycles"].sum() / max(g["cells_det"].sum(), 1)), 4),
                        "uncond_cycles_per_cell": round(float(g["cycles"].sum() / n), 4)})
    # the single number that settles it: conditional intensity on class 1 vs class 2 vs class 3
    piv = {}
    for cls in (1, 2, 3):
        r = [x for x in out_cls if x["mask_class"] == cls]
        piv[cls] = {"cond": round(float(np.mean([x["cond_cycles_per_cell"] for x in r])), 4),
                    "uncond": round(float(np.mean([x["uncond_cycles_per_cell"] for x in r])), 4),
                    "detect_rate": round(float(np.mean([x["detect_rate"] for x in r])), 4)}
    piv["ratio_cond_2_over_1"] = round(piv[2]["cond"] / piv[1]["cond"], 4)
    piv["ratio_map_2_over_1"] = 2.0
    return {"by_class_year": out_cls, "summary": piv,
            "by_kabupaten_class": [
                {k: (int(v) if isinstance(v, (np.integer,)) else
                     (round(float(v), 4) if isinstance(v, (float, np.floating)) else v))
                 for k, v in r.items()}
                for r in kab.drop(columns=["cells_ha"]).to_dict("records")]}


# ── C · is the deficit in the second (gadu) season? ──────────────────────────────────
def test_c(d):
    """Detected against KSA by calendar month, over the benchmark years.

    Java's calendar is bimodal: a wet-season (rendeng) harvest peaking about March and a second
    (gadu) harvest peaking about July-September.  'One crop where there are two' predicts the
    deficit is concentrated in the second lobe.  A flat ratio across the year says the detector
    under-counts uniformly, which is a different fault with a different fix.
    """
    import numpy as np
    import pandas as pd

    m = d["model"]
    p = m[m["year"].isin(FULL_YEARS) & m["benchmark_usable"] & m["ksa_ha"].notna()]
    g = (p.groupby("month").agg(ours=("harvested_ha", "sum"), ksa=("ksa_ha", "sum"))
         .reindex(range(1, 13)).fillna(0.0).reset_index())
    g["ratio"] = g["ours"] / g["ksa"].replace(0, np.nan)
    g["share_ours"] = g["ours"] / g["ours"].sum()
    g["share_ksa"] = g["ksa"] / g["ksa"].sum()

    # lobes: rendeng harvest Feb-May, gadu harvest Jul-Oct (BPS's own two peaks)
    def lobes(ours_v, ksa_v):
        lob = {}
        for name, months in (("rendeng", (2, 3, 4, 5)), ("gadu", (7, 8, 9, 10)),
                             ("shoulder", (1, 6, 11, 12))):
            i = [mm - 1 for mm in months]
            o, k = float(ours_v[i].sum()), float(ksa_v[i].sum())
            lob[name] = {"months": list(months), "ours_ha": round(o, 0), "ksa_ha": round(k, 0),
                         "ratio": round(o / k, 4),
                         "share_ours": round(o / float(ours_v.sum()), 4),
                         "share_ksa": round(k / float(ksa_v.sum()), 4)}
        lob["gadu_over_rendeng_ratio"] = round(lob["gadu"]["ratio"] / lob["rendeng"]["ratio"], 4)
        return lob

    ours_v = g["ours"].to_numpy("float64")
    ksa_v = g["ksa"].to_numpy("float64")
    lob = lobes(ours_v, ksa_v)
    # The detector's harvest date runs late (gate G-I2), and a late bias moves mass out of the
    # first lobe into the second — which would manufacture exactly the pattern below.  So the
    # same comparison is repeated with our curve rolled back by the whole-curve lag measured in
    # test G, and the conclusion has to survive both.
    lag = int(round(_median_lag(m)))
    shifted = np.roll(ours_v, -lag)
    lob_adj = lobes(shifted, ksa_v)
    return {"by_month": [{"month": int(r.month), "ours_ha": round(float(r.ours), 0),
                          "ksa_ha": round(float(r.ksa), 0),
                          "ratio": (None if not np.isfinite(r.ratio) else round(float(r.ratio), 4)),
                          "share_ours": round(float(r.share_ours), 4),
                          "share_ksa": round(float(r.share_ksa), 4)}
                         for r in g.itertuples()],
            "by_month_lag_corrected": [{"month": i + 1, "ours_ha": round(float(v), 0)}
                                       for i, v in enumerate(shifted)],
            "lag_months_removed": lag,
            "lobes": lob, "lobes_lag_corrected": lob_adj}


def _median_lag(m):
    """Median whole-curve lag of our monthly harvest against KSA's, in months."""
    import numpy as np

    p = m[m["benchmark_usable"] & m["ksa_ha"].notna() & m["year"].isin(FULL_YEARS)]
    lags = []
    for _, g in p.groupby(["kabupaten", "year"], observed=True):
        ours, ksa = np.zeros(12), np.zeros(12)
        for r in g.itertuples():
            ours[r.month - 1] += float(r.harvested_ha)
            ksa[r.month - 1] += float(r.ksa_ha)
        if ours.sum() <= 0 or ksa.sum() <= 0 or g["ksa_ha"].notna().sum() < 10:
            continue
        a = (ours - ours.mean()) / (ours.std() or 1)
        b = (ksa - ksa.mean()) / (ksa.std() or 1)
        cc = [float((np.roll(a, -k) * b).mean()) for k in range(12)]
        k = int(np.argmax(cc))
        lags.append(k - 12 if k > 6 else k)
    return float(np.median(lags)) if lags else 0.0


# ── D · revisit, per kabupaten and per year ──────────────────────────────────────────
def test_d(d):
    """How often the satellite actually looked, and whether detection follows it.

    The record opens 2022-07-01, six months AFTER Sentinel-1B failed (23 Dec 2021), so the
    before/after test on that failure is not available in this sample — the whole record is on
    the degraded side of it.  The mirror-image experiment IS available: Sentinel-1C and 1D come
    online during the record, so revisit RISES within the sample.
    """
    import numpy as np
    import pandas as pd

    t0 = pd.Timestamp(config.SAR_START)
    rows = []
    for kab in config.SCOPE_DEEP:
        f = config.DATA_DIR / "bs" / f"{kab}.npz"
        if not f.exists():
            continue
        z = np.load(f)
        # obs_days holds the genuine acquisition offsets, one entry per (orbit, date) slot the
        # ingest actually retrieved.  The regular grid and its `nearest` array are derived from
        # these, so this is the raw look count rather than a resampling artefact.
        od = np.sort(np.unique(z["obs_days"].astype("int64")))
        dates = t0 + pd.to_timedelta(od, "D")
        for y in FULL_YEARS:
            sel = dates.year == y
            dy = od[sel]
            if dy.size < 2:
                continue
            gaps = np.diff(dy).astype("float64")
            rows.append({"kabupaten": kab, "year": int(y), "n_acquisitions": int(dy.size),
                         "n_orbits": int(len(np.unique(z["obs_orbit"]))),
                         "median_gap_days": round(float(np.median(gaps)), 2),
                         "p90_gap_days": round(float(np.percentile(gaps, 90)), 2),
                         "max_gap_days": round(float(gaps.max()), 1)})
        del z
    df = pd.DataFrame(rows)
    ex = d["extent"][["kabupaten", "year", "paddy_extent_ha"]]
    ar = d["area_y"][["kabupaten", "year", "harvested_ha"]]
    ci = d["model"].groupby(["kabupaten", "year"], observed=True)["ci"].first().reset_index()
    df = df.merge(ex, on=["kabupaten", "year"]).merge(ar, on=["kabupaten", "year"]) \
           .merge(ci, on=["kabupaten", "year"], how="left")
    cells = d["cells"]
    pr = (cells[cells["mask_class"] > 0].groupby("kabupaten", observed=True)["ha"]
          .sum().reset_index(name="prior_extent_ha"))
    df = df.merge(pr, on="kabupaten", how="left")
    df["recall_extent"] = df["paddy_extent_ha"] / df["prior_extent_ha"]
    by_year = (df.groupby("year").agg(acq=("n_acquisitions", "mean"),
                                      gap=("median_gap_days", "mean"),
                                      recall=("recall_extent", "mean"),
                                      ci=("ci", "mean")).reset_index())
    x = df["median_gap_days"].to_numpy("float64")
    y_ = df["recall_extent"].to_numpy("float64")
    ok = np.isfinite(x) & np.isfinite(y_)
    corr = float(np.corrcoef(x[ok], y_[ok])[0, 1]) if ok.sum() > 2 else None
    return {"rows": [{k: (round(float(v), 4) if isinstance(v, (float, np.floating)) else
                          (int(v) if isinstance(v, (int, np.integer)) else v))
                      for k, v in r.items()} for r in df.to_dict("records")],
            "by_year": [{"year": int(r.year), "mean_acquisitions": round(float(r.acq), 1),
                         "mean_median_gap_days": round(float(r.gap), 2),
                         "mean_extent_recall": round(float(r.recall), 4),
                         "mean_cycles_per_year": round(float(r.ci), 3)}
                        for r in by_year.itertuples()],
            "corr_gap_vs_extent_recall": (round(corr, 4) if corr is not None else None),
            "s1b_failure": "2021-12-23", "record_starts": config.SAR_START,
            "s1b_test_possible": False}


# ── E · thin a dense kabupaten's own record and re-detect ────────────────────────────
def test_e(d, kab=None, thins=(1, 2, 3, 4)):
    """The controlled experiment: same fields, same detector, fewer looks.

    Every other explanation for the cross-section between kabupaten — soil, variety, plot size,
    irrigation command — is held fixed by construction, because it is the same kabupaten.  Only
    the number of looks changes.
    """
    import numpy as np
    import pandas as pd

    import phenology as PH

    cells = d["cells"]
    if kab is None:                                       # the densest record we hold
        best, bn = None, -1
        for k in config.SCOPE_DEEP:
            f = config.DATA_DIR / "bs" / f"{k}.npz"
            if not f.exists():
                continue
            z = np.load(f)
            n = int(np.unique(z["obs_days"]).size)
            if n > bn:
                best, bn = k, n
            del z
        kab = best
    f = config.DATA_DIR / "bs" / f"{kab}.npz"
    z = np.load(f)
    steps = z["steps"].astype("float64")
    near0 = z["nearest"].astype("float64")
    half = config.STEP_DAYS / 2.0

    def deq(a):
        b = a.astype("float32")
        b[a == -32768] = np.nan
        return b / 100.0

    vv0, vh0 = deq(z["vv"]), deq(z["vh"])
    ck = cells[cells["kabupaten"] == kab].reset_index(drop=True)
    ha = float(ck["ha"].iloc[0])
    prior_cells = int((ck["mask_class"] > 0).sum()) if "mask_class" in ck else len(ck)
    # a grid step is backed by a genuine look when an acquisition falls within half a step of it
    # — backscatter.resample()'s own definition, reused rather than reinvented
    real = np.flatnonzero(near0 <= half)
    out = []
    for t in thins:
        keep = real[::t]
        ks = steps[keep]
        # Re-interpolate the series through only the surviving looks, which is what the pipeline
        # would have produced had the other acquisitions never existed.  Masking instead of
        # re-interpolating would leave the dropped observations' information in the neighbouring
        # grid steps and understate the cost of the thinning.
        j = np.clip(np.searchsorted(ks, steps, "right") - 1, 0, len(ks) - 1)
        j2 = np.clip(j + 1, 0, len(ks) - 1)
        span = np.where(ks[j2] > ks[j], ks[j2] - ks[j], 1.0)
        w = np.clip((steps - ks[j]) / span, 0.0, 1.0)
        near = np.abs(steps[:, None] - ks[None, :]).min(axis=1).astype("float32")
        gaps = np.diff(ks)
        ncyc, ncell = 0, set()
        vhr = []                       # the case's own damping observable, remeasured per rung
        CH = 30_000
        for c0 in range(0, vv0.shape[0], CH):
            c1 = min(c0 + CH, vv0.shape[0])
            vv = (1 - w) * vv0[c0:c1][:, j] + w * vv0[c0:c1][:, j2]
            vh = (1 - w) * vh0[c0:c1][:, j] + w * vh0[c0:c1][:, j2]
            r = PH.detect_cycles(vv.astype("float32"), vh.astype("float32"), near,
                                 config.STEP_DAYS)
            ncyc += len(r["cell"])
            if len(r["cell"]):
                ncell.update((r["cell"] + c0).tolist())
            pm = (ck["mask_class"].to_numpy()[c0:c1] > 0)
            if pm.any():
                with np.errstate(all="ignore"):
                    vhr.append(np.nanmax(vh[pm], axis=1) - np.nanmin(vh[pm], axis=1))
            del vv, vh
        with np.errstate(all="ignore"):
            vh_range = float(np.nanmedian(np.concatenate(vhr))) if vhr else float("nan")
        out.append({"thin": int(t), "n_acquisitions": int(keep.size),
                    "median_gap_days": round(float(np.median(gaps)), 2),
                    "cycles": int(ncyc), "cells_detected": int(len(ncell)),
                    "extent_ha": round(len(ncell) * ha, 0),
                    "harvested_ha": round(ncyc * ha, 0),
                    "extent_recall_vs_prior": round(len(ncell) / max(prior_cells, 1), 4),
                    "vh_range_db": round(vh_range, 2),
                    "cycles_per_detected_cell": round(ncyc / max(len(ncell), 1), 4)})
        log(f"  thin 1/{t}: {keep.size} looks, median gap "
            f"{out[-1]['median_gap_days']}d -> {ncyc:,} cycles over {len(ncell):,} cells")
    base = out[0]
    for r in out:
        r["cycles_vs_full"] = round(r["cycles"] / base["cycles"], 4)
        r["extent_vs_full"] = round(r["cells_detected"] / base["cells_detected"], 4)
    return {"kabupaten": kab, "seasons": len(config.SEASONS),
            "prior_rice_cells": prior_cells, "ladder": out,
            "note": ("the same kabupaten, the same fields, the same detector — only the number "
                     "of looks changes, so soil, variety, plot size and irrigation command are "
                     "held fixed by construction")}


# ── F · what the published calibration actually is ───────────────────────────────────
def test_f(d):
    """Decompose the calibrated series into the part the satellite moved and the rest."""
    import numpy as np
    import pandas as pd

    meta = json.loads((config.DATA_DIR / "model_meta.json").read_text())
    b = meta["calibration"]["coefficients"]
    m = d["model"].copy()
    det = m["harvested_ha"] / 1000.0
    vhr = m["vh_range_db"].fillna(m["vh_range_db"].median())
    sat = b["detected_kha"] * det + b["detected_kha_x_inv_vh_range"] * det / np.maximum(vhr, 1) * 10
    rest = m["calibrated_ha"] / 1000.0 - sat
    m["sat_kha"], m["rest_kha"] = sat, rest
    eff = (b["detected_kha"] + b["detected_kha_x_inv_vh_range"] * 10 / np.maximum(vhr, 1))
    per_kab = (m.assign(eff=eff).groupby("kabupaten", observed=True)
               .agg(vh_range_db=("vh_range_db", "first"), eff_slope=("eff", "first"),
                    sat=("sat_kha", "mean"), cal=("calibrated_ha", "mean")).reset_index())
    per_kab["sat_share"] = per_kab["sat"] / (per_kab["cal"] / 1000.0)
    use = m[m["ksa_ha"].notna() & m["benchmark_usable"]]
    hold = use[use["is_holdout"]]
    return {
        "coefficients": b,
        "n_fit_rows": meta["calibration"]["n_fit_rows"],
        "r2_in_sample_monthly": meta["calibration"]["r2_in_sample"],
        "r2_monthly_calibrated": _r2(use["ksa_ha"], use["calibrated_ha"]),
        "r2_monthly_uncalibrated": _r2(use["ksa_ha"], use["harvested_ha"]),
        "r2_monthly_holdout": _r2(hold["ksa_ha"], hold["calibrated_ha"]) if len(hold) > 3 else None,
        "r2_monthly_mean_only": _r2(use["ksa_ha"],
                                    np.full(len(use), float(use["ksa_ha"].mean()))),
        "mean_sat_share": round(float((m["sat_kha"] / (m["calibrated_ha"] / 1000.0))
                                      .replace([np.inf, -np.inf], np.nan).mean()), 4),
        "per_kabupaten": [{"kabupaten": r.kabupaten,
                           "vh_range_db": round(float(r.vh_range_db), 2),
                           "effective_slope_on_detected": round(float(r.eff_slope), 4),
                           "mean_satellite_kha": round(float(r.sat), 3),
                           "mean_calibrated_kha": round(float(r.cal / 1000.0), 3),
                           "satellite_share": round(float(r.sat_share), 4)}
                          for r in per_kab.itertuples()],
        "sign_flip_vh_range_db": round(-b["detected_kha_x_inv_vh_range"] * 10 / b["detected_kha"], 3),
    }


# ── G · the timing gate, re-scored on the whole curve instead of its argmax ──────────
def test_g(d):
    """Is the 5-week timing failure a measurement error or an estimator artefact?

    The gate scores the distance between two ARGMAXES of a bimodal 12-month curve, which flips
    by five months when the two lobes are close.  The shape-based alternative is the circular
    cross-correlation lag: shift our curve around the calendar and take the shift that best
    matches KSA's.  If the curve is in the right place and only its taller lobe differs, the two
    scores diverge, and the gate is measuring the wrong thing.
    """
    import numpy as np
    import pandas as pd

    m = d["model"]
    p = m[m["benchmark_usable"] & m["ksa_ha"].notna() & m["year"].isin(FULL_YEARS)]
    rows = []
    for (kab, y), g in p.groupby(["kabupaten", "year"], observed=True):
        v = np.zeros(12), np.zeros(12)
        ours, ksa = np.zeros(12), np.zeros(12)
        for r in g.itertuples():
            ours[r.month - 1] += float(r.harvested_ha)
            ksa[r.month - 1] += float(r.ksa_ha)
        if ours.sum() <= 0 or ksa.sum() <= 0 or (g["ksa_ha"].notna().sum() < 10):
            continue
        a = (ours - ours.mean()) / (ours.std() or 1)
        b = (ksa - ksa.mean()) / (ksa.std() or 1)
        cc = np.array([float((np.roll(a, -k) * b).mean()) for k in range(12)])
        lag = int(np.argmax(cc))
        lag_signed = lag - 12 if lag > 6 else lag
        rows.append({"kabupaten": kab, "year": int(y),
                     "argmax_ours": int(np.argmax(ours)) + 1,
                     "argmax_ksa": int(np.argmax(ksa)) + 1,
                     "argmax_error_months": int(((np.argmax(ours) - np.argmax(ksa) + 6) % 12) - 6),
                     "xcorr_lag_months": lag_signed,
                     "xcorr_at_lag": round(float(cc[lag]), 4),
                     "xcorr_at_zero": round(float(cc[0]), 4)})
    df = pd.DataFrame(rows)
    return {"rows": rows,
            "median_abs_argmax_error_months": round(float(df["argmax_error_months"].abs().median()), 2),
            "median_abs_xcorr_lag_months": round(float(df["xcorr_lag_months"].abs().median()), 2),
            "median_xcorr_at_lag": round(float(df["xcorr_at_lag"].median()), 4),
            "median_xcorr_at_zero": round(float(df["xcorr_at_zero"].median()), 4),
            "n": int(len(df))}


# ── H · does the benchmark move? ─────────────────────────────────────────────────────
def test_h(d):
    """The 'benchmark is the moving target' hypothesis, tested rather than asserted.

    BPS replaced the eye-estimate method with the KSA area sampling frame from reference year
    2018.  That break is real and large — but it is six years before this record opens, so it
    cannot explain a 2023-2025 shortfall.  What CAN is a break inside the window, so the KSA
    series is tested for one, and cross-checked against a completely independent measurement:
    an unrelated 10 m rice map, published by other people, whose own single/double/triple class
    implies a harvested area for exactly these six kabupaten.
    """
    import numpy as np
    import pandas as pd

    ksa = d["ksa_y"]
    ours = ksa[ksa["kab"].isin(config.SCOPE_DEEP) & ksa["benchmark_usable"]]
    series = (ours.groupby("year")["ha"].agg(["sum", "size"]).reset_index()
              .rename(columns={"sum": "ha", "size": "n_units"}))
    # Grobogan's provincial table breaks for 2024-25 (a published BPS defect the pipeline already
    # refuses), so a six-unit series is not comparable across years.  The five-unit series is.
    five = [k for k in config.SCOPE_DEEP if k != "Grobogan"]
    f5 = (ksa[ksa["kab"].isin(five) & ksa["benchmark_usable"]]
          .groupby("year")["ha"].agg(["sum", "size"]).reset_index())
    f5 = f5[f5["size"] == 5]
    v5 = f5["sum"].to_numpy("float64")
    nat = config.BPS_BREAK["ksa_series_mha"]
    cells = d["cells"]
    crops = cells["mask_class"].map(CLASS_CROPS).fillna(0)
    map_h = float((crops * cells["ha"]).sum())
    six = series[series["n_units"] == 6]
    return {
        "break_year": config.KSA_FIRST_YEAR,
        "pre_ksa_last_year": config.BPS_BREAK["last_sp_year"],
        "pre_ksa_ha": config.BPS_BREAK["last_sp_ha"],
        "first_ksa_ha": config.BPS_BREAK["first_ksa_ha"],
        "break_pct": round(100 * (config.BPS_BREAK["first_ksa_ha"]
                                  / config.BPS_BREAK["last_sp_ha"] - 1), 2),
        "national_ksa_mha": nat,
        "record_opens": config.SAR_START,
        "break_inside_window": False,
        "six_kabupaten_ksa_by_year": [{"year": int(r.year), "ha": round(float(r.ha), 0),
                                       "n_units": int(r.n_units)} for r in series.itertuples()],
        "five_kabupaten_ksa_by_year": [{"year": int(r[1]), "ha": round(float(r[2]), 0)}
                                       for r in f5.itertuples()],
        "five_kabupaten_range_pct": round(100 * (v5.max() / v5.min() - 1), 2) if v5.size else None,
        "five_kabupaten_cv_pct": round(100 * float(v5.std() / v5.mean()), 2) if v5.size else None,
        "map_implied_harvest_ha": round(map_h, 0),
        "map_vs_ksa_pct": (round(100 * (map_h / float(six["ha"].iloc[-1]) - 1), 2)
                           if len(six) else None),
        "map_vs_ksa_year": (int(six["year"].iloc[-1]) if len(six) else None),
        "national_2025_jump_ha": config.BPS_2025_JUMP_HA,
        "national_2025_jump_pct": round(100 * (nat[2025] / nat[2024] - 1), 2),
    }


# ── I · precision and recall against the independent map, per year ──────────────────
def test_i(d):
    """The page quotes 83 % agreement 'on our own area' as evidence that we find the rice.

    That is a PRECISION, and precision is the one number a detector can always buy by detecting
    less.  Both halves are recomputed here, and per year rather than pooled over the whole
    record, because pooling four years of detections against a one-year map inflates recall.
    """
    import numpy as np
    import pandas as pd

    ph, cells = d["ph"], d["cells"]
    prior_cells = int((cells["mask_class"] > 0).sum())
    rows = []
    for y in list(FULL_YEARS) + ["pooled"]:
        p = ph if y == "pooled" else ph[ph["year"] == y]
        det = p.drop_duplicates(["kabupaten", "cell_i"])
        n_det = int(len(det))
        n_in = int((det["mask_class"] > 0).sum())
        rows.append({"year": y, "detected_cells": n_det, "in_prior": n_in,
                     "precision": round(n_in / max(n_det, 1), 4),
                     "recall": round(n_in / max(prior_cells, 1), 4),
                     "f1": round(2 * n_in / max(n_det + prior_cells, 1), 4)})
    return {"prior_rice_cells": prior_cells, "rows": rows,
            "note": "precision = share of our detections the map also calls rice; recall = share "
                    "of the map's rice we detect"}


def main() -> None:
    util.guard_disk()
    d = _load()
    log(f"audit: {len(d['ph']):,} cycles, {len(d['cells']):,} cells")
    out = {"generated_utc": __import__("pandas").Timestamp.utcnow()
           .strftime("%Y-%m-%dT%H:%M:%SZ"),
           "full_years": list(FULL_YEARS)}
    log("A · factoring the deficit into extent and intensity")
    out["A_decomposition"] = test_a(d)
    for r in out["A_decomposition"]["aggregate"]:
        log(f"    {r['year']}  extent recall {r['recall_extent']:.3f} x intensity recall "
            f"{r['recall_ci']:.3f} = {r['ratio_ksa']:.3f} of KSA "
            f"({r['share_extent']:.0%} of the deficit is extent)")
    log("B · stratifying by the independent map's single/double/triple class")
    out["B_by_crop_class"] = test_b(d)
    s = out["B_by_crop_class"]["summary"]
    for c in (1, 2, 3):
        log(f"    map class {c} ({c} crop/yr): detect rate {s[c]['detect_rate']:.3f}, "
            f"cycles per detected cell {s[c]['cond']:.3f}, per cell {s[c]['uncond']:.3f}")
    log("C · seasonal location of the deficit")
    out["C_seasonal"] = test_c(d)
    lb = out["C_seasonal"]["lobes"]
    log(f"    rendeng ratio {lb['rendeng']['ratio']:.3f}, gadu ratio {lb['gadu']['ratio']:.3f}")
    log("D · revisit")
    out["D_revisit"] = test_d(d)
    for r in out["D_revisit"]["by_year"]:
        log(f"    {r['year']}  {r['mean_acquisitions']:.0f} acquisitions, median gap "
            f"{r['mean_median_gap_days']:.1f} d, extent recall {r['mean_extent_recall']:.3f}")
    log("E · thinning the densest record (same fields, fewer looks)")
    out["E_thinning"] = test_e(d)
    log("F · what the calibration is")
    out["F_calibration"] = test_f(d)
    log(f"    monthly R2 calibrated {out['F_calibration']['r2_monthly_calibrated']}, "
        f"sign flips below VH range {out['F_calibration']['sign_flip_vh_range_db']} dB")
    log("G · re-scoring the timing gate on the whole curve")
    out["G_timing"] = test_g(d)
    log(f"    argmax |error| {out['G_timing']['median_abs_argmax_error_months']} months vs "
        f"cross-correlation lag {out['G_timing']['median_abs_xcorr_lag_months']} months")
    log("H · does the benchmark move?")
    out["H_benchmark"] = test_h(d)
    log(f"    independent map implies {out['H_benchmark']['map_implied_harvest_ha']:,.0f} ha "
        f"vs KSA — {out['H_benchmark']['map_vs_ksa_pct']:+.1f}%")
    log("I · precision and recall against the independent map")
    out["I_prf"] = test_i(d)
    for r in out["I_prf"]["rows"]:
        log(f"    {str(r['year']):7s} precision {r['precision']:.3f}  recall {r['recall']:.3f}")
    OUT.write_text(json.dumps(out, indent=1, default=str))
    log(f"audit -> {OUT}")


if __name__ == "__main__":
    main()
