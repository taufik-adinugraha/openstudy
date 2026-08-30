"""Stage 3 · indices — the season state, the drought, and the baseline we have to beat.

FOUR THINGS, IN DESCENDING ORDER OF HOW MUCH THEY MATTER

1. SPI FROM CHIRPS MONTHLY, COMPUTED HERE RATHER THAN TAKEN READY-MADE.
   A standardised index is only meaningful against a long base period, and the ERA5 pull in this
   case starts in 2012 — fourteen years, which is not a climatology.  CHIRPS v3 monthly global
   GeoTIFFs run from 1981, are 0.05 deg, and the server honours HTTP Range, so ``/vsicurl`` reads
   only the ~300 rows covering 9S-6N.  That buys a 45-year base period for about 50 MB of
   traffic, and it is the difference between "SPI-3" and "a 14-year z-score wearing SPI's name".
   SPI is self-computed rather than downloaded so that the identical code can be run on the
   CHIRPS-GEFS *forecast* and stay consistent with the operational path; the ready-made CDS
   product validates ours rather than replacing it.

2. THE CEMS CANADIAN FIRE WEATHER INDEX — THE EXTERNAL BASELINE, AND THE POINT OF GATE G-J2.
   ``cems-fire-historical-v1`` on EWDS (not CDS, which 404s) publishes the full Canadian set plus
   ``keetch_byram_drought_index``, so KBDI is not hand-rolled.  ~29 MB/year over the AOI.
   ** THIS IS THE ONLY THING IN THIS STAGE THAT CAN BE BLOCKED. **  EWDS needs a one-time browser
   acceptance of "Terms of use of the CEMS Early Warning Data Store (rev. 11)".  A 403 there is a
   policy click, not a dead key: the same token returns HTTP 200 on the EWDS account endpoint.
   The stage submits, detects the policy 403, records the exact URL the account owner must visit,
   marks G-J2's FWI half PENDING, and carries on — because a case that cannot build until someone
   clicks a checkbox is not a resumable case.

3. ENSO / IOD / SOI.  ONI monthly and weekly Nino3.4 from CPC (``wksst9120.for``, NOT
   ``wksst8110.for``, which still resolves but froze in January 2021 when the base period
   changed — exactly the kind of silent staleness that poisons a feature).  Daily SOI from Long
   Paddock, CC BY 4.0, because every BoM SOI URL is now 404 and daily beats monthly anyway.
   HadISST DMI from NOAA PSL.
   ** THE DMI IS A HISTORICAL FEATURE ONLY. **  The series runs ~3 months behind and is stamped
   "Preliminary", so there is no operational IOD index — and 2019, the strongest positive IOD on
   record and the reason that fire season happened, is unreadable without it.  The feature is
   used in training and the gap is stated on the page rather than papered over.

4. CHIRPS-GEFS, the days-ahead rainfall forecast, for the operational refresh only.
   Issued same day, leads 0-15, public domain, no auth, 16 files per issue.  There is no open
   reforecast archive covering 2012-2024, so this CANNOT train the historical forecast path —
   see risk.py, which defines that path by information set instead, and says so.

OUTPUT
------
``data/enso.parquet``            day, oni, nino34, nino34_anom, soi, dmi (each with its own lag)
``data/chirps_monthly.parquet``  cell, month, rain_mm  (1981 -> now, the SPI base period)
``data/spi.parquet``             cell, month, spi1, spi3, spi6
``data/fwi.parquet``             cell, day, fwi, ffmc, dmc, dc, isi, bui, dsr, kbdi   (or absent)
``data/indices_meta.json``       coverage, lags, licences, and any PENDING reason
"""

from __future__ import annotations

import io
import json
import os
import time
from datetime import date, datetime, timedelta

import config
import util
from util import log

