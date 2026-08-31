"""Stage: review — the extra tests the adversarial review asked for.

Six tests, each pre-specified before it was run, all on data already on disk.
Nothing here changes the published pipeline; it writes data/derived/review.json,
which pipeline/article.py turns into the review article's figures.

  A  Deceleration.        The clock extrapolates a 2017–2023 rate to 2050. Is the
                          rate constant? Refit every Susilo et al. 2023 GNSS
                          station's vertical velocity on an early and a late
                          sub-period, seasonality removed, and difference them.
  B  Reference frame.     The gate compares an InSAR rate measured over 2017–2023
                          against a GNSS rate published for the station's whole
                          record (2010–2021 at CJKT). Refit the GNSS on the InSAR
                          window and see whether the disagreement survives. A
                          residual common to every station is a datum offset; a
                          residual that vanishes was a period mismatch.
  C  Ground estimator.    "People below +1 m" is evaluated on each 100 m cell's
                          LOWEST 30 m pixel. Recompute on the cell mean and on the
                          cell's 25th-percentile pixel and report the spread.
  D  DEM epoch.           GLO-30 was acquired 2011–2015 and is used as the 2025
                          surface. Apply the correction the pipeline declines
                          (ground(2025) = DEM + v·(2025−2013)) and re-count.
  E  Datum.               The 0 m threshold is fixed EGM2008 mean sea level for
                          every year to 2050. Sweep the threshold upward and
                          report the sensitivity of the count to the datum.
  F  Does the radar matter?  Re-run the 2050 exposure ranking with velocity set
                          to zero everywhere and compare the ranking.
  G  Decay scenario.      Re-run the clock with the rate decaying at the halving
                          time the Jakarta GNSS station actually exhibits, instead
                          of held constant, and report the 2050 spread.
  H  Replication.         Reproduce the depositors' own published per-district
                          statistics (Ohenhen et al. 2026, table S2) from our
                          regridded copy of their field — the like-for-like check
                          that the case's gridding and zonal step are faithful.

Run: uv run python pipeline/review.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

import config

DERIVED = config.DATA_DIR / "derived"
RAW = config.RAW
BASE_YEAR = 2025
DEM_EPOCH = 2013          # midpoint of the GLO-30 acquisition window 2011–2015
INSAR_START = 2017.0      # the deposited field's window
SPLIT = 2017.0            # early / late sub-period boundary for test A
MIN_YEARS = 3.0           # a sub-period shorter than this is not fitted


def log(m: str) -> None:
    print(f"[review] {m}", flush=True)


# ───────────────────────────────────────────────────────────── GNSS helpers
def station_meta(pos: Path) -> tuple[float, float] | None:
    """(lon, lat) from the .pos header's NEU reference position."""
    with open(pos) as f:
        for line in f:
            if line.startswith("NEU Reference position"):
                m = re.findall(r"[-+]?\d+\.\d+", line)
                if len(m) >= 2:
                    return float(m[1]), float(m[0])
            if line.startswith("YYYYMMDD"):
                break
    return None


