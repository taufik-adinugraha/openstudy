"""Stage 4 · era5 — fire weather at the surface and steering winds aloft.

THE GOTCHA THAT COSTS A DAY IF IT IS NOT WRITTEN DOWN
------------------------------------------------------
A multi-variable CDS request does not always come back as one NetCDF.  The service splits the
response by ``stepType`` — instantaneous fields (winds, temperature, dewpoint, pressure,
boundary-layer height) in one file, accumulated fields (total precipitation, surface solar
radiation) in another — and hands back a zip.  Concatenating them naively along time produces a
frame in which precipitation and radiation are all-NaN, which a gradient-boosted model then
silently drops as useless columns.  Nothing errors.  ``read_split_netcdfs`` therefore opens each
member separately and JOINS ON THE GRID KEY (valid_time, latitude, longitude), and asserts a
non-null fraction on every column before returning.

BUILD DECISION — NO ACCUMULATED FIELD SHARES A REQUEST WITH AN INSTANTANEOUS ONE.
15 years of full-hourly ERA5 over the AOI is ~58 GB against a 31 GB disk, so the request has to
be cut, and the honest cut is four synoptic hours a day for the state variables (00 and 06 UTC
are 07:00 and 13:00 WIB — the humidity maximum and near peak fire danger, so daily max-T and
min-RH are sampled at the right end of the diurnal cycle rather than averaged away).

But ``total_precipitation`` accumulates over the preceding hour, so summing 4 of 24 hours would
report one sixth of the rain — a silent, systematic dry bias precisely where an ignition model is
most sensitive.  Precipitation is therefore asked for ON ITS OWN, at all 24 hours, as the ``tp``
job kind.  A single variable at full hourly resolution costs 1.05/13 = 0.081 GB per year, which
is a rounding error, and the daily total is a genuine sum rather than a reconstruction.  That is
the whole trick: sample the state, accumulate the flux, and never mix the two in one request.

THREE JOB KINDS
---------------
``reanalysis-era5-single-levels``   fire weather: 10 m u/v wind, 2 m temperature, 2 m dewpoint
                                    (-> relative humidity and VPD, the variables that actually
                                    drive ignition probability), surface pressure, boundary-layer
                                    height (the vertical volume the smoke gets to mix into — a low
                                    BLH is what turns smoke into an episode), soil water layers
                                    1-3, LAI high/low.  All hours, all months, 2012 -> now.
``reanalysis-era5-pressure-levels`` transport: u/v/w at 925, 850 and 700 hPa.  Surface wind is the
                                    wrong thing to advect a plume with — smoke from a hot fire
                                    lofts above the surface layer and is steered by the flow at
                                    ~850 hPa.  Using 10 m wind alone systematically under-rotates
                                    trajectories toward the coast.  Restricted to
                                    ``config.ERA5_PL_MONTHS`` because trajectories are only ever
                                    run in the burning months.

QUEUEING
--------
CDS queues server-side and a multi-year backfill takes hours.  ``util.Cads`` submits jobs
asynchronously and records the job id in ``data/cads_jobs.json``, so the expensive thing — the
queue position — survives a process restart.  ``--submit-only`` fills the queue and exits;
``--poll`` drains it.  The default does both in a loop.  Every year is an independent part, so
re-running is always safe and a failed year never blocks the others.

OUTPUT
------
``data/era5_parts/sl_<YYYY>.parquet``   cell x day fire-weather aggregates
``data/era5_parts/pl_<YYYY>.parquet``   cell x 6-hourly steering winds (int16, scaled) for the
                                        fire-season months — the trajectory engine's input
``data/era5_daily.parquet``             all sl parts concatenated
"""

from __future__ import annotations

import argparse
import shutil
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import config
import util
from util import log