ENSO_OUT = config.DATA_DIR / "enso.parquet"
CHIRPS_OUT = config.DATA_DIR / "chirps_monthly.parquet"
SPI_OUT = config.DATA_DIR / "spi.parquet"
FWI_OUT = config.DATA_DIR / "fwi.parquet"
GEFS_OUT = config.DATA_DIR / "chirps_gefs.parquet"
META_OUT = config.DATA_DIR / "indices_meta.json"
FWI_PARTS = config.DATA_DIR / "fwi_parts"

CHIRPS_MONTHLY = ("https://data.chc.ucsb.edu/products/CHIRPS/v3.0/monthly/global/tifs/"
                  "chirps-v3.0.{year}.{month:02d}.tif")
CHIRPS_BASE_START = 1981
# CORRECTION 2026-08-30 (build): the spec's `prelim/global_daily/fixed/` is a CHIRPS-2.0 path and
# 404s under v3.0.  The v3 daily tree is v3.0/daily/{final,prelim}/{sat,rnl}/, and only `sat` has
# a prelim stream (rnl is ERA5-downscaled, so it inherits ERA5's lag and is final-only).
CHIRPS_DAILY_FINAL_P25 = ("https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/final/sat/p25/"
                          "{year}/chirps-v3.0.sat.{year}.{month:02d}.{day:02d}.tif")
CHIRPS_DAILY_PRELIM = ("https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/prelim/sat/{year}/"
                       "chirps-v3.0.prelim.{year}.{month:02d}.{day:02d}.tif")
# directory = ISSUE date, filename = VALID date.  Lead 0 has them equal.
CHIRPS_GEFS_V3 = ("https://data.chc.ucsb.edu/products/CHIRPS-GEFS/v3/daily/global/{iy}/{im:02d}/"
                  "{id:02d}/c3g_{vy}.{vm:02d}.{vd:02d}.tif")


def _gdal_env() -> None:
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.cog")
    os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
    os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
    os.environ.setdefault("VSI_CACHE", "TRUE")


# ── 3 · ocean state ───────────────────────────────────────────────────────────────────
def _get(url: str) -> str:
    import requests
    r = requests.get(url, timeout=180, headers=util.browser_ua())
    r.raise_for_status()
    return r.text