def read_rneu(p: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(decimal year, vertical metres, vertical sigma) from a Susilo et al. .rneu file."""
    t, v, s = [], [], []
    for line in open(p):
        if line.startswith("#"):
            continue
        f = line.split()
        if len(f) < 8:
            continue
        try:
            t.append(float(f[1])); v.append(float(f[4])); s.append(float(f[7]))
        except ValueError:
            continue
    return np.array(t), np.array(v), np.array(s)


def fit_rate(t: np.ndarray, v: np.ndarray, quadratic: bool = False) -> dict | None:
    """Vertical rate in mm/yr. Design: constant + trend (+ curvature) + annual + semi-annual.

    The sinusoids absorb the hydrological load cycle, which in Jakarta is large enough
    to change a short-window trend if it is left in.
    """
    if t.size < 200 or (t.max() - t.min()) < MIN_YEARS:
        return None
    t0 = t.mean()
    x = t - t0
    cols = [np.ones_like(x), x]
    names = ["c", "rate"]
    if quadratic:
        cols.append(x ** 2); names.append("curv")
    for k in (1, 2):
        cols += [np.sin(2 * np.pi * k * t), np.cos(2 * np.pi * k * t)]
        names += [f"s{k}", f"c{k}"]
    A = np.column_stack(cols)
    beta, *_ = np.linalg.lstsq(A, v, rcond=None)
    resid = v - A @ beta
    dof = max(len(v) - A.shape[1], 1)
    s2 = float(resid @ resid) / dof
    cov = s2 * np.linalg.pinv(A.T @ A)
    i = names.index("rate")
    out = {"rate_mm": float(beta[i] * 1000), "se_mm": float(np.sqrt(cov[i, i]) * 1000),
           "n": int(t.size), "t0": float(t.min()), "t1": float(t.max()),
           "rms_mm": float(np.sqrt(s2) * 1000)}
    if quadratic:
        j = names.index("curv")
        # d/dt of (2·curv·x) → mm/yr² ; positive = slowing down if the rate is negative
        out["accel_mm_yr2"] = float(2 * beta[j] * 1000)
        out["accel_se"] = float(2 * np.sqrt(cov[j, j]) * 1000)
    return out


def sample_insar(grid, west, north, res, lon, lat, radius):
    r = int(round(radius / res))
    ci, ri = int((lon - west) / res), int((north - lat) / res)
    win = grid[max(ri - r, 0):ri + r + 1, max(ci - r, 0):ci + r + 1]
    vals = win[np.isfinite(win)]
    if not vals.size:
        return None, 0
    return float(np.median(vals)), int(vals.size)


# ───────────────────────────────────────────────────────── A + B: the GNSS tests
def gnss_tests() -> dict:
    import rasterio

    with rasterio.open(DERIVED / "velocity_ohenhen2026_cmyr.tif") as src:
        vel = src.read(1)
        b = src.bounds
        res = abs(src.transform.a)

    rows = []
    for rneu in sorted((RAW / "gnss").glob("*_clean_Noff1site.rneu")):
        st = rneu.name.split("_")[0]
        pos = RAW / "gnss" / f"{st}.sub.final_igb14.pos"
        ll = station_meta(pos) if pos.exists() else None
        t, v, _ = read_rneu(rneu)
        if t.size == 0:
            continue
        full = fit_rate(t, v)
        curv = fit_rate(t, v, quadratic=True)
        early = fit_rate(*(lambda m: (t[m], v[m]))(t < SPLIT))
        late = fit_rate(*(lambda m: (t[m], v[m]))(t >= SPLIT))
        # robustness: the archive documents equipment changes that are not modelled as
        # offsets. Refit the late window from 2018 so any step in late 2017 is excluded.
        late_post = fit_rate(*(lambda m: (t[m], v[m]))(t >= 2018.0))
        ins, nins = (None, 0)
        if ll and b.left <= ll[0] <= b.right and b.bottom <= ll[1] <= b.top:
            ins, nins = sample_insar(vel, b.left, b.top, res, ll[0], ll[1], 0.003)
        rows.append({
            "station": st, "lon": ll[0] if ll else None, "lat": ll[1] if ll else None,
            "span": [float(t.min()), float(t.max())], "n": int(t.size),
            "full": full, "early": early, "late": late, "late_post": late_post,
            "accel_mm_yr2": curv.get("accel_mm_yr2") if curv else None,
            "accel_se": curv.get("accel_se") if curv else None,
            "insar_mm": None if ins is None else round(ins * 10, 2), "insar_cells": nins,
        })

    # test A — deceleration, on stations with both sub-periods and real subsidence
    pairs = [r for r in rows if r["early"] and r["late"]]
    sink = [r for r in pairs if r["early"]["rate_mm"] < -2.0]
    dif = [r["late"]["rate_mm"] - r["early"]["rate_mm"] for r in sink]
    testA = {
        "n_stations": len(rows), "n_paired": len(pairs), "split": SPLIT,
        "n_subsiding_early": len(sink),
        "mean_change_mm": float(np.mean(dif)) if dif else None,
        "median_change_mm": float(np.median(dif)) if dif else None,
        "n_slower": int(sum(1 for d in dif if d > 0)),
        "mean_early_mm": float(np.mean([r["early"]["rate_mm"] for r in sink])) if sink else None,
        "mean_late_mm": float(np.mean([r["late"]["rate_mm"] for r in sink])) if sink else None,
        "stations": [{"station": r["station"], "early": round(r["early"]["rate_mm"], 2),
                      "late": round(r["late"]["rate_mm"], 2),
                      "d": round(r["late"]["rate_mm"] - r["early"]["rate_mm"], 2),
                      "se": round(max(r["early"]["se_mm"], r["late"]["se_mm"]), 2)}
                     for r in sorted(sink, key=lambda r: r["early"]["rate_mm"])],
    }

    # test B — reference frame: InSAR against GNSS refitted on the InSAR window
    gate = {"CJKT": -6.4, "CTGR": -2.9, "CBTU": -0.5}
    cmp_ = []
    for r in rows:
        if r["insar_mm"] is None or r["insar_cells"] == 0 or not r["late"]:
            continue
        cmp_.append({
            "station": r["station"], "lon": r["lon"], "lat": r["lat"],
            "insar": r["insar_mm"], "gnss_full": round(r["full"]["rate_mm"], 2) if r["full"] else None,
            "gnss_late": round(r["late"]["rate_mm"], 2), "gnss_late_se": round(r["late"]["se_mm"], 2),
            "late_span": [round(r["late"]["t0"], 2), round(r["late"]["t1"], 2)],
            "published": gate.get(r["station"]),
            "d_full": round(r["insar_mm"] - r["full"]["rate_mm"], 2) if r["full"] else None,
            "d_late": round(r["insar_mm"] - r["late"]["rate_mm"], 2),
            "gnss_late_post": round(r["late_post"]["rate_mm"], 2) if r["late_post"] else None,
            "d_late_post": round(r["insar_mm"] - r["late_post"]["rate_mm"], 2) if r["late_post"] else None,
        })
    # the Jakarta-metro stations (inside the case's own working bbox) vs the rest of north Java
    bw, bs, be, bn = config.BBOX
    def metro(r):
        return r["lon"] is not None and bw <= r["lon"] <= be and bs <= r["lat"] <= bn
    west = [r for r in rows if metro(r) and r["early"] and r["late"]]
    east = [r for r in rows if not metro(r) and r["lon"] is not None and r["early"] and r["late"]]
    testA["west"] = {"n": len(west), "n_slower": sum(1 for r in west if r["late"]["rate_mm"] > r["early"]["rate_mm"]),
                     "mean_change_mm": float(np.mean([r["late"]["rate_mm"] - r["early"]["rate_mm"] for r in west]))}
    testA["east"] = {"n": len(east), "n_slower": sum(1 for r in east if r["late"]["rate_mm"] > r["early"]["rate_mm"]),
                     "mean_change_mm": float(np.mean([r["late"]["rate_mm"] - r["early"]["rate_mm"] for r in east]))}
    testA["all"] = [{"station": r["station"], "lon": r["lon"], "lat": r["lat"],
                     "early": round(r["early"]["rate_mm"], 2), "late": round(r["late"]["rate_mm"], 2),
                     "d": round(r["late"]["rate_mm"] - r["early"]["rate_mm"], 2),
                     "late_post": round(r["late_post"]["rate_mm"], 2) if r["late_post"] else None,
                     "west": metro(r)} for r in sorted(west + east, key=lambda r: r["late"]["rate_mm"])]
    # halving time implied by the Jakarta station, for the test-G scenario
    cj = next((r for r in rows if r["station"] == "CJKT"), None)
    if cj and cj["early"] and cj["late"]:
        dt = 0.5 * ((cj["late"]["t0"] + cj["late"]["t1"]) - (cj["early"]["t0"] + cj["early"]["t1"]))
        ratio = cj["late"]["rate_mm"] / cj["early"]["rate_mm"]
        testA["cjkt_halving_years"] = round(float(dt * np.log(0.5) / np.log(ratio)), 2)
        testA["cjkt_gap_years"] = round(float(dt), 2)
        testA["cjkt_ratio"] = round(float(ratio), 3)

    dl = [c["d_late"] for c in cmp_]
    dlp = [c["d_late_post"] for c in cmp_ if c["d_late_post"] is not None]
    df = [c["d_full"] for c in cmp_ if c["d_full"] is not None]
    testB = {
        "n": len(cmp_), "stations": sorted(cmp_, key=lambda c: c["insar"]),
        "mean_d_full": float(np.mean(df)) if df else None, "sd_d_full": float(np.std(df, ddof=1)) if len(df) > 1 else None,
        "mean_d_late": float(np.mean(dl)) if dl else None, "sd_d_late": float(np.std(dl, ddof=1)) if len(dl) > 1 else None,
        "max_abs_d_late": float(np.max(np.abs(dl))) if dl else None,
        "mean_d_late_post": float(np.mean(dlp)) if dlp else None,
        "sd_d_late_post": float(np.std(dlp, ddof=1)) if len(dlp) > 1 else None,
    }
    # decimated series for the figures (weekly samples keep the file small and the shape intact)
    series = {}
    for st in ("CJKT", "CTGR", "CBTU"):
        p = RAW / "gnss" / f"{st}_clean_Noff1site.rneu"
        if not p.exists():
            continue
        t, v, _ = read_rneu(p)
        k = max(1, t.size // 620)
        series[st] = {"t": [round(float(x), 3) for x in t[::k]],
                      "v_mm": [round(float(x) * 1000, 1) for x in v[::k]]}
    log(f"A: {testA['n_subsiding_early']} subsiding stations, mean late−early {testA['mean_change_mm']:+.2f} mm/yr, "
        f"{testA['n_slower']} slower; Jakarta metro {testA['west']['n_slower']}/{testA['west']['n']} slower "
        f"({testA['west']['mean_change_mm']:+.2f}), rest of north Java {testA['east']['n_slower']}/{testA['east']['n']} "
        f"({testA['east']['mean_change_mm']:+.2f})")
    log(f"B: {testB['n']} stations in the field; InSAR − GNSS(full) {testB['mean_d_full']:+.2f} ± {testB['sd_d_full']:.2f}; "
        f"InSAR − GNSS(2017+) {testB['mean_d_late']:+.2f} ± {testB['sd_d_late']:.2f}; "
        f"InSAR − GNSS(2018+) {testB['mean_d_late_post']:+.2f} ± {testB['sd_d_late_post']:.2f} mm/yr")
    return {"A": testA, "B": testB, "stations": rows, "series": series}


# ──────────────────────────────────────────── C–G: the grid recomputes
def grid_tests(halving_years: float | None) -> dict:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.merge import merge
    from rasterio.warp import reproject

    with rasterio.open(DERIVED / "analysis_grid_100m.tif") as src:
        pop = src.read(1).astype("float64")
        ground = src.read(5).astype("float64")
        vel = src.read(6).astype("float64")
        kidx = src.read(7).astype("int64")
        T, shape = src.transform, (src.height, src.width)

    # rebuild the two alternative ground estimators from the same DSM
    tiles = [rasterio.open(RAW / "dem" / u.rsplit("/", 1)[-1]) for u in config.GLO30_TILES]
    w, s, e, n = config.BBOX
    dem, dem_T = merge(tiles, bounds=(w - 0.01, s - 0.01, e + 0.01, n + 0.01), nodata=np.nan)
    for t in tiles:
        t.close()
    dem = dem[0].astype("float32")

    def warp_to(arr, resampling):
        dst = np.full(shape, np.nan, dtype="float32")
        reproject(arr, dst, src_transform=dem_T, src_crs="EPSG:4326", src_nodata=np.nan,
                  dst_transform=T, dst_crs="EPSG:4326", dst_nodata=np.nan, resampling=resampling)
        return dst.astype("float64")

    g_mean = warp_to(dem, Resampling.average)
    g_q1 = warp_to(dem, Resampling.q1)          # 25th percentile of the source pixels
    g_min = np.where(np.isfinite(ground), ground, np.nan)   # what the pipeline used (clamped)
    for a in (g_mean, g_q1):
        a[~np.isfinite(g_min)] = np.nan
    g_mean = np.maximum(g_mean, -5.0)
    g_q1 = np.maximum(g_q1, -5.0)

    inside = (kidx > 0)
    v0 = np.where(np.isfinite(vel), vel, 0.0)
    valid = inside & np.isfinite(g_min)

    def count(gr, thr, year, shift=0.0):
        m = valid & np.isfinite(gr) & ((gr + v0 * (year - BASE_YEAR) / 100.0) < (thr + shift))
        return float(pop[m].sum())

    # --- C: the estimator ---
    est = {}
    for key, gr in (("min", g_min), ("q25", g_q1), ("mean", g_mean)):
        est[key] = {t: {y: round(count(gr, thr, y)) for y in (2025, 2050)}
                    for t, thr in (("1m", 1.0), ("0m", 0.0))}
    # how many people stand on cells whose ground proxy is exactly the DSM's zero floor
    at_zero = valid & (np.abs(g_min) < 1e-6)
    testC = {"estimators": est,
             "pop_at_exact_zero": round(float(pop[at_zero].sum())),
             "cells_at_exact_zero": int(at_zero.sum()),
             "min_vs_mean_1m_2025": round(est["min"]["1m"][2025] / max(est["mean"]["1m"][2025], 1), 2),
             "min_vs_mean_0m_2025": round(est["min"]["0m"][2025] / max(est["mean"]["0m"][2025], 1), 2),
             "median_min_minus_mean_m": round(float(np.nanmedian((g_min - g_mean)[valid])), 2)}

    # --- D: the DEM epoch the pipeline declines to correct ---
    lag = BASE_YEAR - DEM_EPOCH
    g_epoch = g_min + v0 * lag / 100.0
    testD = {"dem_epoch": DEM_EPOCH, "lag_years": lag,
             "median_drop_m": round(float(np.nanmedian((g_min - g_epoch)[valid])), 3),
             "max_drop_m": round(float(np.nanmax((g_min - g_epoch)[valid])), 3),
             "counts": {t: {y: round(count(g_epoch, thr, y)) for y in (2025, 2050)}
                        for t, thr in (("1m", 1.0), ("0m", 0.0))},
             "published": {t: {y: round(count(g_min, thr, y)) for y in (2025, 2050)}
                           for t, thr in (("1m", 1.0), ("0m", 0.0))}}

    # --- E: the datum is held fixed; sweep it ---
    shifts = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30]
    testE = {"shifts_m": shifts,
             "pop_0m_2050": [round(count(g_min, 0.0, 2050, sh)) for sh in shifts],
             "pop_1m_2050": [round(count(g_min, 1.0, 2050, sh)) for sh in shifts],
             "pop_0m_2025": [round(count(g_min, 0.0, 2025, sh)) for sh in shifts]}

    # --- F: does the radar change the ranking at all? ---
    nk = int(kidx.max())
    def per_kel(gr, thr, year, v):
        m = valid & np.isfinite(gr) & ((gr + v * (year - BASE_YEAR) / 100.0) < thr)
        return np.bincount(kidx[m], weights=pop[m], minlength=nk + 1)[1:]
    with_v = per_kel(g_min, 1.0, 2050, v0)
    no_v = per_kel(g_min, 1.0, 2050, np.zeros_like(v0))
    today = per_kel(g_min, 1.0, 2025, v0)

    def spearman(a, b):
        ra, rb = _rank(a), _rank(b)
        ra = ra - ra.mean(); rb = rb - rb.mean()
        return float((ra * rb).sum() / np.sqrt((ra ** 2).sum() * (rb ** 2).sum()))

    def top(a, k=20):
        return set(np.argsort(-a)[:k].tolist())
    testF = {
        "rho_2050_vs_no_subsidence": round(spearman(with_v, no_v), 4),
        "rho_2050_vs_today": round(spearman(with_v, today), 4),
        "top20_shared_no_subsidence": len(top(with_v) & top(no_v)),
        "top20_shared_today": len(top(with_v) & top(today)),
        "city_2050_with": round(float(with_v.sum())), "city_2050_without": round(float(no_v.sum())),
        "city_2025": round(float(today.sum())),
    }
    # --- G: the rate is not a constant; run the clock with it decaying ---
    testG = None
    if halving_years and halving_years > 0:
        HT = float(halving_years)
        def eff(y):                        # ∫ v·2^(−t/HT) dt from 2025 to y, in "equivalent years"
            return HT / np.log(2) * (1 - 2 ** (-(y - BASE_YEAR) / HT))
        def count_decay(thr, year):
            m = valid & ((g_min + v0 * eff(year) / 100.0) < thr)
            return float(pop[m].sum())
        testG = {"halving_years": round(HT, 2),
                 "effective_years_2050": round(float(eff(2050)), 2), "linear_years_2050": 25,
                 "pop_1m_2050": round(count_decay(1.0, 2050)), "pop_0m_2050": round(count_decay(0.0, 2050)),
                 "series_1m": [round(count_decay(1.0, y)) for y in range(2025, 2051, 5)],
                 "series_0m": [round(count_decay(0.0, y)) for y in range(2025, 2051, 5)],
                 "linear_1m": [round(count(g_min, 1.0, y)) for y in range(2025, 2051, 5)],
                 "linear_0m": [round(count(g_min, 0.0, y)) for y in range(2025, 2051, 5)],
                 "years": list(range(2025, 2051, 5))}
        log(f"G: halving time {HT:.1f} yr → {testG['effective_years_2050']:.1f} equivalent years of subsidence by 2050 "
            f"instead of 25; below +1 m 2050 {testG['pop_1m_2050']:,} vs {est['min']['1m'][2050]:,}")

    # --- the exposure curve: population standing below every height, both estimators ---
    heights = [round(h, 2) for h in np.arange(-1.0, 6.01, 0.25)]
    curve = {"heights": heights,
             "min_2025": [round(count(g_min, h, 2025)) for h in heights],
             "mean_2025": [round(count(g_mean, h, 2025)) for h in heights],
             "min_2050": [round(count(g_min, h, 2050)) for h in heights]}

    # --- H: reproduce the depositors' own per-district table from our regrid ---
    import pandas as pd
    kel = pd.read_parquet(DERIVED / "kelurahan_exposure.parquet", columns=["kota", "island"])
    kel = kel.reset_index(drop=True)
    kota_of = {i + 1: (None if kel.island[i] else kel.kota[i]) for i in range(len(kel))}
    testH = []
    for kota in sorted({k for k in kota_of.values() if k}):
        want = np.array([i for i, k in kota_of.items() if k == kota])
        m = np.isin(kidx, want) & np.isfinite(vel)
        v = vel[m]
        if not v.size:
            continue
        testH.append({"kota": kota, "n_cells": int(v.size), "mean": round(float(v.mean()), 3),
                      "median": round(float(np.median(v)), 3), "sd": round(float(v.std(ddof=1)), 3),
                      "min": round(float(v.min()), 3),
                      "share_neg": round(float((v < 0).mean()) * 100, 1),
                      "share_lt1": round(float((v < -1).mean()) * 100, 1)})

    # --- area, not just people, below the datum (the "40% of Jakarta" claim) ---
    latm = T.f + T.e * (shape[0] / 2)     # mean latitude of the grid
    cell_km2 = (abs(T.a) * 111.320 * np.cos(np.radians(latm))) * (abs(T.e) * 110.574)
    def area_below(gr, thr):
        m = valid & np.isfinite(gr) & (gr < thr)
        return round(float(m.sum()) * cell_km2, 1)
    total_km2 = round(float(valid.sum()) * cell_km2, 1)
    area_at_zero = round(float(at_zero.sum()) * cell_km2, 1)
    area = {"total_km2": total_km2,
            "min_0m": area_below(g_min, 0.0), "min_1m": area_below(g_min, 1.0),
            "mean_0m": area_below(g_mean, 0.0), "mean_1m": area_below(g_mean, 1.0)}
    area["at_zero_km2"] = area_at_zero
    area["min_0m_incl_zero"] = round(area["min_0m"] + area_at_zero, 1)
    area["min_0m_incl_zero_share"] = round((area["min_0m"] + area_at_zero) / total_km2, 4)
    area["min_0m_share"] = round(area["min_0m"] / total_km2, 4)
    area["mean_0m_share"] = round(area["mean_0m"] / total_km2, 4)
    log(f"H: {len(testH)} kota reproduced; area below MSL {area['min_0m_share']:.1%} (cell min) "
        f"vs {area['mean_0m_share']:.1%} (cell mean) of {total_km2:,.0f} km²")

    log(f"C: below +1 m 2025 — min {est['min']['1m'][2025]:,} · q25 {est['q25']['1m'][2025]:,} · mean {est['mean']['1m'][2025]:,}; "
        f"{testC['pop_at_exact_zero']:,} people on cells pinned to exactly 0.00 m")
    log(f"D: epoch correction moves below +1 m 2025 {testD['published']['1m'][2025]:,} → {testD['counts']['1m'][2025]:,}")
    log(f"E: +10 cm of datum moves below-MSL 2050 {testE['pop_0m_2050'][0]:,} → {testE['pop_0m_2050'][2]:,}")
    log(f"F: rho(2050 ranking, same ranking with the radar switched off) = {testF['rho_2050_vs_no_subsidence']}")
    return {"C": testC, "D": testD, "E": testE, "F": testF, "G": testG, "H": testH,
            "area": area, "curve": curve}


def _rank(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    r = np.empty(len(a), dtype="float64")
    r[order] = np.arange(1, len(a) + 1)
    return r


def main() -> int:
    out = {"generated": __import__("datetime").date.today().isoformat(),
           "note": "Six pre-specified tests run over data already on disk. See the module docstring."}
    g = gnss_tests()
    out.update(g)
    out.update(grid_tests(g["A"].get("cjkt_halving_years")))
    (DERIVED / "review.json").write_text(json.dumps(_clean(out), indent=1, allow_nan=False))
    log(f"wrote {DERIVED / 'review.json'}")
    return 0


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


if __name__ == "__main__":
    sys.exit(main())