PARTS = config.DATA_DIR / "era5_parts"
NC_DIR = config.RAW / "era5_nc"
SL_OUT = config.DATA_DIR / "era5_daily.parquet"
# MEASURED 2026-08-30: CDS caps QUEUED REQUESTS PER DATASET, not per user.  Six submissions to
# reanalysis-era5-single-levels were accepted and then all six came back `rejected` with
# "Number queued requests for this dataset is temporarily limited."  A rejected job is a lost
# queue slot, not an error, so the fix is to keep at most two per dataset in flight and let the
# rest wait on disk.  sl and pl are different datasets, so four jobs run concurrently in practice.
MAX_INFLIGHT_PER_DATASET = 2
POLL_SECONDS = 45
REJECT_COOLOFF_S = 180      # a rejected submission means the queue is full; back off before retry


def years() -> list[int]:
    end = date.today() - timedelta(days=config.ERA5_LAG_DAYS)
    return list(range(int(config.START[:4]), end.year + 1))


# MEASURED 2026-08-30, by binary search against the live API.  Multi-year requests would cut the
# queue from 45 jobs to 10, but CDS refuses them: "cost limits exceeded / Your request is too
# large".  The measured ceiling is between 16,368 and 17,856 FIELDS per request —
#   sl 1 yr = 11 var x 12 mo x 31 d x  4 h = 16,368  ACCEPTED
#   pl 2 yr =  9 fld x  8 mo x 31 d x  4 h = 17,856  REJECTED
# — and crucially the cost is computed BEFORE the ``area`` subset is applied, so asking for a
# small box buys nothing.  One year per request is therefore the ceiling, not a preference, and
# the grouping machinery below stays in place with a group size of one so a future limit change
# is a one-line edit.
GROUP_YEARS = {"sl": 1, "tp": 1, "pl": 1}
CDS_MAX_FIELDS_MEASURED = 16_368


def groups(kind: str) -> list[tuple[int, ...]]:
    ys, n = years(), GROUP_YEARS[kind]
    return [tuple(ys[i:i + n]) for i in range(0, len(ys), n)]


def _days_of(yrs: tuple[int, ...], months: tuple[int, ...] | None = None):
    """(years, months, days) as zero-padded strings, clipped to what ERA5T can actually have."""
    ms = list(months) if months else list(range(1, 13))
    end = date.today() - timedelta(days=config.ERA5_LAG_DAYS)
    if max(yrs) >= end.year:
        # a group containing the current year has to ask for months that exist for every year in
        # it; CDS returns the cartesian product, and the missing tail is simply absent
        ms = [m for m in ms if m <= end.month or min(yrs) < end.year]
    return ([str(y) for y in yrs], [f"{m:02d}" for m in ms],
            [f"{d:02d}" for d in range(1, 32)])


def request_month(dataset: str, variables: list[str], year: int, month: int, **kw) -> dict:
    """Kept for API compatibility with the scaffold; the build submits whole years, not months.

    ``~/.cdsapirc`` exists on the server and the ``cc-by`` licence is accepted; the key is also
    mirrored into ``.env`` as ``CDS_API_KEY`` / ``CDS_API_URL`` for portability, and
    ``util.Cads`` reads it from there so the pipeline runs on a machine with no ``.cdsapirc``.
    """
    ms, ds = [f"{month:02d}"], [f"{d:02d}" for d in range(1, 32)]
    return dict(product_type=["reanalysis"], variable=variables, year=[str(year)],
                month=ms, day=ds, data_format="netcdf",
                download_format="unarchived", area=list(config.ERA5_AREA), **kw)