def enso_frame():
    """One daily table.  Every series keeps its own real latency; none is extrapolated."""
    import numpy as np
    import pandas as pd

    idx = pd.date_range(config.START, date.today(), freq="D")
    out = pd.DataFrame({"day": idx})

    # ONI — 3-month running Nino3.4 anomaly, monthly, the standard ENSO state variable
    txt = _get(config.ENSO_URLS["oni"])
    rows = [ln.split() for ln in txt.strip().splitlines()[1:] if len(ln.split()) == 4]
    seas = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]
    oni = pd.DataFrame(rows, columns=["seas", "yr", "total", "anom"])
    oni["month"] = oni["seas"].map({s: i + 1 for i, s in enumerate(seas)})
    oni["ts"] = pd.to_datetime(dict(year=oni["yr"].astype(int), month=oni["month"], day=1))
    oni["oni"] = oni["anom"].astype(float)
    out = pd.merge_asof(out, oni[["ts", "oni"]].sort_values("ts"),
                        left_on="day", right_on="ts", direction="backward").drop(columns=["ts"])

    # weekly Nino3.4 — wksst9120, NOT wksst8110 (frozen at the 2021 base-period change)
    try:
        txt = _get(config.ENSO_URLS["nino34_weekly"])
        rec = []
        for ln in txt.splitlines():
            s = ln.strip()
            if len(s) < 40 or not s[:2].isdigit():
                continue
            try:
                d = datetime.strptime(s[:9], "%d%b%Y")
            except ValueError:
                continue
            nums = s[9:].replace("-", " -").split()
            if len(nums) >= 8:
                rec.append({"ts": d, "nino34": float(nums[4]), "nino34_anom": float(nums[5])})
        w = pd.DataFrame(rec).sort_values("ts")
        out = pd.merge_asof(out, w, left_on="day", right_on="ts",
                            direction="backward").drop(columns=["ts"])
    except Exception as exc:                                # noqa: BLE001
        log(f"  nino34 weekly unavailable: {type(exc).__name__} {exc}")
        out["nino34"] = np.nan
        out["nino34_anom"] = np.nan

    # daily SOI — Long Paddock, CC BY 4.0 (every BoM URL is dead)
    try:
        txt = _get(config.ENSO_URLS["soi_daily"])
        soi = pd.read_csv(io.StringIO(txt), sep=r"\s+")
        soi.columns = [c.lower() for c in soi.columns]
        soi["ts"] = (pd.to_datetime(soi["year"].astype(int).astype(str) + "-01-01")
                     + pd.to_timedelta(soi["day"].astype(int) - 1, unit="D"))
        out = pd.merge_asof(out, soi[["ts", "soi"]].sort_values("ts"),
                            left_on="day", right_on="ts",
                            direction="backward").drop(columns=["ts"])
    except Exception as exc:                                # noqa: BLE001
        log(f"  daily SOI unavailable: {type(exc).__name__} {exc}")
        out["soi"] = np.nan

    # DMI — HadISST, monthly, ~3 months behind, stamped "Preliminary".  HISTORICAL ONLY.
    try:
        txt = _get(config.ENSO_URLS["dmi"])
        rec = []
        for ln in txt.splitlines():
            p = ln.split()
            if len(p) == 13 and p[0].isdigit():
                for m, v in enumerate(p[1:], start=1):
                    fv = float(v)
                    if fv > -9:
                        rec.append({"ts": pd.Timestamp(int(p[0]), m, 1), "dmi": fv})
        d = pd.DataFrame(rec).sort_values("ts")
        out = pd.merge_asof(out, d, left_on="day", right_on="ts",
                            direction="backward").drop(columns=["ts"])
        last = d["ts"].max()
        log(f"  dmi: ends {last.date()} — {(pd.Timestamp.today() - last).days} days behind "
            f"today.  NOT operational; historical feature only")
    except Exception as exc:                                # noqa: BLE001
        log(f"  DMI unavailable: {type(exc).__name__} {exc}")
        out["dmi"] = np.nan

    for c in ("oni", "nino34", "nino34_anom", "soi", "dmi"):
        if c in out.columns:
            out[c] = out[c].astype("float32")
    log(f"  enso: {len(out):,} days; coverage "
        + ", ".join(f"{c} {out[c].notna().mean():.0%}"
                    for c in ("oni", "nino34_anom", "soi", "dmi") if c in out.columns))
    return out


# ── 1 · CHIRPS monthly + SPI ──────────────────────────────────────────────────────────
def chirps_month(year: int, month: int):
    """One global monthly GeoTIFF, window-read to the AOI over HTTP Range.

    Reads ~300 of 2400 rows.  The file is striped at one row per strip, so a latitude-band
    window is almost as cheap as a tiled read would be — about 1 MB against 30 MB.
    """
    import numpy as np
    import pandas as pd
    import rasterio
    from rasterio.windows import from_bounds
    url = "/vsicurl/" + CHIRPS_MONTHLY.format(year=year, month=month)
    w, s, e, n = config.AOI
    with rasterio.open(url) as src:
        win = from_bounds(w, s, e, n, src.transform)
        arr = src.read(1, window=win).astype("float32")
        tr = src.window_transform(win)
        nod = src.nodata
    arr = np.where((arr == nod) | (arr < 0), np.nan, arr)
    rows, cols = np.mgrid[0:arr.shape[0], 0:arr.shape[1]]
    lon = tr.c + (cols + 0.5) * tr.a
    lat = tr.f + (rows + 0.5) * tr.e
    clat, clon = util.snap_cell(lat, lon)
    d = pd.DataFrame({"cell": util.cell_key(clat, clon).ravel(), "v": arr.ravel()})
    d = d[np.isfinite(d["v"])]
    g = d.groupby("cell", as_index=False)["v"].mean().rename(columns={"v": "rain_mm"})
    g["month"] = pd.Timestamp(year, month, 1)
    return g


