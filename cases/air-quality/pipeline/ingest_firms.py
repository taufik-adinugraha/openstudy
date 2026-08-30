"""Stage 1c — NASA FIRMS VIIRS fire hotspots: the biomass-burning signal.

Jakarta's haze episodes are not all local traffic. Peat and land-clearing
fires in south Sumatra and west Java load the regional airshed, and whether
that smoke reaches the city depends on where the fire is relative to the wind.
So raw hotspots are useless as a feature; what the model needs is "how much
burning is happening UPWIND, and how far away".

This stage therefore never stores raw hotspots. Each 10-day API window is
fetched, immediately reduced to counts and total fire radiative power per
(day x compass sector from Jakarta x distance ring), and the raw CSV is
dropped. A window whose aggregate parquet exists is skipped, so the run is
resumable and idempotent.

FIRMS splits history across two products (probed 2026-08-30):
  VIIRS_SNPP_SP   2012-01-20 -> 2026-04-27   standard processing (archive)
  VIIRS_SNPP_NRT  2026-04-28 -> today        near real time
The seam is read from the API's own data_availability endpoint, so it moves
forward on its own instead of rotting into a hard-coded date.
"""

from __future__ import annotations

import argparse
import io
import math
import os
import sys
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

import config

FIRE_PARTS = config.DATA_DIR / "fire_parts"
OUT = config.DATA_DIR / "fire_daily.parquet"

SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
RING_EDGES = [0, 100, 400, 1200]          # km from Jakarta
RING_NAMES = ["near", "mid", "far"]
WINDOW_DAYS = 5                           # FIRMS area API hard limit: "Expects [1..5]"


def log(msg: str) -> None:
    print(f"[firms] {msg}", flush=True)


def key() -> str:
    k = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not k:
        log("FATAL: FIRMS_MAP_KEY missing from repo-root .env")
        sys.exit(2)
    return k


def availability() -> dict[str, tuple[date, date]]:
    url = f"{config.FIRMS_BASE}/data_availability/csv/{key()}/ALL"
    df = pd.read_csv(io.StringIO(requests.get(url, timeout=60).text))
    return {
        r.data_id: (pd.Timestamp(r.min_date).date(), pd.Timestamp(r.max_date).date())
        for r in df.itertuples()
    }


