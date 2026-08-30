"""Stage 1c — NASA FIRMS VIIRS fire hotspots: the biomass-burning signal.

Jakarta's haze episodes are not all local traffic. Peat and land-clearing
fires in south Sumatra and west Java load the regional airshed, and whether
that smoke reaches the city depends on where the fire is relative to the wind.
So raw hotspots are useless as a feature; what the model needs is "how much
burning is happening UPWIND, and how far away".

This stage therefore never stores raw hotspots. Each 5-day API window is
fetched, immediately reduced to counts and total fire radiative power per
(day x compass sector from Jakarta x distance ring), and the raw CSV is
dropped. A window whose aggregate parquet exists is skipped, so the run is
resumable and idempotent.

FIRMS splits history across two products (probed 2026-08-30):
  VIIRS_SNPP_SP   2012-01-20 -> 2026-04-27   standard processing (archive)
  VIIRS_SNPP_NRT  2026-04-28 -> today        near real time
The seam is read from the API's own data_availability endpoint, so it moves
forward on its own instead of rotting into a hard-coded date.

The static-source problem, and why a `type` filter alone is a bug
-----------------------------------------------------------------
VIIRS labels each detection: 0 fire, 1 active volcano, 2 other static land
source (gas flares, industrial heat), 3 offshore. Indonesia has ~130 active
volcanoes and heavy flaring, so those labels matter. But `type` is emitted
ONLY by the standard-processing product — **no NRT product carries the
column at all** (verified against the live API: SP returns `type`, NRT does
not). A `type == 0` filter guarded by "if the column exists" therefore cleans
the archive and leaves the recent tail dirty, and since this case splices the
two products it puts a false step change exactly at the SP/NRT seam. Fire
counts feed the model as a feature, so the step contaminates the chart and
the forecast together.

The fix is a static-source mask. Static sources do not move: a location the
archive labels a volcano or a flare is still one today. So the SP archive is
sampled across its whole span, every ~0.01 deg cell it ever labelled 1/2/3 is
collected with a persistence threshold, the cell is buffered by one neighbour
ring to absorb geolocation jitter, and the resulting mask is applied to EVERY
row of BOTH products — in addition to keeping the `type == 0` filter wherever
the column does exist. The mask is cached in data/firms_static_mask.parquet
and its per-window samples in data/static_mask_parts/, so a normal run never
refetches it.
"""

from __future__ import annotations

import argparse
import io
import math
import os
import shutil
import sys
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

import config

# Bumped whenever the reduction itself changes, so stale aggregates computed
# under an older rule are never silently reused. v2 = static-source mask.
REDUCE_VERSION = 2
FIRE_PARTS = config.DATA_DIR / f"fire_parts_v{REDUCE_VERSION}"
LEGACY_PARTS = config.DATA_DIR / "fire_parts"
OUT = config.DATA_DIR / "fire_daily.parquet"
AUDIT = config.DATA_DIR / "fire_filter_audit.parquet"

SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
RING_EDGES = [0, 100, 400, 1200]          # km from Jakarta
RING_NAMES = ["near", "mid", "far"]
WINDOW_DAYS = 5                           # FIRMS area API hard limit: "Expects [1..5]"

# ── static-source mask ───────────────────────────────────────────────────
STATIC_TYPES = (1, 2, 3)        # volcano / other static land source / offshore
MASK_CELL_DEG = 0.01            # ~1.1 km at this latitude; a VIIRS pixel is 375 m
MASK_BUFFER_CELLS = 1           # keep the 3x3 ring (~3.3 km) around each core cell:
                                # nadir pixels are 375 m but scan-edge pixels reach
                                # ~750 m and geolocation jitters between overpasses
MASK_MIN_WINDOWS = 2            # a cell must appear in >= 2 independently sampled
                                # months before it counts as persistent — one-off
                                # mislabels do not earn a permanent exclusion
MASK_MIN_COVERAGE = 0.90        # refuse to reduce real windows until this share of
                                # the sampled months is in hand: a mask built from a
                                # handful of samples would under-exclude. Above it the
                                # run proceeds — the mask is applied uniformly to both
                                # products either way, so a stray missing sample makes
                                # it slightly conservative, not biased at the seam.
MASK_PARTS = config.DATA_DIR / "static_mask_parts"
MASK_OUT = config.DATA_DIR / "firms_static_mask.parquet"
CELL_STRIDE = 1_000_000         # cell id = ilat * STRIDE + ilon (ilon is 9500..11900)


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


# ── the static-source mask ───────────────────────────────────────────────
def cell_ids(lat, lon) -> np.ndarray:
    """Grid lat/lon onto MASK_CELL_DEG cells as one integer id per cell."""
    ilat = np.floor(np.asarray(lat, dtype=float) / MASK_CELL_DEG).astype(np.int64)
    ilon = np.floor(np.asarray(lon, dtype=float) / MASK_CELL_DEG).astype(np.int64)
    return ilat * CELL_STRIDE + ilon