def pull_chirps_monthly():
    import pandas as pd
    _gdal_env()
    parts_dir = config.DATA_DIR / "chirps_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    todo = [(y, m) for y in range(CHIRPS_BASE_START, today.year + 1)
            for m in range(1, 13) if (y, m) <= (today.year, today.month)]
    got = 0
    for y, m in todo:
        p = parts_dir / f"{y}-{m:02d}.parquet"
        if p.exists():
            continue
        if not util.guard_disk(0.5):
            break
        try:
            chirps_month(y, m).to_parquet(p, index=False)
            got += 1
            if got % 60 == 0:
                log(f"  chirps monthly: {got} new parts, at {y}-{m:02d}")
        except Exception as exc:                            # noqa: BLE001 — resumable
            if y >= today.year - 1:
                log(f"  chirps {y}-{m:02d}: {type(exc).__name__} (not yet published)")
            else:
                log(f"  chirps {y}-{m:02d}: {type(exc).__name__} {exc}")
    parts = sorted(parts_dir.glob("*.parquet"))
    util.require(bool(parts), "no CHIRPS monthly parts — the SPI base period cannot be built")
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    df.to_parquet(CHIRPS_OUT, index=False, compression="zstd")
    log(f"  chirps monthly: {len(df):,} cell-months, {df['month'].min().date()} -> "
        f"{df['month'].max().date()} ({df['month'].dt.year.nunique()}-year base period)")
    return df


def spi_from_monthly(monthly):
    """SPI-1/3/6 by the standard method: gamma fit per cell per calendar month, then Phi^-1.

    Zero-inflation is handled the standard way — the probability of a dry month enters as a
    point mass and the gamma is fitted only to the positive values — because an equatorial peat
    cell has a genuine zero-rain month often enough that ignoring it biases every SPI in the
    dry season, which is the only season this case cares about.
    """
    import numpy as np
    import pandas as pd
    from scipy import stats

    m = monthly.sort_values(["cell", "month"]).reset_index(drop=True)
    out = m[["cell", "month"]].copy()
    for scale in config.SPI_SCALES:
        acc = (m.groupby("cell")["rain_mm"]
                .rolling(scale, min_periods=scale).sum().reset_index(level=0, drop=True))
        m[f"acc{scale}"] = acc
        z = np.full(len(m), np.nan)
        cal = m["month"].dt.month.to_numpy()
        cells = m["cell"].to_numpy()
        a = m[f"acc{scale}"].to_numpy()
        for cm in range(1, 13):
            sel = cal == cm
            if not sel.any():
                continue
            sub_cells, sub_a = cells[sel], a[sel]
            order = np.argsort(sub_cells, kind="stable")
            sc, sa = sub_cells[order], sub_a[order]
            bounds = np.flatnonzero(np.r_[True, sc[1:] != sc[:-1], True])
            zs = np.full(len(sa), np.nan)
            for i0, i1 in zip(bounds[:-1], bounds[1:]):
                v = sa[i0:i1]
                fin = np.isfinite(v)
                pos = v[fin & (v > 0)]
                if fin.sum() < 10 or len(pos) < 5:
                    continue
                q0 = float((fin.sum() - len(pos)) / fin.sum())     # P(zero)
                try:
                    shp, loc, scl = stats.gamma.fit(pos, floc=0)
                except Exception:                                   # noqa: BLE001
                    continue
                cdf = np.where(v > 0, q0 + (1 - q0) * stats.gamma.cdf(v, shp, loc=loc, scale=scl),
                               q0 / 2.0)
                zs[i0:i1] = stats.norm.ppf(np.clip(cdf, 1e-6, 1 - 1e-6))
            back = np.empty_like(zs)
            back[order] = zs
            z[sel] = back
        out[f"spi{scale}"] = np.clip(z, -4, 4).astype("float32")
    out.to_parquet(SPI_OUT, index=False, compression="zstd")
    cov = {f"spi{s}": float(out[f"spi{s}"].notna().mean()) for s in config.SPI_SCALES}
    log(f"  spi: {len(out):,} cell-months; coverage {cov}")
    return out