def haversine_km(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    r = 6371.0
    p1, p2 = math.radians(config.JKT_LAT), np.radians(lat)
    dp = p2 - p1
    dl = np.radians(lon - config.JKT_LON)
    a = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def bearing_deg(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Compass bearing FROM Jakarta TO the hotspot."""
    p1, p2 = math.radians(config.JKT_LAT), np.radians(lat)
    dl = np.radians(lon - config.JKT_LON)
    y = np.sin(dl) * np.cos(p2)
    x = math.cos(p1) * np.sin(p2) - math.sin(p1) * np.cos(p2) * np.cos(dl)
    return (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0


def reduce_window(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["acq_date", "sector", "ring", "n_fire", "frp_sum"])
    raw = raw[pd.to_numeric(raw["latitude"], errors="coerce").notna()].copy()
    lat = raw["latitude"].astype(float).to_numpy()
    lon = raw["longitude"].astype(float).to_numpy()
    dist = haversine_km(lat, lon)
    brg = bearing_deg(lat, lon)

    raw["ring"] = pd.cut(dist, bins=RING_EDGES, labels=RING_NAMES, right=False)
    # 8 sectors centred on the compass points: N is 337.5..22.5.
    raw["sector"] = pd.Categorical(
        [SECTORS[int(((b + 22.5) % 360) // 45)] for b in brg], categories=SECTORS
    )
    raw["frp"] = pd.to_numeric(raw.get("frp"), errors="coerce").fillna(0.0)
    raw = raw.dropna(subset=["ring"])

    g = (raw.groupby(["acq_date", "sector", "ring"], observed=True)
            .agg(n_fire=("frp", "size"), frp_sum=("frp", "sum"))
            .reset_index())
    g["acq_date"] = pd.to_datetime(g["acq_date"]).dt.date
    return g


def fetch_window(src: str, start: date, days: int) -> pd.DataFrame:
    w, s, e, n = config.FIRE_BBOX
    url = (f"{config.FIRMS_BASE}/area/csv/{key()}/{src}/"
           f"{w},{s},{e},{n}/{days}/{start.isoformat()}")
    for attempt in range(4):
        r = requests.get(url, timeout=180)
        if r.status_code == 429:
            time.sleep(30 * (attempt + 1))
            continue
        r.raise_for_status()
        text = r.text
        if text.lstrip().lower().startswith(("invalid", "<!doctype", "<html")):
            raise RuntimeError(f"FIRMS returned a non-CSV body: {text[:160]}")
        if not text.strip() or "latitude" not in text.split("\n", 1)[0]:
            return pd.DataFrame()
        return pd.read_csv(io.StringIO(text))
    raise RuntimeError(f"rate-limited out on {src} {start}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=config.START)
    ap.add_argument("--windows", type=int, default=0, help="cap on windows fetched this run")
    args = ap.parse_args()

    if not config.guard_disk(log):
        return
    FIRE_PARTS.mkdir(parents=True, exist_ok=True)

    avail = availability()
    sp_min, sp_max = avail[config.FIRMS_ARCHIVE_SRC]
    nrt_min, nrt_max = avail[config.FIRMS_NRT_SRC]
    log(f"availability: SP {sp_min}->{sp_max}   NRT {nrt_min}->{nrt_max}")

    start = max(pd.Timestamp(args.start).date(), sp_min)
    end = nrt_max
    fetched = 0
    cur = start
    while cur <= end:
        days = min(WINDOW_DAYS, (end - cur).days + 1)
        tag = f"{cur.isoformat()}_{days}"
        part = FIRE_PARTS / f"{tag}.parquet"
        # Windows ending within the last 3 days are refetched: NRT keeps
        # back-filling late granules for ~48h.
        fresh = (end - (cur + timedelta(days=days - 1))).days < 3
        if part.exists() and not fresh:
            cur += timedelta(days=days)
            continue
        src = config.FIRMS_ARCHIVE_SRC if cur <= sp_max else config.FIRMS_NRT_SRC
        # A window straddling the SP/NRT seam: shorten it so it stays in SP.
        if src == config.FIRMS_ARCHIVE_SRC and cur + timedelta(days=days - 1) > sp_max:
            days = (sp_max - cur).days + 1
            tag = f"{cur.isoformat()}_{days}"
            part = FIRE_PARTS / f"{tag}.parquet"
        try:
            raw = fetch_window(src, cur, days)
        except Exception as exc:                            # noqa: BLE001
            log(f"{tag} [{src}]: {type(exc).__name__} {exc} — skipped, resumable")
            cur += timedelta(days=days)
            continue
        agg = reduce_window(raw)
        agg.to_parquet(part, index=False)                   # raw never touches disk
        log(f"{tag} [{src}]: {len(raw):,} hotspots -> {len(agg)} sector-days")
        fetched += 1
        cur += timedelta(days=days)
        if args.windows and fetched >= args.windows:
            log(f"window cap reached — rerun to continue")
            break
        time.sleep(0.7)                                     # stay far under 5000/10min
        if not config.guard_disk(log):
            break

    parts = sorted(FIRE_PARTS.glob("*.parquet"))
    if not parts:
        log("no fire aggregates yet")
        return
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    df = (df.groupby(["acq_date", "sector", "ring"], observed=True)[["n_fire", "frp_sum"]]
            .sum().reset_index().sort_values("acq_date"))
    df.to_parquet(OUT, index=False)
    log(f"wrote {OUT.name}: {len(df):,} sector-days, {df['acq_date'].min()} -> {df['acq_date'].max()}, "
        f"{int(df['n_fire'].sum()):,} hotspots total")


if __name__ == "__main__":
    main()