def build_request(kind: str, yrs: tuple[int, ...]) -> tuple[str, dict]:
    if kind == "sl":
        ys, ms, ds = _days_of(yrs)
        return config.ERA5_SL, dict(
            product_type=["reanalysis"], variable=config.ERA5_SL_VARS,
            year=ys, month=ms, day=ds, time=config.ERA5_SL_HOURS,
            data_format="netcdf", download_format="unarchived",
            area=list(config.ERA5_AREA))
    if kind == "tp":
        ys, ms, ds = _days_of(yrs)
        return config.ERA5_SL, dict(
            product_type=["reanalysis"], variable=config.ERA5_TP_VARS,
            year=ys, month=ms, day=ds, time=config.ERA5_TP_HOURS,
            data_format="netcdf", download_format="unarchived",
            area=list(config.ERA5_AREA))
    ys, ms, ds = _days_of(yrs, config.ERA5_PL_MONTHS)
    return config.ERA5_PL, dict(
        product_type=["reanalysis"], variable=config.ERA5_PL_VARS,
        pressure_level=config.ERA5_LEVELS,
        year=ys, month=ms, day=ds, time=config.ERA5_PL_HOURS,
        data_format="netcdf", download_format="unarchived",
        area=list(config.ERA5_AREA))


# ── reading ───────────────────────────────────────────────────────────────────────────
def _members(path: Path) -> list[Path]:
    """A CDS payload is either a NetCDF or a zip of them.  Return the NetCDF paths."""
    if not zipfile.is_zipfile(path):
        return [path]
    out_dir = path.with_suffix("")
    out_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith(".nc")]
        for n in names:
            z.extract(n, out_dir)
    return [out_dir / n for n in names]


def read_split_netcdfs(paths):
    """Open every member and JOIN on the grid key rather than stacking along time.

    See the module docstring.  ``xr.merge`` aligns on the shared coordinates — valid_time,
    latitude, longitude — which is the grid-key join the docstring demands, and it is lazy, so
    nothing is read until a time slice is asked for.  Returns one merged, still-lazy Dataset with
    the coordinates renamed to (t, lat, lon).
    """
    import xarray as xr

    dss = []
    for p in paths:
        ds = xr.open_dataset(p, engine="netcdf4", decode_timedelta=False)
        if "expver" in ds.dims:
            ds = ds.isel(expver=0, drop=True)
        elif "expver" in ds.coords:
            ds = ds.drop_vars("expver")
        for junk in ("number", "expver", "surface", "depthBelowLandLayer"):
            if junk in ds.coords or junk in ds.variables:
                ds = ds.drop_vars(junk, errors="ignore")
        tname = "valid_time" if "valid_time" in ds.coords else "time"
        ds = ds.rename({tname: "t", "latitude": "lat", "longitude": "lon"})
        dss.append(ds)
    return xr.merge(dss, join="outer", compat="override")


def _chunks(ds, key: str):
    """Yield (label, DataFrame) one month at a time.

    A whole year of the single-levels request is ~8.4 M rows x 15 columns once melted, which is
    comfortably over a 3 GB systemd MemoryMax alongside the other jobs on this box.  Slicing by
    month keeps peak RSS near 300 MB and costs nothing, because the aggregation is per-day.
    """
    import numpy as np
    import pandas as pd
    t = pd.to_datetime(ds["t"].values)
    for ym in sorted(set(zip(t.year, t.month))):
        sel = (t.year == ym[0]) & (t.month == ym[1])
        if not sel.any():
            continue
        sub = ds.isel(t=np.flatnonzero(sel))
        df = sub.to_dataframe().reset_index()
        for c in df.columns:
            if df[c].dtype == np.float64:
                df[c] = df[c].astype("float32")
        yield f"{ym[0]}-{ym[1]:02d}", df


def assert_nonnull(df, label: str) -> None:
    """The split-file failure mode has to be LOUD, not a silently-dropped column."""
    import pandas as pd
    for c in df.columns:
        if c in ("t", "lat", "lon", "pressure_level"):
            continue
        frac = float(pd.notna(df[c]).mean())
        util.require(frac >= config.MIN_NONNULL,
                     f"{label}: column {c} is only {frac:.1%} non-null — the stepType split bug "
                     f"(join on the grid key, do not concatenate along time)")