# ── 4 · CHIRPS-GEFS forecast (operational refresh only) ───────────────────────────────
def pull_gefs(issue: date | None = None):
    """The most recent issue's 16 lead days, on the model grid.

    Used by ``make refresh`` to drive the live risk panel.  It CANNOT train the historical
    forecast path: there is no open GEFS reforecast archive covering 2012-2024, and pretending
    otherwise would be the exact "train on one product, serve on another" skew the spec warns
    about.  risk.py therefore defines the forecast path by INFORMATION SET, not by product, and
    this layer is used and labelled as what it is — today's rainfall forecast.
    """
    import numpy as np
    import pandas as pd
    import rasterio
    from rasterio.windows import from_bounds
    _gdal_env()
    issue = issue or date.today()
    w, s, e, n = config.AOI
    rows = []
    for lead in config.CHIRPS_GEFS_LEADS:
        valid = issue + timedelta(days=lead)
        url = "/vsicurl/" + CHIRPS_GEFS_V3.format(iy=issue.year, im=issue.month, id=issue.day,
                                                  vy=valid.year, vm=valid.month, vd=valid.day)
        try:
            with rasterio.open(url) as src:
                win = from_bounds(w, s, e, n, src.transform)
                arr = src.read(1, window=win).astype("float32")
                tr = src.window_transform(win)
                nod = src.nodata
        except Exception as exc:                            # noqa: BLE001
            log(f"  gefs {issue} lead {lead}: {type(exc).__name__}")
            continue
        arr = np.where((arr == nod) | (arr < 0), np.nan, arr)
        rr, cc = np.mgrid[0:arr.shape[0], 0:arr.shape[1]]
        lon = tr.c + (cc + 0.5) * tr.a
        lat = tr.f + (rr + 0.5) * tr.e
        clat, clon = util.snap_cell(lat, lon)
        d = pd.DataFrame({"cell": util.cell_key(clat, clon).ravel(), "v": arr.ravel()})
        d = d[np.isfinite(d["v"])].groupby("cell", as_index=False)["v"].mean()
        d = d.rename(columns={"v": "rain_fc_mm"})
        d["issue"], d["lead"], d["valid"] = pd.Timestamp(issue), lead, pd.Timestamp(valid)
        rows.append(d)
    if not rows:
        log(f"  gefs: no leads available for issue {issue}")
        return None
    df = pd.concat(rows, ignore_index=True)
    df.to_parquet(GEFS_OUT, index=False, compression="zstd")
    log(f"  gefs: issue {issue}, {df['lead'].nunique()} leads, {len(df):,} cell-leads")
    return df


# ── 2 · the CEMS fire indices — the baseline, and the one blockable thing here ────────
# MEASURED 2026-08-30 against a real EWDS payload: the NetCDF short names are not the ones the
# request uses, and they are not the obvious abbreviations either.  Recorded verbatim so nobody
# has to rediscover that the duff moisture code arrives as `dufmcode`.
FWI_SHORT = {"fwinx": "fwi", "fwi": "fwi",
             "ffmcode": "ffmc", "ffmc": "ffmc",
             "dufmcode": "dmc", "dmc": "dmc",
             "drtcode": "dc", "dc": "dc",
             "infsinx": "isi", "isi": "isi",
             "fbupinx": "bui", "bui": "bui",
             "dsrate": "dsr", "dsrat": "dsr", "dsr": "dsr",
             "kbdi": "kbdi"}


