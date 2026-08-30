"""Stage 5 · cams — a real chemistry-transport model to stand beside, and the plume heights.

Three ADS products, three different jobs, one shared token.  ADS needed a one-time browser
acceptance of its terms and its data-protection statement; that click has been made and every
request below was verified accepted live on 2026-08-30.  The pipeline still detects a policy 403
and degrades to PENDING rather than dying, because the acceptance is per account and this case
has to build on a fresh one.

WHY EACH ONE IS HERE

``cams-global-fire-emissions-gfas`` — THE UPGRADE, and the one that changes a number.
    GFAS converts FRP to emissions and, critically, publishes ``injection_height``, plume top and
    plume bottom per fire.  Plume height is what decides whether smoke stays in the boundary
    layer and poisons Palangkaraya or reaches the 850 hPa flow and crosses the Strait — and in
    the first draft of this case it was the crudest guess in the whole pipeline, a fixed
    three-level ``PLUME_RISE`` tuple.  Replacing a guess with a published product is the single
    largest accuracy gain available here.  GFAS ends 2025-12-03, so the operational tail falls
    back to ``config.PLUME_RISE`` and transport.py reports the fallback share per run.

``cams-global-reanalysis-eac4`` — TIER-3 SURROGATE TRUTH, labelled as a model everywhere.
    Pekanbaru, Palangkaraya and Pontianak — the cities this case is about — have never had an
    open PM2.5 sensor.  EAC4 stands in, and is called a reanalysis on every row it appears in.
    It is also the ONLY reference that reaches the 2015 anchor, because NEA's history begins
    2016-03.  EAC4 ends 2025-12-31.

``cams-global-atmospheric-composition-forecasts`` — THE CTM TO STAND BESIDE.
    A real chemistry-transport forecast covering both anchor years, 0-120 h lead.  The trajectory
    model is drawn NEXT TO it, never in place of it.  ** Aligned by ISSUE time, not valid time **:
    comparing our day-3 forecast against CAMS's analysis would flatter us by exactly the amount
    that makes the comparison worthless.  Pulled for the anchor seasons and the recent tail only
    — the divergence chart needs episodes, not fifteen years of quiet.

OUTPUT
------
``data/gfas.parquet``                  cell, day, injection_height_m, plume_top_m,
                                       plume_bottom_m, frp_w_m2, pm25_flux
``data/cams_eac4_receptors.parquet``   receptor, day, pm25 (tier 3 surrogate, MODEL)
``data/cams_eac4_grid.parquet``        cell, day, pm25 — the field behind chapter 04
``data/cams_forecast.parquet``         cell, issue, lead_h, pm25 — the CTM benchmark
``data/cams_meta.json``                coverage, end dates, and the "this is a model" flags
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import config
import util
from util import log

NC_DIR = config.RAW / "cams_nc"
PARTS = config.DATA_DIR / "cams_parts"
GFAS_OUT = config.DATA_DIR / "gfas.parquet"
EAC4_REC_OUT = config.DATA_DIR / "cams_eac4_receptors.parquet"
EAC4_GRID_OUT = config.DATA_DIR / "cams_eac4_grid.parquet"
FC_OUT = config.DATA_DIR / "cams_forecast.parquet"
META_OUT = config.DATA_DIR / "cams_meta.json"

EAC4_LAST_YEAR = 2025          # config.CAMS_EAC4 coverage ends 2025-12-31
GFAS_LAST_YEAR = 2025          # ends 2025-12-03
FC_YEARS = (2015, 2019, 2025)  # the anchors plus a recent season, for the divergence chart
EAC4_TIMES = ["00:00", "06:00", "12:00", "18:00"]


def _open(paths):
    """Open one CDS payload as a single lazy Dataset with (t, lat, lon) coordinates."""
    import xarray as xr
    dss = []
    for p in paths:
        ds = xr.open_dataset(p, engine="netcdf4", decode_timedelta=False)
        if "expver" in ds.dims:
            ds = ds.isel(expver=0, drop=True)
        for junk in ("number", "expver", "surface"):
            ds = ds.drop_vars(junk, errors="ignore")
        ren = {}
        for a, b in (("valid_time", "t"), ("time", "t"), ("latitude", "lat"),
                     ("longitude", "lon")):
            if a in ds.coords and b not in ds.coords:
                ren[a] = b
        dss.append(ds.rename(ren))
    return xr.merge(dss, join="outer", compat="override")


def _to_cells(ds, agg: str = "mean"):
    """Any CAMS grid -> the 0.25 deg model grid, one row per cell-day.

    CAMS products are coarser than the model grid (EAC4 ~0.75 deg, forecasts ~0.4 deg, GFAS
    0.1 deg), so this is a NEAREST-NEIGHBOUR broadcast upward for the coarse ones and a genuine
    average for GFAS.  That asymmetry is real and is stated on the methodology page: a 0.75 deg
    reanalysis cell covers about nine model cells, and drawing it at model resolution would imply
    detail the product does not have.
    """
    import numpy as np
    import pandas as pd
    df = ds.to_dataframe().reset_index()
    df = df[df["lat"].between(config.AOI[1], config.AOI[3])
            & df["lon"].between(config.AOI[0], config.AOI[2])]
    df["day"] = pd.to_datetime(df["t"]).dt.normalize()
    df["clat"], df["clon"] = util.snap_cell(df["lat"], df["lon"])
    df["cell"] = util.cell_key(df["clat"], df["clon"])
    value_cols = [c for c in df.columns
                  if c not in ("t", "lat", "lon", "day", "clat", "clon", "cell")
                  and df[c].dtype.kind == "f"]
    g = df.groupby(["cell", "day"], as_index=False)[value_cols].agg(agg)
    for c in value_cols:
        g[c] = g[c].astype("float32")
    return g


# ── GFAS: the injection heights ───────────────────────────────────────────────────────
def gfas_specs() -> list[dict]:
    """One request per QUARTER.  Measured 2026-08-30 against the live API: seven GFAS variables
    for a whole year, or even for six months, are refused with "cost limits exceeded"; three
    months are accepted.  GFAS is 0.1 deg, which is why it is the tightest of the three products
    even though it is daily and single-level.
    """
    out = []
    for y in range(int(config.START[:4]), GFAS_LAST_YEAR + 1):
        for q, (m0, m1) in enumerate(((1, 3), (4, 6), (7, 9), (10, 12)), start=1):
            last = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m1 - 1]
            if m1 == 2 and y % 4 == 0:
                last = 29
            end = f"{y}-{m1:02d}-{last:02d}"
            if y == GFAS_LAST_YEAR and end > config.CAMS_GFAS_ENDS:
                end = config.CAMS_GFAS_ENDS
                if f"{y}-{m0:02d}-01" > end:
                    continue
            out.append(dict(key=f"gfas:{y}q{q}", dataset=config.CAMS_GFAS,
                            dest=PARTS / f"gfas_{y}q{q}.parquet",
                            request=dict(variable=config.CAMS_GFAS_VARS,
                                         date=f"{y}-{m0:02d}-01/{end}",
                                         area=list(config.ERA5_AREA),
                                         data_format="netcdf")))
    # anchors first, then most-recent-first: 56 quarterly requests on a serial queue may not all
    # land in one session, and the two years the case is about are worth more than 2013 Q2
    out.sort(key=lambda s: (0 if int(s["key"][5:9]) in config.ANCHOR_YEARS else 1,
                            -int(s["key"][5:9]), s["key"]))
    return out


def reduce_gfas(spec, paths) -> None:
    """Heights on the model grid, with the short names spelled out.

    ``injh`` is the mean altitude of maximum injection — the release height this case wanted GFAS
    for — and it is averaged over the coarse GFAS cells falling in each 0.25 deg model cell.  The
    assertion below is the point of this function: if a future request silently drops ``injh``
    again, the stage fails loudly instead of writing a table of plume tops and letting
    transport.py quietly fall back to a parameterisation.
    """
    import pandas as pd
    ds = _open(paths)
    g = _to_cells(ds, "mean")
    ds.close()
    g = g.rename(columns=config.CAMS_GFAS_SHORT)
    util.require("injection_height_m" in g.columns,
                 f"GFAS returned {sorted(g.columns)} with no injection height — the request was "
                 f"accepted and truncated (see config.CAMS_GFAS_VARS)")
    g.to_parquet(spec["dest"], index=False, compression="zstd")


# ── EAC4: tier-3 surrogate truth ──────────────────────────────────────────────────────
def eac4_specs() -> list[dict]:
    out = []
    for y in range(int(config.START[:4]), EAC4_LAST_YEAR + 1):
        out.append(dict(key=f"eac4:{y}", dataset=config.CAMS_EAC4,
                        dest=PARTS / f"eac4_{y}.parquet",
                        request=dict(variable=["particulate_matter_2.5um",
                                               "total_aerosol_optical_depth_550nm"],
                                     date=f"{y}-01-01/{y}-12-31", time=EAC4_TIMES,
                                     area=list(config.ERA5_AREA), data_format="netcdf")))
    return out


def reduce_eac4(spec, paths) -> None:
    ds = _open(paths)
    g = _to_cells(ds, "mean")
    ds.close()
    g.to_parquet(spec["dest"], index=False, compression="zstd")


# ── forecasts: the CTM to stand beside ────────────────────────────────────────────────
def fc_specs() -> list[dict]:
    out = []
    for y in FC_YEARS:
        out.append(dict(key=f"camsfc:{y}", dataset=config.CAMS_FORECAST,
                        dest=PARTS / f"camsfc_{y}.parquet",
                        request=dict(variable=["particulate_matter_2.5um"],
                                     date=f"{y}-06-01/{y}-11-30", time=["00:00"],
                                     leadtime_hour=["0", "24", "48", "72", "96"],
                                     type=["forecast"], area=list(config.ERA5_AREA),
                                     data_format="netcdf")))
    return out


def reduce_fc(spec, paths) -> None:
    """Keep the LEAD axis.  Collapsing it would destroy the only honest way to compare."""
    import numpy as np
    import pandas as pd
    ds = _open(paths)
    df = ds.to_dataframe().reset_index()
    ds.close()
    lead_col = next((c for c in ("forecast_period", "step", "leadtime_hour", "prediction_timedelta")
                     if c in df.columns), None)
    df["clat"], df["clon"] = util.snap_cell(df["lat"], df["lon"])
    df["cell"] = util.cell_key(df["clat"], df["clon"])
    ref = next((c for c in ("forecast_reference_time", "time") if c in df.columns), "t")
    df["issue"] = pd.to_datetime(df[ref]).dt.normalize()
    if lead_col is not None:
        v = df[lead_col]
        df["lead_h"] = (v.dt.total_seconds() // 3600).astype("int32") \
            if hasattr(v, "dt") and v.dtype.kind == "m" else v.astype("int32")
    else:
        df["lead_h"] = ((pd.to_datetime(df["t"]) - df["issue"]).dt.total_seconds()
                        // 3600).astype("int32")
    val = next(c for c in df.columns if c.startswith("pm2p5") or c == "pm2p5")
    g = (df.groupby(["cell", "issue", "lead_h"], as_index=False)[val].mean()
           .rename(columns={val: "pm25"}))
    g["pm25"] = g["pm25"].astype("float32")
    g.to_parquet(spec["dest"], index=False, compression="zstd")


def receptor_series(grid):
    """Nearest model cell to each tier-3 receptor.  Labelled a MODEL, on every row."""
    import numpy as np
    import pandas as pd
    rows = []
    for name, m in config.RECEPTORS.items():
        if m.get("source") != "cams_eac4":
            continue
        clat, clon = util.snap_cell(m["lat"], m["lon"])
        cell = int(util.cell_key(clat, clon))
        s = grid[grid["cell"] == cell].copy()
        if s.empty:
            log(f"  eac4 receptor {name}: no cell at {clat},{clon}")
            continue
        s["receptor"] = name
        s["tier"] = 3
        s["source"] = "cams_eac4"
        # EAC4 pm2p5 is kg/m3; the page speaks ug/m3 like every air-quality number anyone quotes
        s["pm25"] = (s["pm2p5"] * 1e9).astype("float32") if "pm2p5" in s.columns \
            else s.filter(like="pm2").iloc[:, 0].astype("float32") * 1e9
        s["pm25_max"] = s["pm25"]
        s["n_obs"] = 24
        rows.append(s[["receptor", "day", "pm25", "pm25_max", "n_obs", "tier", "source"]])
        log(f"  eac4 receptor {name}: {len(s):,} days, peak {s['pm25'].max():.0f} ug/m3 "
            f"(MODEL, not an observation)")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def main() -> None:
    import pandas as pd
    PARTS.mkdir(parents=True, exist_ok=True)
    util.require(bool(config.CDS_API_KEY), "CDS_API_KEY missing from repo-root .env")
    meta = {"store": "ADS", "policy_urls": config.POLICY_URLS["ads"]}

    log("cams: GFAS injection heights -> EAC4 surrogate -> forecasts (the CTM to stand beside)")
    meta["gfas"] = util.run_store_jobs("ads", gfas_specs(), reduce_gfas, NC_DIR,
                                       max_inflight=2, max_minutes=200)
    meta["eac4"] = util.run_store_jobs("ads", eac4_specs(), reduce_eac4, NC_DIR,
                                       max_inflight=2, max_minutes=200)
    meta["forecast"] = util.run_store_jobs("ads", fc_specs(), reduce_fc, NC_DIR,
                                           max_inflight=1, max_minutes=120)

    for glob, out, label in ((f"gfas_*.parquet", GFAS_OUT, "gfas"),
                             (f"eac4_*.parquet", EAC4_GRID_OUT, "eac4"),
                             (f"camsfc_*.parquet", FC_OUT, "cams_forecast")):
        parts = sorted(PARTS.glob(glob))
        if not parts:
            log(f"  {label}: no parts yet")
            continue
        df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
        df.to_parquet(out, index=False, compression="zstd")
        log(f"  {label}: {len(df):,} rows -> {out.name} ({out.stat().st_size/1e6:.1f} MB)")

    if EAC4_GRID_OUT.exists():
        rec = receptor_series(pd.read_parquet(EAC4_GRID_OUT))
        if len(rec):
            rec.to_parquet(EAC4_REC_OUT, index=False, compression="zstd")
            meta["receptors"] = {"rows": int(len(rec)),
                                 "receptors": sorted(rec["receptor"].unique().tolist()),
                                 "kind": "MODEL (CAMS EAC4 reanalysis), never an observation"}

    meta["coverage_notes"] = {
        "gfas_ends": config.CAMS_GFAS_ENDS,
        "eac4_ends": "2025-12-31",
        "forecast_years_pulled": list(FC_YEARS),
        "alignment": "the forecast comparison is aligned by ISSUE time, not valid time",
        "resolution": "CAMS is coarser than the 0.25 deg model grid (EAC4 ~0.75 deg, forecasts "
                      "~0.4 deg); values are broadcast up and the page says so rather than "
                      "implying detail the product does not have",
    }
    META_OUT.write_text(json.dumps(meta, indent=1))
    log(f"cams: gfas {meta['gfas']['status']} · eac4 {meta['eac4']['status']} · "
        f"forecast {meta['forecast']['status']}")
    util.manifest_put("cams", **{k: meta[k]["status"] for k in
                                 ("gfas", "eac4", "forecast")})


if __name__ == "__main__":
    main()