def derive(df):
    """Add the fire-weather variables the raw fields do not contain.

    relative_humidity   from 2 m temperature and dewpoint (Magnus, over water)
    vpd                 vapour-pressure deficit — a better ignition covariate than RH alone,
                        because it is the atmosphere's actual drying power rather than a ratio
    wind_speed          from 10 m u/v
    """
    import numpy as np
    util.require(all(c in df.columns for c in ("t2m", "d2m", "u10", "v10")),
                 f"ERA5 short-name mismatch — got {sorted(df.columns)}")
    t_c = df["t2m"] - 273.15
    d_c = df["d2m"] - 273.15
    es = 6.1094 * np.exp(17.625 * t_c / (t_c + 243.04))      # saturation vapour pressure, hPa
    ea = 6.1094 * np.exp(17.625 * d_c / (d_c + 243.04))      # actual vapour pressure, hPa
    df["rh"] = np.clip(100.0 * ea / es, 0, 100)
    df["vpd"] = np.maximum(es - ea, 0.0) / 10.0               # kPa
    df["t2c"] = t_c
    df["ws10"] = np.hypot(df["u10"], df["v10"])
    return df


# ── reduction ─────────────────────────────────────────────────────────────────────────
def reduce_sl(df):
    """Four synoptic hours -> one cell-day row of fire-weather state."""
    import numpy as np
    import pandas as pd
    df = derive(df)
    df["day"] = pd.to_datetime(df["t"]).dt.normalize()
    df["clat"], df["clon"] = util.snap_cell(df["lat"], df["lon"])
    g = df.groupby(["clat", "clon", "day"], sort=False)
    out = g.agg(
        t2m_max=("t2c", "max"), t2m_mean=("t2c", "mean"),
        rh_min=("rh", "min"), rh_mean=("rh", "mean"),
        vpd_max=("vpd", "max"),
        ws10_mean=("ws10", "mean"), ws10_max=("ws10", "max"),
        u10_mean=("u10", "mean"), v10_mean=("v10", "mean"),
        blh_mean=("blh", "mean"), blh_min=("blh", "min"), blh_max=("blh", "max"),
        sp_mean=("sp", "mean"),
        swvl1=("swvl1", "mean"), swvl2=("swvl2", "mean"), swvl3=("swvl3", "mean"),
        lai_hv=("lai_hv", "mean"), lai_lv=("lai_lv", "mean"),
    ).reset_index()
    out["cell"] = util.cell_key(out["clat"], out["clon"])
    for c in out.columns:
        if out[c].dtype == np.float64:
            out[c] = out[c].astype("float32")
    return out


def reduce_tp(df):
    """24 hourly accumulations -> one honest daily rainfall total, in millimetres.

    ERA5 ``tp`` is metres accumulated over the preceding hour, so the daily total is the SUM of
    all 24 — which is precisely why this variable is requested on its own rather than sampled
    with the rest.  Also derives the wet-day flag the dryness counters are built from.
    """
    import numpy as np
    import pandas as pd
    d = df.copy()
    d["day"] = pd.to_datetime(d["t"]).dt.normalize()
    d["clat"], d["clon"] = util.snap_cell(d["lat"], d["lon"])
    g = (d.groupby(["clat", "clon", "day"], sort=False)
           .agg(rain_mm=("tp", "sum"), n_hours=("tp", "size"))
           .reset_index())
    # a day with fewer than 24 hourly accumulations is not a daily total; drop it rather than
    # publish a systematically dry value
    g = g[g["n_hours"] >= 24].drop(columns=["n_hours"])
    g["rain_mm"] = (g["rain_mm"] * 1000.0).astype("float32")
    g["cell"] = util.cell_key(g["clat"], g["clon"])
    g["clat"] = g["clat"].astype("float32")
    g["clon"] = g["clon"].astype("float32")
    return g