def reduce_fwi(spec, paths) -> None:
    """One year of the Canadian set on the 0.25 deg model grid, one row per cell-day."""
    import numpy as np
    import pandas as pd
    import xarray as xr
    dss = []
    for p in paths:
        ds = xr.open_dataset(p, engine="netcdf4", decode_timedelta=False)
        ren = {}
        for a, b in (("valid_time", "t"), ("time", "t"), ("latitude", "lat"),
                     ("longitude", "lon")):
            if a in ds.coords and b not in ds.coords:
                ren[a] = b
        dss.append(ds.rename(ren).drop_vars(["number", "expver", "surface"], errors="ignore"))
    ds = xr.merge(dss, join="outer", compat="override")
    out = []
    t = pd.to_datetime(ds["t"].values)
    for m in sorted(set(t.month)):
        sub = ds.isel(t=np.flatnonzero(t.month == m))
        df = sub.to_dataframe().reset_index()
        df = df[df["lat"].between(config.AOI[1], config.AOI[3])
                & df["lon"].between(config.AOI[0], config.AOI[2])]
        df["day"] = pd.to_datetime(df["t"]).dt.normalize()
        df["clat"], df["clon"] = util.snap_cell(df["lat"], df["lon"])
        df["cell"] = util.cell_key(df["clat"], df["clon"])
        vals = [c for c in df.columns if df[c].dtype.kind == "f"
                and c not in ("lat", "lon", "clat", "clon")]
        g = df.groupby(["cell", "day"], as_index=False)[vals].mean()
        out.append(g)
        del df, sub
    ds.close()
    g = pd.concat(out, ignore_index=True)
    g = g.rename(columns={k: v for k, v in FWI_SHORT.items() if k in g.columns})
    for c in g.columns:
        if g[c].dtype == "float64":
            g[c] = g[c].astype("float32")
    g.to_parquet(spec["dest"], index=False, compression="zstd")


def pull_fwi(max_minutes: float = 200.0) -> dict:
    """The CEMS Canadian FWI + KBDI set from EWDS — the external baseline gate G-J2 scores against.

    ``cems-fire-historical-v1`` is on EWDS, not CDS (which 404s), and EWDS needed a one-time
    browser acceptance of its terms.  That click has been made and a real submission was verified
    accepted on 2026-08-30.  The policy branch is kept anyway and names the exact URL, because
    the acceptance is per account and this pipeline has to build on a fresh one.

    A caveat worth carrying: EWDS's ``/profiles/v1/account/licences`` lists only ``cc-by`` and
    still accepts these submissions, so that listing is NOT authoritative.  The only reliable
    test is a real submit — which is what this function does.
    """
    import pandas as pd
    FWI_PARTS.mkdir(parents=True, exist_ok=True)
    years = list(range(int(config.START[:4]), date.today().year + 1))
    specs = [dict(key=f"fwi:{y}", dataset=config.FWI_DATASET,
                  dest=FWI_PARTS / f"{y}.parquet",
                  request=dict(product_type=["reanalysis"], variable=config.FWI_VARS,
                               dataset_type="consolidated_dataset", system_version=["4_1"],
                               year=[str(y)], month=[f"{m:02d}" for m in range(1, 13)],
                               day=[f"{d:02d}" for d in range(1, 32)],
                               grid="0.25/0.25", area=list(config.ERA5_AREA),
                               data_format="netcdf"))
             for y in years]
    status = util.run_store_jobs("ewds", specs, reduce_fwi, config.RAW / "fwi_nc",
                                 max_inflight=1, max_minutes=max_minutes)
    parts = sorted(FWI_PARTS.glob("*.parquet"))
    if not parts:
        return {**status, "effect": "G-J2's climatology half is scored; its FWI half is PENDING."}
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    df.to_parquet(FWI_OUT, index=False, compression="zstd")
    log(f"  fwi: {len(df):,} cell-days, {df['day'].min().date()} -> {df['day'].max().date()}, "
        f"columns {[c for c in df.columns if c not in ('cell', 'day')]}")
    return {**status, "rows": int(len(df)), "years": len(parts),
            "licence": config.FWI_LICENCE, "doi": config.FWI_DOI}