def mask_sample_starts(sp_min: date, sp_max: date) -> list[date]:
    """One 5-day window per calendar month across the whole SP archive.

    Volcanoes and flares are persistent by definition, so a monthly stride
    samples them many times over without fetching 14 years of dailies.
    """
    out: list[date] = []
    y, m = sp_min.year, sp_min.month
    while (y, m) <= (sp_max.year, sp_max.month):
        d = max(date(y, m, 1), sp_min)
        if d + timedelta(days=WINDOW_DAYS - 1) <= sp_max:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def static_hits(raw: pd.DataFrame) -> pd.DataFrame:
    """Cells this SP window labels as a static (non-fire) source."""
    cols = ["cell", "n_det", "n_t1", "n_t2", "n_t3"]
    if raw.empty or "type" not in raw.columns:
        return pd.DataFrame({c: pd.Series(dtype="int64") for c in cols})
    d = raw.copy()
    d["t"] = pd.to_numeric(d["type"], errors="coerce")
    d = d[d["t"].isin(STATIC_TYPES) & pd.to_numeric(d["latitude"], errors="coerce").notna()]
    if d.empty:
        return pd.DataFrame({c: pd.Series(dtype="int64") for c in cols})
    d["cell"] = cell_ids(d["latitude"].astype(float), d["longitude"].astype(float))
    for t in STATIC_TYPES:
        d[f"n_t{t}"] = (d["t"] == t).astype("int64")
    g = (d.groupby("cell")
          .agg(n_det=("t", "size"), n_t1=("n_t1", "sum"),
               n_t2=("n_t2", "sum"), n_t3=("n_t3", "sum"))
          .reset_index())
    return g[cols]


def build_static_mask(sp_min: date, sp_max: date, *, refresh: bool = False,
                      cap: int = 0) -> tuple[pd.DataFrame, bool]:
    """Persistent static-source cells, from the SP archive's own `type` labels.

    Returns (mask, usable). `usable` is False while fewer than
    MASK_MIN_COVERAGE of the sampled months are in hand — the caller must not
    reduce and cache real windows against a barely-built mask, so it stops and
    asks for a rerun instead. A rerun resumes from the cached samples.
    """
    MASK_PARTS.mkdir(parents=True, exist_ok=True)
    starts = mask_sample_starts(sp_min, sp_max)
    missing = [d for d in starts if not (MASK_PARTS / f"{d.isoformat()}.parquet").exists()]

    if MASK_OUT.exists() and not missing and not refresh:
        m = pd.read_parquet(MASK_OUT)
        log(f"static mask: {len(m):,} cells cached "
            f"({int(m['is_core'].sum()):,} core) — no refetch")
        return m, True

    if missing:
        log(f"static mask: {len(starts)} sampled windows "
            f"({starts[0]} -> {starts[-1]}), {len(missing)} still to fetch")
    got = 0
    for d in missing:
        if not config.guard_disk(log):
            break
        try:
            raw = fetch_window(config.FIRMS_ARCHIVE_SRC, d, WINDOW_DAYS)
        except Exception as exc:                            # noqa: BLE001
            log(f"mask {d}: {type(exc).__name__} {exc} — skipped, resumable")
            continue
        hits = static_hits(raw)
        hits.to_parquet(MASK_PARTS / f"{d.isoformat()}.parquet", index=False)
        got += 1
        if got % 12 == 0 or got == len(missing):
            log(f"  mask sampling {got}/{len(missing)}: {d} — {len(raw):,} hotspots, "
                f"{len(hits)} static cells, {int(hits['n_det'].sum()) if len(hits) else 0} detections")
        time.sleep(0.7)
        if cap and got >= cap:
            log("mask window cap reached — rerun to continue")
            break

    parts = sorted(MASK_PARTS.glob("*.parquet"))
    have = sum((MASK_PARTS / f"{d.isoformat()}.parquet").exists() for d in starts)
    coverage = have / max(len(starts), 1)
    usable = coverage >= MASK_MIN_COVERAGE
    frames = []
    for p in parts:
        f = pd.read_parquet(p)
        if len(f):
            frames.append(f.assign(w=p.stem))

    empty_cols = ["cell", "ilat", "ilon", "is_core", "n_det", "n_windows",
                  "n_t1", "n_t2", "n_t3"]
    if not frames:
        log("static mask: no static detections sampled yet — mask is empty")
        m = pd.DataFrame({c: pd.Series(dtype="int64") for c in empty_cols})
        m["is_core"] = m["is_core"].astype(bool)
        m.to_parquet(MASK_OUT, index=False)
        return m, False

    agg = (pd.concat(frames, ignore_index=True).groupby("cell")
             .agg(n_det=("n_det", "sum"), n_windows=("w", "nunique"),
                  n_t1=("n_t1", "sum"), n_t2=("n_t2", "sum"), n_t3=("n_t3", "sum"))
             .reset_index())
    core = agg[agg["n_windows"] >= MASK_MIN_WINDOWS].copy()
    core["ilat"] = core["cell"] // CELL_STRIDE
    core["ilon"] = core["cell"] - core["ilat"] * CELL_STRIDE

    # Buffer: every core cell plus its 8 neighbours. A detection of the same
    # flare lands in an adjacent cell often enough that an unbuffered mask
    # leaks the source back in.
    buffered: dict[int, bool] = {}
    for di in range(-MASK_BUFFER_CELLS, MASK_BUFFER_CELLS + 1):
        for dj in range(-MASK_BUFFER_CELLS, MASK_BUFFER_CELLS + 1):
            for c in ((core["ilat"] + di) * CELL_STRIDE + (core["ilon"] + dj)).to_numpy():
                buffered.setdefault(int(c), False)
    for c in core["cell"].to_numpy():
        buffered[int(c)] = True

    m = pd.DataFrame({"cell": list(buffered), "is_core": list(buffered.values())})
    m = m.merge(core[["cell", "n_det", "n_windows", "n_t1", "n_t2", "n_t3"]],
                on="cell", how="left")
    for c in ("n_det", "n_windows", "n_t1", "n_t2", "n_t3"):
        m[c] = m[c].fillna(0).astype("int64")
    m["ilat"] = m["cell"] // CELL_STRIDE
    m["ilon"] = m["cell"] - m["ilat"] * CELL_STRIDE
    m = m.sort_values("cell").reset_index(drop=True)
    m.to_parquet(MASK_OUT, index=False)

    log(f"static mask: {len(parts)} sampled windows -> {len(agg):,} candidate cells "
        f"-> {len(core):,} core (>= {MASK_MIN_WINDOWS} distinct months) "
        f"-> {len(m):,} cells with a {MASK_BUFFER_CELLS}-cell buffer")
    log(f"  core detections by label: volcano {int(core['n_t1'].sum()):,}, "
        f"static land/flare {int(core['n_t2'].sum()):,}, offshore {int(core['n_t3'].sum()):,}")
    if have < len(starts):
        log(f"static mask coverage {have}/{len(starts)} sampled months "
            f"({coverage * 100:.0f}%) — {'usable' if usable else 'TOO THIN'}; rerun to finish")
    return m, usable