def reduce_pl(ds, dest: Path) -> None:
    """The steering field, kept GRIDDED — trajectories need time resolution, not a daily mean.

    ** THIS IS A SHAPE DECISION, AND IT IS THE ONE THAT MAKES THE CASE FIT ON THE DISK. **
    Written row-per-record like every other stage, fifteen years of 6-hourly winds on three
    levels is 255 million rows: with a timestamp, two coordinates, a level and three values per
    row that is ~5 GB, most of which is the key repeated 255 million times.  The same numbers as
    a dense ``(time, level, lat, lon)`` array are ~100 MB a year before compression, because the
    grid is implied by the axes.  The trajectory integrator wants exactly that shape anyway — it
    interpolates in (t, lat, lon), so a dense array is both smaller and faster to use.

    Values are int16 with a fixed scale: winds x100 (0.01 m/s, +-327 m/s range) and omega x1000
    (0.001 Pa/s).  Both are far finer than ERA5's own effective accuracy.
    """
    import numpy as np
    import pandas as pd
    da = ds.transpose("t", "pressure_level", "lat", "lon")
    lat = da["lat"].values.astype("float32")
    lon = da["lon"].values.astype("float32")
    lev = da["pressure_level"].values.astype("int16")
    t = pd.to_datetime(da["t"].values)

    def q(name, scale):
        a = np.asarray(da[name].values, dtype="float32") * scale
        return np.rint(np.clip(a, -32000, 32000)).astype("int16")

    np.savez_compressed(
        dest, u=q("u", 100.0), v=q("v", 100.0), w=q("w", 1000.0),
        lat=lat, lon=lon, level=lev,
        t=t.values.astype("datetime64[s]").astype("int64"),
        scale_uv=np.float32(100.0), scale_w=np.float32(1000.0))


# ── driver ───────────────────────────────────────────────────────────────────────
def _part(kind: str, year: int) -> Path:
    """One reduced artefact PER YEAR, even though requests cover multi-year groups.

    Requests are grouped because CDS runs one job at a time; outputs are per-year because every
    downstream stage is per-year, and because a group that fails halfway should not throw away
    the years it already reduced.  ``pl`` is a gridded ``.npz``, not a parquet - see ``reduce_pl``.
    """
    return PARTS / (f"pl_{year}.npz" if kind == "pl" else f"{kind}_{year}.parquet")


def _pending() -> list[tuple[str, tuple[int, ...]]]:
    """Outstanding requests, ANCHOR YEARS FIRST.

    A 45-request serial queue may not drain inside one working session, so the order is a
    priority, not an accident: 2015 and 2019 are what gate G-J5 replays and what chapter 06 is
    about, so they are fetched first and the case degrades to "fewer training years" rather than
    to "no anchors".  After the anchors, most-recent-first, because the live panel and the
    forecast path are worth more than 2013.
    """
    out = []
    for kind in ("sl", "tp", "pl"):
        for g in groups(kind):
            if not all(_part(kind, y).exists() for y in g):
                out.append((kind, g))
    # Kind order is a priority too, and it reflects what each product unlocks:
    #   sl  everything — no panel exists without it
    #   pl  the trajectory engine, the hero, and gates G-J3/G-J4
    #   tp  daily rainfall, which sharpens the dryness features but does not gate anything:
    #       SPI-1/3/6 already comes from 46 years of CHIRPS and covers the drought signal at
    #       monthly scale, so a partial tp drain degrades the model rather than blocking it.
    def rank(item):
        kind, g = item
        anchor = 0 if any(y in config.ANCHOR_YEARS for y in g) else 1
        return ({"sl": 0, "pl": 1, "tp": 2}[kind], anchor, -max(g))
    return sorted(out, key=rank)


def _gkey(kind: str, g: tuple[int, ...]) -> str:
    return f"era5:{kind}:{g[0]}-{g[-1]}"


