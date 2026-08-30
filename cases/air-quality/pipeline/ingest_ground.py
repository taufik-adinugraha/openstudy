"""Stage 1a — ground PM2.5 from OpenAQ v3 (the target variable).

Two steps, both resumable:

  inventory   /v3/locations over the Jabodetabek bbox -> data/stations.json,
              recording every PM2.5 sensor with its first/last reading. This
              inventory is itself a finding: most of Jakarta's open network is
              stale or silent, and the dashboard publishes it.

  hours       /v3/sensors/{id}/hours month by month -> one parquet per
              sensor-month under data/raw/ground/. A month file that already
              exists is skipped unless it is the current month (which is
              always refetched, because it is still filling).

Consolidation into data/ground_hourly.parquet happens at the end of every run,
so a partial ingest still leaves a usable table behind.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta

import pandas as pd
import requests

import config

SESSION = requests.Session()
GROUND_RAW = config.RAW_DIR / "ground"
STATIONS = config.DATA_DIR / "stations.json"
OUT = config.DATA_DIR / "ground_hourly.parquet"


def log(msg: str) -> None:
    print(f"[ground] {msg}", flush=True)


def headers() -> dict[str, str]:
    key = os.environ.get("OPENAQ_API_KEY", "").strip()
    if not key:
        log("FATAL: OPENAQ_API_KEY missing from repo-root .env")
        sys.exit(2)
    return {"X-API-Key": key, "Accept": "application/json"}


def get(path: str, params: dict, tries: int = 5) -> dict:
    """GET with backoff. OpenAQ answers 429 on the free tier's minute budget."""
    url = f"{config.OPENAQ_BASE}{path}"
    for attempt in range(tries):
        r = SESSION.get(url, params=params, headers=headers(), timeout=90)
        if r.status_code == 429:
            wait = int(r.headers.get("retry-after", 0)) or min(60, 5 * 2**attempt)
            log(f"429 rate-limited — sleeping {wait}s")
            time.sleep(wait)
            continue
        if r.status_code in (500, 502, 503, 504):
            time.sleep(min(60, 5 * 2**attempt))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"giving up on {url} after {tries} tries")


# ── inventory ────────────────────────────────────────────────────────────
def inventory() -> list[dict]:
    w, s, e, n = config.AQ_BBOX
    payload = get("/locations", {"bbox": f"{w},{s},{e},{n}", "limit": 1000})
    stations: list[dict] = []
    for loc in payload.get("results", []):
        pm = [sn for sn in loc.get("sensors", []) if sn["parameter"]["name"] == "pm25"]
        if not pm:
            continue
        first = (loc.get("datetimeFirst") or {}).get("utc")
        last = (loc.get("datetimeLast") or {}).get("utc")
        stations.append(
            {
                "location_id": loc["id"],
                "name": loc["name"],
                "lat": loc["coordinates"]["latitude"],
                "lon": loc["coordinates"]["longitude"],
                "provider": (loc.get("provider") or {}).get("name"),
                "is_mobile": loc.get("isMobile", False),
                "sensor_ids": [sn["id"] for sn in pm],
                "first_utc": first,
                "last_utc": last,
            }
        )

    now = datetime.now(UTC)
    for st in stations:
        last = st["last_utc"]
        if not last:
            st["status"] = "silent"
            st["days_stale"] = None
        else:
            age = (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).days
            st["days_stale"] = age
            st["status"] = "live" if age <= 14 else "stale"

    stations.sort(key=lambda x: (x["status"] != "live", x["days_stale"] if x["days_stale"] is not None else 10**6))
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATIONS.write_text(json.dumps({"retrieved_utc": now.isoformat(), "bbox": config.AQ_BBOX,
                                    "stations": stations}, indent=2))
    counts = pd.Series([s["status"] for s in stations]).value_counts().to_dict()
    log(f"inventory: {len(stations)} PM2.5 stations in Jabodetabek — {counts}")
    return stations


# ── hourly measurements ──────────────────────────────────────────────────
def month_range(first: str | None, until: datetime) -> list[str]:
    start = pd.Timestamp(config.START, tz="UTC")
    if first:
        start = max(start, pd.Timestamp(first).tz_convert("UTC").normalize().replace(day=1))
    months = pd.date_range(start.replace(day=1), until, freq="MS", tz="UTC")
    return [m.strftime("%Y-%m") for m in months]


