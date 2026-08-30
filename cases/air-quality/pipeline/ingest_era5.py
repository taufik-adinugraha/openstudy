"""Stage 1b — ERA5 hourly meteorology for the Jabodetabek box (Copernicus CDS).

The dispersion drivers, not the chemistry: 10 m wind u/v (ventilation and
transport direction), boundary-layer height (the volume the city's emissions
mix into — the single strongest control on surface PM2.5), 2 m temperature and
dewpoint (stability and hygroscopic growth), total precipitation (wet
scavenging), and surface solar radiation (photochemistry and mixing energy).

Resumable BY REQUEST: one CDS request per calendar month, newest month first,
so the model has recent data to train on long before the backfill finishes.
A month whose parquet already exists is skipped. The raw NetCDF is deleted as
soon as it has been aggregated — only the tidy per-cell table is kept.

CDS requests are queued server-side and can take minutes to hours. cdsapi's
blocking retrieve() polls for us; run this inside the aq-era5 systemd unit and
walk away. Rerunning simply resumes.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from datetime import UTC, datetime

import pandas as pd

import config

ERA5_RAW = config.RAW_DIR / "era5"
ERA5_PARTS = config.DATA_DIR / "era5_parts"
OUT = config.DATA_DIR / "era5_hourly.parquet"

# CDS short names -> our column names.
RENAME = {
    "u10": "u10", "v10": "v10", "blh": "blh", "t2m": "t2m",
    "d2m": "d2m", "tp": "tp", "ssrd": "ssrd",
}


def log(msg: str) -> None:
    print(f"[era5] {msg}", flush=True)


def months_to_do(start: str, end_dt) -> list[str]:
    """Newest first — recent months are what the live model needs."""
    lo = pd.Timestamp(start).tz_localize(None).replace(day=1)
    hi = pd.Timestamp(end_dt).tz_localize(None)
    rng = pd.date_range(lo, hi, freq="MS")
    return [m.strftime("%Y-%m") for m in rng][::-1]


def request_for(month: str) -> dict:
    year, mm = month.split("-")
    days = pd.Period(month, freq="M").days_in_month
    return {
        "product_type": ["reanalysis"],
        "variable": config.ERA5_VARS,
        "year": [year],
        "month": [mm],
        "day": [f"{d:02d}" for d in range(1, days + 1)],
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": config.ERA5_AREA,          # N, W, S, E
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def to_frame(path) -> pd.DataFrame:
    import xarray as xr

    ds = xr.open_dataset(path)
    try:
        # New CDS returns 'valid_time'; the legacy CDS returned 'time'.
        tname = "valid_time" if "valid_time" in ds.coords else "time"
        keep = [v for v in ds.data_vars if v in RENAME]
        if not keep:
            raise RuntimeError(f"no expected variables in {path.name}: {list(ds.data_vars)}")
        df = ds[keep].to_dataframe().reset_index()
    finally:
        ds.close()

    df = df.rename(columns={tname: "ts_utc"})
    cols = ["ts_utc", "latitude", "longitude"] + [c for c in RENAME if c in df.columns]
    df = df[[c for c in cols if c in df.columns]]
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    for c in df.columns:
        if c not in ("ts_utc",):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    return df.dropna(subset=["ts_utc"])


KEYS = ["ts_utc", "latitude", "longitude"]


def collapse(df: pd.DataFrame) -> pd.DataFrame:
    """The new CDS splits a multi-variable request into separate NetCDFs by
    stepType — instantaneous fields in one, accumulations (total precipitation,
    surface solar radiation) in another. Stacking those gives every variable a
    50% NaN column and silently loses the accumulations at the first
    de-duplication. They have to be JOINED on the grid key, not concatenated.
    """
    if df.duplicated(KEYS).any():
        df = df.groupby(KEYS, as_index=False).first()      # first() skips NaN
    return df.sort_values(KEYS)


def unpack(target):
    """CDS sometimes ignores download_format and ships a zip of .nc parts."""
    if zipfile.is_zipfile(target):
        with zipfile.ZipFile(target) as z:
            names = [n for n in z.namelist() if n.endswith(".nc")]
            outs = []
            for n in names:
                dest = target.parent / n.replace("/", "_")
                dest.write_bytes(z.read(n))
                outs.append(dest)
        return outs
    return [target]


def fetch(month: str) -> bool:
    import cdsapi

    part = ERA5_PARTS / f"{month}.parquet"
    if part.exists():
        return True
    ERA5_RAW.mkdir(parents=True, exist_ok=True)
    ERA5_PARTS.mkdir(parents=True, exist_ok=True)
    target = ERA5_RAW / f"{month}.nc"

    log(f"{month}: submitting CDS request (queued server-side, this can take a while)")
    t0 = datetime.now(UTC)
    client = cdsapi.Client()
    try:
        client.retrieve(config.ERA5_DATASET, request_for(month), str(target))
    except Exception as exc:                                # noqa: BLE001
        msg = str(exc)
        if "403" in msg or "licence" in msg.lower() or "license" in msg.lower():
            log(f"{month}: LICENCE BLOCKER — {msg[:300]}")
            log("This is the user's legal act; accept it in the CDS browser UI. Not retried.")
            return False
        log(f"{month}: request failed ({type(exc).__name__}: {msg[:200]}) — resumable, skipping")
        return True

    mins = (datetime.now(UTC) - t0).total_seconds() / 60
    parts = unpack(target)
    frames = [to_frame(p) for p in parts]
    df = collapse(pd.concat(frames, ignore_index=True))
    df.to_parquet(part, index=False)
    for p in parts:
        p.unlink(missing_ok=True)
    target.unlink(missing_ok=True)                          # delete raw immediately
    log(f"{month}: {len(df):,} cell-hours in {mins:.1f} min -> {part.name} (raw deleted)")
    return True


def repair_parts() -> None:
    """Rewrite any part written before the stepType split was handled."""
    fixed = 0
    for p in sorted(ERA5_PARTS.glob("*.parquet")):
        df = pd.read_parquet(p)
        if not df.duplicated(KEYS).any():
            continue
        collapse(df).to_parquet(p, index=False)
        fixed += 1
    log(f"repaired {fixed} part(s) that had instant/accum rows stacked instead of joined")


def consolidate() -> None:
    parts = sorted(ERA5_PARTS.glob("*.parquet"))
    if not parts:
        log("nothing to consolidate yet")
        return
    repair_parts()
    df = collapse(pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True))
    df = df.sort_values("ts_utc")
    df.to_parquet(OUT, index=False)
    miss = df[[c for c in RENAME if c in df.columns]].isna().mean()
    log(f"wrote {OUT.name}: {len(df):,} cell-hours, {df['ts_utc'].min()} -> {df['ts_utc'].max()} "
        f"({df[['latitude', 'longitude']].drop_duplicates().shape[0]} grid cells)")
    log("missing share per variable: " + ", ".join(f"{k} {v:.1%}" for k, v in miss.items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=config.START)
    ap.add_argument("--months", type=int, default=0, help="cap on months fetched this run (0 = all)")
    ap.add_argument("--consolidate-only", action="store_true")
    # CDS queues per request, and a single month can sit 'running' for the best
    # part of an hour. Sharding lets a few workers hold places in the queue at
    # once; each is still just one idle socket, and each skips what exists.
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    args = ap.parse_args()

    if args.consolidate_only:
        consolidate()
        return
    if not config.guard_disk(log):
        return

    # ERA5(T) publishes with ~5 days latency; asking for the current month is
    # fine (we get a partial file) but asking for the future is not.
    end = pd.Timestamp.utcnow().tz_localize(None)
    todo = months_to_do(args.start, end)
    if args.nshards > 1:
        todo = [m for i, m in enumerate(todo) if i % args.nshards == args.shard]
        log(f"shard {args.shard}/{args.nshards}: {len(todo)} months")
    done = 0
    for m in todo:
        if (ERA5_PARTS / f"{m}.parquet").exists():
            continue
        if not config.guard_disk(log):
            break
        if not fetch(m):
            sys.exit(3)                                     # licence blocker: stop, report
        done += 1
        if args.months and done >= args.months:
            log(f"month cap ({args.months}) reached — rerun to continue")
            break
    if args.shard == 0:          # one writer only — shards share the parts dir
        consolidate()


if __name__ == "__main__":
    main()