def main() -> None:
    import argparse
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--fwi-only", action="store_true",
                    help="drain the EWDS queue only; the rest of this stage is already on disk")
    ap.add_argument("--max-minutes", type=float, default=200.0)
    ap.add_argument("--consolidate-only", action="store_true",
                    help="with --fwi-only: fold the parts already on disk into fwi.parquet and "
                         "touch no queue, so a half-drained EWDS backfill is still scorable")
    args = ap.parse_args()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.fwi_only:
        if args.consolidate_only:
            parts = sorted(FWI_PARTS.glob("*.parquet"))
            util.require(bool(parts), "no CEMS parts on disk")
            df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
            df.to_parquet(FWI_OUT, index=False, compression="zstd")
            log(f"fwi: {len(parts)} years -> {len(df):,} cell-days, "
                f"{df['day'].min().date()} -> {df['day'].max().date()}")
            return
        st = pull_fwi(args.max_minutes)
        log(f"indices --fwi-only: {st['status']}")
        if META_OUT.exists():
            m = json.loads(META_OUT.read_text())
            m["fwi"] = st
            META_OUT.write_text(json.dumps(m, indent=1))
        return

    log("indices: ENSO/IOD/SOI -> CHIRPS monthly -> SPI -> CEMS FWI (EWDS) -> CHIRPS-GEFS")

    enso = enso_frame()
    enso.to_parquet(ENSO_OUT, index=False, compression="zstd")

    monthly = pull_chirps_monthly()
    spi = spi_from_monthly(monthly)

    fwi_status = pull_fwi(args.max_minutes)
    gefs = pull_gefs()

    META_OUT.write_text(json.dumps({
        "enso": {"days": int(len(enso)),
                 "dmi_operational": config.DMI_OPERATIONAL,
                 "dmi_note": "HadISST DMI runs ~3 months behind and is stamped Preliminary — "
                             "a historical feature only.  2019 is unreadable without it.",
                 "soi_source": "Long Paddock (State of Queensland), CC BY 4.0 — every BoM SOI "
                               "URL is 404",
                 "nino34_note": "wksst9120.for; wksst8110.for still resolves but froze "
                                "2021-01-27 at a base-period change"},
        "chirps": {"product": "CHIRPS v3.0 monthly global",
                   "base_period_years": int(monthly["month"].dt.year.nunique()),
                   "first": str(monthly["month"].min().date()),
                   "last": str(monthly["month"].max().date()),
                   "licence": config.CHIRPS_LICENCE,
                   "path_correction": "the spec's prelim/global_daily/fixed/ is a CHIRPS-2.0 "
                                      "path and 404s under v3.0; v3 is daily/{final,prelim}/sat/",
                   "caveat": "CHIRPS daily is a disaggregated pentad, not an independent daily "
                             "observation; and a %CCD bug zeroed precipitation over Indonesia "
                             "where IR data was missing, fixed in 2015"},
        "spi": {"scales": list(config.SPI_SCALES),
                "method": "gamma fit per cell per calendar month with a zero point mass, "
                          "then the inverse normal CDF",
                "rows": int(len(spi))},
        "fwi": fwi_status,
        "gefs": {"status": "ok" if gefs is not None else "unavailable",
                 "role": "operational refresh only — no open reforecast archive exists, so it "
                         "cannot train the historical forecast path"},
    }, indent=1))
    log(f"indices: done.  FWI {fwi_status['status']}")
    util.manifest_put("indices", enso_days=int(len(enso)),
                      chirps_months=int(monthly["month"].nunique()),
                      fwi=fwi_status["status"])


if __name__ == "__main__":
    main()
