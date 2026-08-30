"""Stage 10 · export — view-models for the dashboard, small enough to be interactive.

BUDGET
------
First paint under ``config.WEB_BUDGET_MB``.  Anything per-cell or per-date is a separate,
lazily-fetched file keyed by the URL state.  Aggregates and pre-simplified geometry only.

THE SIGNATURE INTERACTION HAS A DATA CONTRACT
---------------------------------------------
"The radar probe" needs, for any probed location, the real backscatter curve with its detected
events, in under 300 ms and with no server.  So each kabupaten ships one file containing

  * a coarse FIELD raster (cycles per year per cell, byte-quantised) which is what the map draws
    and what the pointer hit-tests against — no geometry, no GPU, just an array;
  * the probe LATTICE (``config.PROBE_STEP_M``): for each lattice point the smoothed VV and VH
    curves on the shared 6-day grid, byte-quantised to quarter-decibels, plus the genuine
    acquisitions as points, plus the detected events as indices into the shared date array.

Byte-quantised arrays go over as base64, not as JSON number lists: the same 400,000 samples are
about 530 kB base64 against roughly 2.5 MB as text, and the browser decodes them in a few
milliseconds.  That is the difference between the case's best moment being instant and laggy.

NaN DISCIPLINE
--------------
``json.dumps`` writes bare ``NaN``, which is not valid JSON: some browsers parse it, others
reject it, and the bug then appears on one machine only.  Everything goes through ``sanitise``
and the exporter asserts the output round-trips through a strict parser.
"""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path

import config
import util
from util import log

WEB = config.WEB_DATA
SRC = config.WEB_SRC_DATA
NODATA = -32768
STAGES = ("flood", "veg", "peak", "ripe", "harvest")