def fetch_sensor_month(sensor_id: int, month: str) -> pd.DataFrame:
    start = pd.Timestamp(f"{month}-01", tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    rows, page = [], 1
    while True:
        payload = get(
            f"/sensors/{sensor_id}/hours",
            {
                "datetime_from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "datetime_to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": 1000,
                "page": page,
            },
        )
        results = payload.get("results", [])
        for r in results:
            ts = (r.get("period") or {}).get("datetimeFrom", {}).get("utc")
            val = r.get("value")
            if ts is None or val is None:
                continue
            rows.append({"ts_utc": ts, "pm25": float(val), "n_sub": (r.get("coverage") or {}).get("observedCount")})
        if len(results) < 1000:
            break
        page += 1
        if page > 40:      # 40k hourly rows in one month is impossible; bail loudly
            log(f"  sensor {sensor_id} {month}: paging runaway, stopping at page 40")
            break
    if not rows:
        return pd.DataFrame(columns=["ts_utc", "pm25", "n_sub"])
    df = pd.DataFrame(rows)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True, format="ISO8601")
    return df.sort_values("ts_utc")


def pull(stations: list[dict], min_days: int) -> None:
    now = datetime.now(UTC)
    current_month = now.strftime("%Y-%m")
    GROUND_RAW.mkdir(parents=True, exist_ok=True)

    usable = [s for s in stations if s["first_utc"] and s["last_utc"]]
    log(f"pulling {len(usable)} stations with any record (skipping {len(stations) - len(usable)} silent)")

    for st in usable:
        if not config.guard_disk(log):
            return
        sdir = GROUND_RAW / str(st["location_id"])
        sdir.mkdir(exist_ok=True)
        months = month_range(st["first_utc"], min(now, pd.Timestamp(st["last_utc"]).to_pydatetime()))
        got = 0
        for m in months:
            path = sdir / f"{m}.parquet"
            if path.exists() and m != current_month:
                got += 1
                continue
            try:
                df = fetch_sensor_month(st["sensor_ids"][0], m)
            except Exception as exc:                      # noqa: BLE001 — one bad month must not kill the run
                log(f"  {st['location_id']} {m}: {type(exc).__name__} {exc} — skipped, resumable")
                continue
            df.to_parquet(path, index=False)
            got += 1
        log(f"  {st['location_id']:>8} {st['name'][:28]:<28} {st['status']:<6} {got}/{len(months)} months on disk")

    consolidate(stations, min_days)


def consolidate(stations: list[dict], min_days: int) -> None:
    frames = []
    by_id = {s["location_id"]: s for s in stations}
    for sdir in sorted(GROUND_RAW.glob("*")):
        if not sdir.is_dir():
            continue
        loc = int(sdir.name)
        parts = [pd.read_parquet(p) for p in sorted(sdir.glob("*.parquet"))]
        parts = [p for p in parts if len(p)]
        if not parts:
            continue
        df = pd.concat(parts, ignore_index=True)
        df["location_id"] = loc
        frames.append(df)
    if not frames:
        log("no ground data on disk yet")
        return

    all_df = pd.concat(frames, ignore_index=True)
    all_df["ts_utc"] = pd.to_datetime(all_df["ts_utc"], utc=True)
    # Physical plausibility filter: low-cost sensors emit negatives and
    # 1000+ spikes when the laser fouls. Cap at 1000 ug/m3, drop <0.
    bad = (all_df["pm25"] < 0) | (all_df["pm25"] > 1000)
    if bad.any():
        log(f"dropping {int(bad.sum())} physically implausible readings (<0 or >1000 ug/m3)")
    all_df = all_df[~bad]
    all_df = all_df.drop_duplicates(["location_id", "ts_utc"]).sort_values(["location_id", "ts_utc"])

    span = all_df.groupby("location_id")["ts_utc"].agg(["min", "max", "count"])
    span["days"] = (span["max"] - span["min"]).dt.total_seconds() / 86400
    span["completeness"] = span["count"] / (span["days"] * 24).clip(lower=1)
    keep = span[(span["days"] >= min_days)].index
    log(f"{len(span)} stations with data; {len(keep)} have >= {min_days} days of span")
    for loc, row in span.iterrows():
        st = by_id.get(loc, {})
        mark = "KEEP" if loc in keep else "drop"
        log(f"  {mark} {loc:>8} {str(st.get('name'))[:26]:<26} {int(row['count']):>6} h  "
            f"{row['days']:>6.0f}d  {row['completeness'] * 100:>5.1f}% complete  "
            f"{row['min'].date()}->{row['max'].date()}")

    out = all_df[all_df["location_id"].isin(keep)].copy()
    out.to_parquet(OUT, index=False)
    log(f"wrote {OUT.name}: {len(out):,} station-hours across {out['location_id'].nunique()} stations")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory-only", action="store_true")
    ap.add_argument("--min-days", type=int, default=180,
                    help="minimum span in days for a station to enter the modelling table")
    args = ap.parse_args()

    if not config.guard_disk(log):
        return
    stations = inventory()
    if args.inventory_only:
        return
    pull(stations, args.min_days)


if __name__ == "__main__":
    main()