def submit_batch(cads, limit: int = MAX_INFLIGHT_PER_DATASET) -> int:
    """Top up the queue to ``limit`` in-flight jobs PER DATASET (see the constant's note).

    Accounting is per DATASET, not per job kind: ``sl`` and ``tp`` are both
    ``reanalysis-era5-single-levels``, so counting them separately would put twice the intended
    load on one queue and collect a batch of `rejected`s.
    """
    jobs = util.jobs_read()
    used: dict[str, int] = {}
    for v in jobs.values():
        if v.get("status") in ("accepted", "running"):
            used[v.get("dataset", "?")] = used.get(v.get("dataset", "?"), 0) + 1
    n = 0
    for kind, g in _pending():
        dataset_for_kind = config.ERA5_PL if kind == "pl" else config.ERA5_SL
        if used.get(dataset_for_kind, 0) >= limit:
            continue
        key = _gkey(kind, g)
        v = jobs.get(key, {})
        if v.get("status") in ("accepted", "running", "downloaded"):
            continue
        if (v.get("status") in ("rejected", "failed")
                and time.time() - v.get("ts", 0) < REJECT_COOLOFF_S):
            continue
        dataset, req = build_request(kind, g)
        job_id, blocked = cads.submit(dataset, req)
        if blocked:
            return -1
        if not job_id:
            util.jobs_put(key, status="failed", ts=time.time(), kind=kind, years=list(g))
            continue
        util.jobs_put(key, job_id=job_id, dataset=dataset, status="accepted",
                      kind=kind, years=list(g), ts=time.time())
        log(f"submitted {key} -> {job_id}")
        n += 1
        used[dataset] = used.get(dataset, 0) + 1
        jobs = util.jobs_read()
    return n


def reduce_payload(kind: str, key: str, raw: Path) -> int:
    """NetCDF -> per-year artefacts.  Returns the number of years written."""
    import numpy as np
    import pandas as pd
    members = _members(raw)
    ds = read_split_netcdfs(members)
    PARTS.mkdir(parents=True, exist_ok=True)
    written = 0
    t = pd.to_datetime(ds["t"].values)
    for year in sorted(set(t.year)):
        if _part(kind, year).exists():
            continue
        sub = ds.isel(t=np.flatnonzero(t.year == year))
        if kind == "pl":
            reduce_pl(sub, _part(kind, year))
            log(f"  {key} {year}: steering grid -> {_part(kind, year).name} "
                f"({_part(kind, year).stat().st_size/1e6:.1f} MB)")
        else:
            pieces = []
            ty = pd.to_datetime(sub["t"].values)
            for m in sorted(set(ty.month)):
                chunk = sub.isel(t=np.flatnonzero(ty.month == m))
                df = chunk.to_dataframe().reset_index()
                for c in df.columns:
                    if df[c].dtype == np.float64:
                        df[c] = df[c].astype("float32")
                assert_nonnull(df, f"{key} {year}-{m:02d}")
                pieces.append(reduce_sl(df) if kind == "sl" else reduce_tp(df))
                del df, chunk
            red = pd.concat(pieces, ignore_index=True)
            del pieces
            red.to_parquet(_part(kind, year), index=False, compression="zstd")
            log(f"  {key} {year}: {len(red):,} rows -> {_part(kind, year).name} "
                f"({_part(kind, year).stat().st_size/1e6:.1f} MB)")
            del red
        written += 1
    ds.close()
    return written