def sanitise(obj):
    """Recursively replace NaN/Inf with None so the output is strict-parseable JSON."""
    import numpy as np

    if isinstance(obj, dict):
        return {str(k): sanitise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitise(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else round(v, 6)
    if isinstance(obj, np.ndarray):
        return sanitise(obj.tolist())
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def write_json(path: Path, obj, label: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(sanitise(obj), separators=(",", ":"), allow_nan=False, default=str)
    json.loads(txt)                       # strict round-trip, asserted not assumed
    path.write_text(txt)
    log(f"export: {label or path.name} {len(txt) / 1024:.0f} kB")
    return len(txt)


def b64(arr) -> str:
    import numpy as np

    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


def _q8(db, lo=-30.0, hi=2.0):
    """dB -> uint8 in quarter-decibel steps; 255 is 'no data'."""
    import numpy as np

    x = np.where(np.isfinite(db), (db - lo) / (hi - lo) * 254.0, 255.0)
    return np.clip(np.round(x), 0, 255).astype("uint8")


def write_probe_lattice(kab, cells, ph, bs_meta):
    """One file per kabupaten: the field raster, the lattice curves, the detected events."""
    import numpy as np
    import pandas as pd

    z = np.load(config.DATA_DIR / "bs" / f"{kab}.npz")
    ck = cells[cells["kabupaten"] == kab].reset_index(drop=True)
    steps = z["steps"].astype("int32")
    probe = z["probe"].astype("int64")
    t0 = pd.Timestamp(config.SAR_START)

    # ── the field raster the map draws: cycles per year per cell, on a 2x coarser grid ──
    s = config.CELL_M
    x = ck["x"].to_numpy(); y = ck["y"].to_numpy()
    x0, y1 = x.min() - s / 2, y.max() + s / 2
    W = int(round((x.max() + s / 2 - x0) / s)); H = int(round((y1 - (y.min() - s / 2)) / s))
    col = np.round((x - x0 - s / 2) / s).astype("int32")
    row = np.round((y1 - y - s / 2) / s).astype("int32")
    years = max(1, len(config.SEASONS))
    per_cell = np.zeros(len(ck), "float32")
    pk = ph[ph["kabupaten"] == kab]
    if len(pk):
        cnt = pk.groupby("cell_i").size()
        per_cell[cnt.index.to_numpy()] = cnt.to_numpy() / years
    F = 2                                  # 200 m field: half the bytes, still crisp at 1440px
    fw, fh = (W + F - 1) // F, (H + F - 1) // F
    field = np.full((fh, fw), 255, "uint8")
    acc = np.zeros((fh, fw), "float32"); nn = np.zeros((fh, fw), "int32")
    np.add.at(acc, (row // F, col // F), per_cell)
    np.add.at(nn, (row // F, col // F), 1)
    m = nn > 0
    field[m] = np.clip(np.round(acc[m] / nn[m] * 50.0), 0, 254).astype("uint8")

    # ── the lattice curves ──
    def deq(a):
        b = a.astype("float32"); b[a == NODATA] = np.nan
        return b / 100.0
    # Thin the stored lattice to the payload budget, keeping it a LATTICE.
    # Stage 5 selected probe points with ``round(x / CELL_M) % step == 0``; numpy rounds half to
    # even and every cell centre sits on a .5, so that test kept four times as many points as
    # intended (2,098 instead of ~520 for Indramayu) and the file came out at 2.6 MB against a
    # 2 MB budget.  Rather than re-derive — which would intersect two different integer grids and
    # leave almost nothing — the stored set is thinned by dropping alternate lattice ROWS and
    # then alternate COLUMNS until it fits, which preserves regular spacing in both axes.
    pc, pr = col[probe], row[probe]
    lat = np.ones(len(probe), "bool")
    target = 1100
    axis = 0
    while lat.sum() > target:
        vals = np.unique((pr if axis == 0 else pc)[lat])
        if vals.size < 4:
            break
        drop = set(vals[1::2].tolist())
        lat &= ~np.isin((pr if axis == 0 else pc), list(drop))
        axis ^= 1
    probe = probe[lat]
    vv = deq(z["vv"][probe]); vh = deq(z["vh"][probe])
    keep = np.isfinite(vv).sum(axis=1) > len(steps) * 0.5
    probe, vv, vh = probe[keep], vv[keep], vh[keep]
    praw = z["probe_raw"][:, :, lat][:, :, keep]          # (n_obs, 2, n_probe)
    obs_days = z["obs_days"].astype("int32")

    ev_by_cell: dict[int, list] = {}
    if len(pk):
        idx = {int(c): i for i, c in enumerate(probe)}
        sub = pk[pk["cell_i"].isin(idx)]
        for r in sub.itertuples():
            ev_by_cell.setdefault(idx[int(r.cell_i)], []).append([
                int((r.transplant - t0).days), int((r.heading - t0).days),
                int((r.harvest - t0).days), round(float(r.confidence), 2)])

    doc = {
        "kabupaten": kab,
        "province": str(ck["province"].iloc[0]),
        "epsg": int(ck["epsg"].iloc[0]),
        "bounds_ll": [float(ck["lon"].min()), float(ck["lat"].min()),
                      float(ck["lon"].max()), float(ck["lat"].max())],
        "field": {"w": fw, "h": fh, "step_m": s * F, "x0": float(x0), "y1": float(y1),
                  "scale": 50.0, "nodata": 255, "data": b64(field)},
        "t0": config.SAR_START,
        "steps": steps.tolist(),
        "obs_days": obs_days.tolist(),
        "quant": {"lo": -30.0, "hi": 2.0, "nodata": 255},
        "n_probe": int(len(probe)),
        "probe_xy": b64(np.c_[col[probe], row[probe]].astype("int16")),
        "probe_ll": b64(np.c_[ck["lon"].to_numpy()[probe],
                              ck["lat"].to_numpy()[probe]].astype("float32")),
        "vv": b64(_q8(vv)), "vh": b64(_q8(vh)),
        "obs_vv": b64(_q8(deq(praw[:, 0, :]).T)), "obs_vh": b64(_q8(deq(praw[:, 1, :]).T)),
        "events": {str(k): v for k, v in ev_by_cell.items()},
        "orbits": bs_meta.get(kab, {}).get("orbits", {}),
        "vintage": {"first": bs_meta.get(kab, {}).get("first"),
                    "last": bs_meta.get(kab, {}).get("last"),
                    "acquisitions": bs_meta.get(kab, {}).get("n_acquisitions")},
    }
    return write_json(WEB / "probe" / f"{kab}.json", doc, f"probe/{kab}.json")


def write_wave(cells, ph, adm):
    """Kecamatan geometry plus a per-week phenological-stage mix — the harvest-wave scrub."""
    import numpy as np
    import pandas as pd
    from shapely import wkb as _wkb

    t0 = pd.Timestamp(config.SAR_START)
    t1 = pd.Timestamp(config.SAR_END)
    weeks = pd.date_range(t0, t1, freq="7D")
    kecs = adm[adm["level"] == "ADM3"].reset_index(drop=True)
    if not len(kecs):
        kecs = adm[(adm["level"] == "ADM2") & adm["deep"]].reset_index(drop=True)
    ids = kecs["gb_id"].astype(str).tolist()
    pos = {k: i for i, k in enumerate(ids)}

    total_ha = (cells.groupby("kec_id")["ha"].sum())
    frames = np.zeros((len(weeks), len(ids), len(STAGES)), "float32")
    pk = ph.copy()
    pk["t"] = (pk["transplant"] - t0).dt.days
    pk["h"] = (pk["heading"] - t0).dt.days
    pk["x"] = (pk["harvest"] - t0).dt.days
    wk = np.array([(w - t0).days for w in weeks])
    for r in pk.itertuples():
        j = pos.get(str(r.kec_id))
        if j is None:
            continue
        # flood: transplant +-6 d | veg: transplant..heading-10 | peak: heading +-10
        # ripe: heading+10..harvest-6 | harvest: harvest +-6
        segs = ((0, r.t - 6, r.t + 6), (1, r.t + 6, r.h - 10), (2, r.h - 10, r.h + 10),
                (3, r.h + 10, r.x - 6), (4, r.x - 6, r.x + 6))
        for si, a, b in segs:
            if b <= a:
                continue
            lo = np.searchsorted(wk, a); hi = np.searchsorted(wk, b)
            if hi > lo:
                frames[lo:hi, j, si] += r.ha
    denom = np.array([total_ha.get(k, 1.0) for k in ids], "float32")[None, :, None]
    q = np.clip(np.round(frames / np.maximum(denom, 1) * 255.0), 0, 255).astype("uint8")

    geo = []
    for r in kecs.itertuples():
        g = _wkb.loads(r.wkb).simplify(0.0015, preserve_topology=True)
        polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
        rings = []
        for p in polys:
            xs, ys = p.exterior.coords.xy
            rings.append([[round(float(a), 4), round(float(b), 4)] for a, b in zip(xs, ys)])
        geo.append({"id": str(r.gb_id), "name": r.name, "kab": str(r.bps), "rings": rings})

    kabnames = {str(v["bps"]): k for k, v in config.SCOPE_DEEP.items()}
    doc = {
        "t0": config.SAR_START,
        "weeks": [str(w.date()) for w in weeks],
        "stages": list(STAGES),
        "units": geo,
        "kab_names": kabnames,
        "frames": b64(q),
        "shape": [len(weeks), len(ids), len(STAGES)],
        "note": ("share of each kecamatan's detected paddy in each phenological stage, by week; "
                 "the ramp is cyclic because a crop calendar is a loop"),
    }
    return write_json(WEB / "wave.json", doc)


def write_area(panel, year_panel, ksa_nat, prices, break_):
    import pandas as pd

    by_kab = {}
    for kab, g in panel.groupby("kabupaten", observed=True):
        g = g.sort_values(["year", "month"])
        by_kab[kab] = {
            "t": [f"{int(r.year)}-{int(r.month):02d}" for r in g.itertuples()],
            "ours": [round(float(v), 1) for v in g["harvested_ha"]],
            "lo": [round(float(v), 1) for v in g["harvested_ha_lo"]],
            "hi": [round(float(v), 1) for v in g["harvested_ha_hi"]],
            "planted": [round(float(v), 1) for v in g["planted_ha"]],
            "cal": [None if pd.isna(v) else round(float(v), 1) for v in g["calibrated_ha"]],
            "pred": [None if pd.isna(v) else round(float(v), 1)
                     for v in g.get("harvested_ha_pred", pd.Series([None] * len(g)))],
            "ksa": [None if pd.isna(v) else round(float(v), 1) for v in g["ksa_ha"]],
            "usable": [bool(v) for v in g["benchmark_usable"]],
        }
    doc = {"by_kabupaten": by_kab,
           "year_panel": year_panel.to_dict("records"),
           "ksa_national": ksa_nat,
           "break": break_,
           "prices": prices,
           "holdout_season": config.HOLDOUT_SEASON,
           "seasons": list(config.SEASONS)}
    return write_json(WEB / "area.json", doc)


def main() -> None:
    import numpy as np
    import pandas as pd

    util.guard_disk()
    D = config.DATA_DIR
    WEB.mkdir(parents=True, exist_ok=True)
    SRC.mkdir(parents=True, exist_ok=True)
    cells = pd.read_parquet(D / "cells.parquet")
    ph = pd.read_parquet(D / "phenology.parquet")
    adm = pd.read_parquet(D / "adm.parquet")
    panel = pd.read_parquet(D / "model.parquet")
    stats = json.loads(config.STATS_JSON.read_text())
    bs_meta = json.loads((D / "backscatter_meta.json").read_text())
    year_panel = pd.DataFrame(stats["year_panel"])
    total = 0

    for kab in config.SCOPE_DEEP:
        if (D / "bs" / f"{kab}.npz").exists():
            write_probe_lattice(kab, cells, ph, bs_meta)
    total += write_wave(cells, ph, adm)

    checks = json.loads((D / "bps_checks.json").read_text())
    ksa_nat = checks.get("national_annual_ha_summed_from_provinces", {})
    prices = None
    if (D / "bps_prices.parquet").exists():
        p = pd.read_parquet(D / "bps_prices.parquet")
        p = p[p["turtahun"].between(1, 12)] if "turtahun" in p else p
        prices = {"t": [f"{int(r.year)}-{int(r.turtahun):02d}" for r in p.itertuples()],
                  "v": [float(r.value) for r in p.itertuples()],
                  "unit": "Rp/kg wholesale", "var": 295}
    total += write_area(panel, year_panel, ksa_nat,
                        prices, {**config.BPS_BREAK, **checks.get("ksa_break", {})})

    cal = {}
    if (D / "onset.parquet").exists():
        o = pd.read_parquet(D / "onset.parquet")
        cal["onset"] = o.assign(onset=o["onset"].astype(str)).to_dict("records")
    if (D / "enso.parquet").exists():
        e = pd.read_parquet(D / "enso.parquet")
        e = e[e["year"] >= 2018]
        cal["enso"] = e.to_dict("records")
    plant = (ph.assign(month=ph["transplant"].dt.month, season=ph["plant_season"])
             .groupby(["kabupaten", "season"], observed=True)
             .apply(lambda g: float((g["transplant"] - g["transplant"].min()).dt.days.mean()),
                    include_groups=False).reset_index(name="_x"))
    pk = (ph.groupby(["kabupaten", "plant_season"], observed=True)["transplant"]
          .quantile(0.5).reset_index(name="median_transplant"))
    pk["median_transplant"] = pk["median_transplant"].astype(str)
    cal["median_transplant"] = pk.to_dict("records")
    cal["rule"] = config.ONSET_RULE
    total += write_json(WEB / "calendar.json", cal)

    if (D / "optical_vs_radar.parquet").exists():
        ov = pd.read_parquet(D / "optical_vs_radar.parquet")
        g = ov.groupby("month")[["s2_all", "s2_usable", "s1_acquisitions"]].sum().reset_index()
        total += write_json(WEB / "optical.json", {
            "by_month": g.to_dict("records"),
            "cloud_max_pct": config.S2_CLOUD_MAX,
            "years": [2023, 2024, 2025],
            "source": "Element84 Earth Search STAC, anonymous; collection "
                      + config.S2_COLLECTION,
        })

    diag = json.loads((D / "phenology_diag.json").read_text())
    area_meta = json.loads((D / "area_meta.json").read_text())
    model_meta = json.loads((D / "model_meta.json").read_text())
    mask_meta = json.loads((D / "rice_mask_meta.json").read_text()) \
        if (D / "rice_mask_meta.json").exists() else {"available": False}
    bps_vars = json.loads((D / "bps_vars.json").read_text())

    kab_cards = []
    for kab, meta in config.SCOPE_DEEP.items():
        g = year_panel[year_panel["kabupaten"] == kab]
        s = diag.get(kab, {})
        kab_cards.append({
            "kabupaten": kab, "province": meta["province"], "system": meta["system"],
            "bps": meta["bps"], "note": meta.get("note"),
            "cells": s.get("cells"), "cycles": s.get("cycles"),
            "cycles_per_cell": s.get("cycles_per_cell"),
            "median_cycle_days": s.get("median_cycle_days"),
            "median_vv_min_db": s.get("median_vv_min_db"),
            "median_vh_rise_db": s.get("median_vh_rise_db"),
            "share_cells_with_any_cycle": s.get("share_cells_with_any_cycle"),
            "orbits": bs_meta.get(kab, {}).get("orbits", {}),
            "acquisitions": bs_meta.get(kab, {}).get("n_acquisitions"),
            "years": g.to_dict("records"),
        })

    summary = {
        **{k: stats[k] for k in ("case", "generated_utc", "scope", "gates", "gates_passed",
                                 "gates_total", "detector_thresholds",
                                 "threshold_sensitivity_ha")},
        "kabupaten": kab_cards,
        "backscatter": bs_meta,
        "area_meta": area_meta,
        "model_meta": model_meta,
        "mask": mask_meta,
        "bps": {"vars": bps_vars.get("series", {}), "checks": checks,
                "licence_data": config.BPS_LICENCE_DATA, "licence_api": config.BPS_LICENCE_API},
        "vintages": {
            "sentinel1": {"collection": config.MPC_RTC_COLLECTION,
                          "licence": config.MPC_RTC_LICENCE,
                          "resolution_m": 10, "read_at_m": 10 * config.MPC_OVERVIEW,
                          "window": [config.SAR_START, config.SAR_END],
                          "attribution": config.MPC_ATTRIBUTION},
            "bps": {"released_through": "2026 monthly tables; KSA regime from 2018",
                    "domains": ["0000", "3200", "3300", "3500"]},
            "rice_prior": mask_meta,
            "boundaries": {"adm1": "geoBoundaries gbOpen ODbL 1.0 (2017)",
                           "adm2": "geoBoundaries gbOpen CC BY 3.0 IGO (2020)",
                           "adm3": config.COD_AB_LICENCE},
            "chirps": {"product": "CHIRPS v3.0 global pentads (COG)",
                       "licence": config.CHIRPS_LICENCE},
        },
        "data_path_note": {
            "planned": "ASF OPERA L2 RTC-S1 (30 m) with the repo's EARTHDATA_TOKEN",
            "reality": ("ASF returns 403 EULA Acceptance Failure for that account on every "
                        "object and on s3credentials; approving the ASF application in "
                        "Earthdata Login is an interactive, account-owner action"),
            "shipped": ("Microsoft Planetary Computer sentinel-1-rtc — the same quantity at "
                        "10 m, CC BY 4.0, anonymous, window-read from remote COGs"),
        },
        "licences": [
            config.MPC_ATTRIBUTION,
            "Statistics: Badan Pusat Statistik (BPS) — " + config.BPS_LICENCE_DATA,
            "Rice prior: Open-SEA-Rice-10, Zenodo 10.5281/zenodo.14627003, CC BY 4.0",
            "Boundaries: geoBoundaries gbOpen (ODbL 1.0 / CC BY 3.0 IGO); kecamatan from "
            "HDX COD-AB (" + config.COD_AB_LICENCE + ")",
            "Rainfall: Climate Hazards Center CHIRPS v3.0 — " + config.CHIRPS_LICENCE,
            "ENSO/IOD: NOAA CPC and PSL, US Government public domain",
            "Optical scene counts: Copernicus Sentinel-2 via Element84 Earth Search",
        ],
        "lahan_baku_sawah": (
            "Indonesia's official rice-field map (Lahan Baku Sawah, ATR/BPN Decree "
            "686/SK-PG.03.03/XII/2019) is live and anonymous on BIG's service with 1,242,551 "
            "sawah polygons, but NO LICENCE IS STATED ANYWHERE ON IT and every catalogue record "
            "on the national SDI portal returns a null licence field. It is therefore used as a "
            "view-time reference and validation comparator only — never stored, never joined "
            "into a published artefact, never redistributed. The stored prior is the CC BY 4.0 "
            "Zenodo product instead."),
    }
    txt = json.dumps(sanitise(summary), separators=(",", ":"), allow_nan=False, default=str)
    json.loads(txt)
    (WEB / "summary.json").write_text(txt)
    (SRC / "summary.json").write_text(txt)
    total += len(txt)
    log(f"export: summary.json {len(txt) / 1024:.0f} kB")

    first_paint = len(txt) + (WEB / "area.json").stat().st_size
    log(f"export -> {WEB}; first paint {first_paint / 1e6:.2f} MB "
        f"(budget {config.WEB_BUDGET_MB} MB); "
        f"total {sum(f.stat().st_size for f in WEB.rglob('*.json')) / 1e6:.2f} MB")
    if first_paint > config.WEB_BUDGET_MB * 1e6:
        log("WARNING: first paint over budget")


if __name__ == "__main__":
    main()