def mask_lookup(mask: pd.DataFrame) -> np.ndarray:
    return np.sort(mask["cell"].to_numpy()) if len(mask) else np.empty(0, dtype=np.int64)


def in_mask(lat: np.ndarray, lon: np.ndarray, mask_ids: np.ndarray) -> np.ndarray:
    if not len(mask_ids):
        return np.zeros(len(lat), dtype=bool)
    return np.isin(cell_ids(lat, lon), mask_ids)


# ── reduction ────────────────────────────────────────────────────────────
EMPTY_AGG_COLS = ["acq_date", "sector", "ring", "n_fire", "frp_sum"]


def reduce_window(raw: pd.DataFrame, mask_ids: np.ndarray) -> tuple[pd.DataFrame, dict]:
    """Hotspots -> (day x sector x ring) counts.

    Three filters, applied in this order, all load-bearing:

      type == 0    where the column exists at all — which is the SP archive
                   only. VIIRS flags active volcanoes (1), other static land
                   sources such as flares and industrial heat (2) and offshore
                   (3). Kept because it is the most precise signal available,
                   but it CANNOT be the whole defence: no NRT product emits
                   the column, so on its own this filter cleans history and
                   leaves the recent tail dirty.
      static mask  the product-independent defence. Cells the SP archive has
                   repeatedly labelled 1/2/3, buffered by one cell, dropped
                   from EVERY row of BOTH products. This is what keeps the
                   SP/NRT seam from inventing a step change in the fire series.
      confidence   drop 'l' (low). The NRT and standard-processing products
                   disagree most in the low-confidence tail, and this case
                   splices them — filtering keeps the seam honest.

    Returns the aggregate and an audit row counting what each filter removed,
    so the effect of the mask is measurable rather than asserted.
    """
    audit = {"n_raw": int(len(raw)), "n_valid": 0, "n_type_static": 0,
             "n_lowconf": 0, "n_in_mask_all": 0, "n_mask_dropped": 0, "n_kept": 0}
    empty = pd.DataFrame({c: pd.Series(dtype="object") for c in EMPTY_AGG_COLS})
    if raw.empty:
        return empty, audit

    raw = raw[pd.to_numeric(raw["latitude"], errors="coerce").notna()].copy()
    audit["n_valid"] = int(len(raw))
    if raw.empty:
        return empty, audit

    lat_all = raw["latitude"].astype(float).to_numpy()
    lon_all = raw["longitude"].astype(float).to_numpy()
    audit["n_in_mask_all"] = int(in_mask(lat_all, lon_all, mask_ids).sum())

    if "type" in raw.columns:
        keep_type = pd.to_numeric(raw["type"], errors="coerce") == 0
        audit["n_type_static"] = int((~keep_type).sum())
        raw = raw[keep_type]
    if "confidence" in raw.columns:
        keep_conf = ~raw["confidence"].astype(str).str.lower().str.startswith("l")
        audit["n_lowconf"] = int((~keep_conf).sum())
        raw = raw[keep_conf]
    if raw.empty:
        return empty, audit

    lat = raw["latitude"].astype(float).to_numpy()
    lon = raw["longitude"].astype(float).to_numpy()
    masked = in_mask(lat, lon, mask_ids)
    audit["n_mask_dropped"] = int(masked.sum())
    if masked.any():
        raw = raw[~masked]
        lat, lon = lat[~masked], lon[~masked]
    audit["n_kept"] = int(len(raw))
    if raw.empty:
        return empty, audit

    dist = haversine_km(lat, lon)
    brg = bearing_deg(lat, lon)

    raw = raw.copy()
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
    return g, audit


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