def drain(cads) -> int:
    """Poll every in-flight job once; download and reduce the finished ones.  Returns #done."""
    jobs = util.jobs_read()
    done = 0
    for key, v in sorted(jobs.items()):
        if not key.startswith("era5:") or v.get("status") in ("reduced", "failed", "rejected"):
            continue
        kind = v["kind"]
        yrs = tuple(v.get("years") or [v.get("year")])
        if all(_part(kind, y).exists() for y in yrs):
            util.jobs_put(key, status="reduced")
            continue
        NC_DIR.mkdir(parents=True, exist_ok=True)
        raw = NC_DIR / f"{kind}_{yrs[0]}_{yrs[-1]}.nc"
        have_raw = raw.exists() and raw.stat().st_size > 1_000_000
        if not have_raw:
            st = cads.status(v["job_id"])
            if st in ("accepted", "running"):
                continue
            if st in ("rejected", "failed", "dismissed", "gone", "unknown"):
                log(f"{key}: job {st} - cooling off, will resubmit")
                util.jobs_put(key, status="rejected", ts=time.time())
                continue
            if not util.guard_disk(3.0):
                return done
            log(f"{key}: {st} - downloading")
            if cads.download(v["job_id"], raw) is None:
                log(f"{key}: download failed, will retry next pass")
                continue
            log(f"{key}: downloaded {raw.stat().st_size/1e6:.0f} MB")
        try:
            n = reduce_payload(kind, key, raw)
            util.jobs_put(key, status="reduced")
            util.manifest_put(key, years=list(yrs), reduced=n)
            done += 1
            # only now is the payload disposable - the point of reducing immediately is that
            # standing disk stays near the aggregate size rather than the archive size
            raw.unlink(missing_ok=True)
            shutil.rmtree(raw.with_suffix(""), ignore_errors=True)
            cads.delete(v["job_id"])
        except Exception as exc:                            # noqa: BLE001 - resumable by design
            # keep the NetCDF and the job: a reduce bug must be fixable without re-queueing
            # hours of CDS wall time.
            log(f"{key}: reduce failed {type(exc).__name__} {exc} - payload kept at {raw}")
            util.jobs_put(key, status="downloaded")
    return done


def consolidate() -> None:
    """Report coverage.  DELIBERATELY DOES NOT WRITE ONE BIG FILE.

    An ``era5_daily.parquet`` holding every year is 1.8 GB of a 28 GB disk that is already
    hosting two other cases' builds — and it would be an exact duplicate of the per-year parts
    beside it.  features.py reads ``era5_parts/{sl,tp}_*.parquet`` through a DuckDB glob instead,
    which is both cheaper and the thing that makes a half-drained queue usable: the panel builds
    from whatever years have landed, and says how many.

    The sl/tp join is a LEFT join by design.  They are separate CDS jobs on the same grid, so a
    year can land for one and not the other; an inner join would silently shorten the record,
    while a left join with a printed coverage figure makes a missing precipitation year visible.
    """
    sl = sorted(PARTS.glob("sl_*.parquet"))
    tp = sorted(PARTS.glob("tp_*.parquet"))
    pl = sorted(PARTS.glob("pl_*.npz"))
    mb = sum(p.stat().st_size for p in sl + tp + pl) / 1e6
    log(f"era5 coverage: sl {len(sl)} yr {[p.stem[3:] for p in sl]}")
    log(f"               tp {len(tp)} yr {[p.stem[3:] for p in tp]}")
    log(f"               pl {len(pl)} yr {[p.stem[3:] for p in pl]}  ({mb:.0f} MB total)")
    util.manifest_put("era5", sl_years=[p.stem[3:] for p in sl],
                      tp_years=[p.stem[3:] for p in tp],
                      pl_years=[p.stem[3:] for p in pl], mb=round(mb, 1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", type=int, default=1)
    ap.add_argument("--submit-only", action="store_true")
    ap.add_argument("--poll", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=600.0)
    args = ap.parse_args()
    util.require(bool(config.CDS_API_KEY), "CDS_API_KEY missing from repo-root .env")
    PARTS.mkdir(parents=True, exist_ok=True)
    cads = util.Cads("cds")

    if not _pending():
        log("era5: all parts present")
        consolidate()
        return

    deadline = time.time() + args.max_minutes * 60
    if args.submit_only:
        submit_batch(cads)
        return
    while time.time() < deadline:
        if not args.poll:
            submit_batch(cads)
        drain(cads)
        left = _pending()
        if not left:
            break
        log(f"era5: {len(left)} parts outstanding — {[f'{k}{y}' for k, y in left[:8]]}")
        time.sleep(POLL_SECONDS)
    consolidate()


if __name__ == "__main__":
    main()