def write_audit(rows: list[dict]) -> None:
    """Merge this run's filter counts into the durable audit table."""
    if not rows:
        return
    new = pd.DataFrame(rows)
    if AUDIT.exists():
        old = pd.read_parquet(AUDIT)
        old = old[~old["tag"].isin(new["tag"])]
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values("start").reset_index(drop=True)
    new.to_parquet(AUDIT, index=False)

    for src, g in new.groupby("src"):
        kept, dropped = int(g["n_kept"].sum()), int(g["n_mask_dropped"].sum())
        base = kept + dropped
        log(f"mask effect [{src}]: {dropped:,} of {base:,} otherwise-kept detections "
            f"removed ({100 * dropped / max(base, 1):.2f}%); "
            f"{int(g['n_type_static'].sum()):,} more dropped by the type label")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=config.START)
    ap.add_argument("--windows", type=int, default=0, help="cap on windows fetched this run")
    ap.add_argument("--mask-windows", type=int, default=0,
                    help="cap on static-mask sample windows fetched this run")
    ap.add_argument("--refresh-mask", action="store_true",
                    help="rebuild the static mask from its cached samples")
    ap.add_argument("--mask-only", action="store_true",
                    help="build the static-source mask and stop")
    args = ap.parse_args()

    if not config.guard_disk(log):
        return
    FIRE_PARTS.mkdir(parents=True, exist_ok=True)

    avail = availability()
    sp_min, sp_max = avail[config.FIRMS_ARCHIVE_SRC]
    nrt_min, nrt_max = avail[config.FIRMS_NRT_SRC]
    log(f"availability: SP {sp_min}->{sp_max}   NRT {nrt_min}->{nrt_max}")

    mask, mask_usable = build_static_mask(
        sp_min, sp_max, refresh=args.refresh_mask, cap=args.mask_windows)
    if args.mask_only:
        return
    if not mask_usable:
        log("not reducing windows against a barely-built mask — rerun to finish the mask first")
        return
    mask_ids = mask_lookup(mask)

    start = max(pd.Timestamp(args.start).date(), sp_min)
    end = nrt_max
    fetched = 0
    audit_rows: list[dict] = []
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
        agg, audit = reduce_window(raw, mask_ids)
        agg.to_parquet(part, index=False)                   # raw never touches disk
        audit_rows.append({"tag": tag, "src": src, "start": cur, "days": days, **audit})
        log(f"{tag} [{src}]: {len(raw):,} hotspots -> {audit['n_kept']:,} kept "
            f"({audit['n_type_static']:,} static by label, {audit['n_mask_dropped']:,} by mask, "
            f"{audit['n_lowconf']:,} low confidence) -> {len(agg)} sector-days")
        fetched += 1
        cur += timedelta(days=days)
        if args.windows and fetched >= args.windows:
            log("window cap reached — rerun to continue")
            break
        time.sleep(0.7)                                     # stay far under 5000/10min
        if not config.guard_disk(log):
            break

    write_audit(audit_rows)

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

    # Aggregates reduced under the pre-mask rule are not comparable with these
    # and must never be mixed back in.
    if LEGACY_PARTS.exists() and cur > end:
        shutil.rmtree(LEGACY_PARTS)
        log(f"removed {LEGACY_PARTS.name}/ — reduced under the pre-mask rule (v1)")


if __name__ == "__main__":
    main()
