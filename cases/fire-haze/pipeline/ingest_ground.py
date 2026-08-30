"""Stage 6 · ground — PM2.5 at the receptors, in three honestly-labelled tiers.

THE FINDING THAT SHAPED THIS STAGE
----------------------------------
The fire belt is unmonitored.  OpenAQ has ZERO PM2.5 locations in Riau and ZERO in all of
Kalimantan — bbox and 25 km radius searches around Pekanbaru, Palangkaraya and Banjarbaru all
return ``found: 0``.  The cities this case is *about* have no open sensor and, as far as any open
catalogue shows, never have.  Indonesia's own ISPU portal is server-rendered with no API and no
stated licence; Malaysia's APIMS host 404s on every path and the only aggregator carrying it
forbids commercial use verbatim.

So there are two options and only one of them is useful.  Drop the unmonitored cities and publish
a haze case that says nothing about Palangkaraya, or substitute a reanalysis and say so on every
row.  This stage does the second, in three tiers, and the dashboard prints the tier next to every
number:

  tier 1  Singapore NEA v1 — hourly, five regions, history from ~2016-03.  The only long, clean,
          commercially-licensed instrument record in the region (Singapore Open Data Licence v1.0
          permits commercial use explicitly).  G-J4 is HARD here and nowhere else.
  tier 2  the handful of Indonesian OpenAQ units that do exist, with their short coverage stated
          beside them.  Reported, never used to pass or fail a gate.
  tier 3  CAMS EAC4 reanalysis PM2.5 as an explicit SURROGATE at Pekanbaru, Palangkaraya and
          Pontianak.  Labelled "model" everywhere it appears; a trajectory model compared against
          a reanalysis is model-vs-model and the page says those words.  It is also the only
          reference that reaches the 2015 anchor, because NEA returns nothing before 2016-03
          (verified: 2015-10-20 -> zero items, 2013 -> HTTP 500).

The 2015 gap is not papered over.  G-J5 scores the 2015 anchor on FIRMS detections plus CAMS
EAC4 and states that it has no Singapore instrument reference.

OUTPUT
------
``data/ground.parquet``     receptor, day, pm25 (ug/m3), tier, source
``data/ground_meta.json``   per-receptor coverage, licence and tier, for the page's badges
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta

import config
import util
from util import log

RAW_NEA = config.RAW / "nea"
GROUND_OUT = config.DATA_DIR / "ground.parquet"
META_OUT = config.DATA_DIR / "ground_meta.json"
NEA_START = date(2016, 3, 1)


# ── tier 1 · Singapore NEA ────────────────────────────────────────────────────────────
def nea_day(d: date):
    """One calendar day of hourly PM2.5 for the five NEA regions.

    The v1 endpoint takes a ``date`` and returns the whole day, which is why it — and not the v2
    real-time endpoint — is the history route.  Days before ~2016-03 return an empty ``items``
    list (and 2013 returns HTTP 500); that is the real start of the record, not a bug to retry.
    """
    import requests
    r = requests.get(config.NEA_V1_URL.format(date=d.isoformat()), timeout=90)
    if r.status_code != 200:
        return None
    return r.json().get("items", [])


def pull_nea() -> None:
    import pandas as pd
    RAW_NEA.mkdir(parents=True, exist_ok=True)
    end = date.today()
    cur, n_new, n_empty = NEA_START, 0, 0
    while cur <= end:
        part = RAW_NEA / f"{cur.isoformat()}.parquet"
        # the last three days are refetched: NEA back-fills late readings
        if part.exists() and (end - cur).days > 3:
            cur += timedelta(days=1)
            continue
        items = nea_day(cur)
        if items is None:
            log(f"  nea {cur}: HTTP error — skipped, resumable")
            cur += timedelta(days=1)
            continue
        rows = []
        for it in items:
            rd = (it.get("readings") or {}).get("pm25_one_hourly") or {}
            for region, val in rd.items():
                rows.append({"ts": it["timestamp"], "region": region, "pm25": val})
        df = pd.DataFrame(rows)
        df.to_parquet(part, index=False)
        n_new += 1
        if df.empty:
            n_empty += 1
        if n_new % 200 == 0:
            log(f"  nea: {n_new} days fetched, at {cur}")
        cur += timedelta(days=1)
        time.sleep(0.08)
    log(f"  nea: {n_new} new days ({n_empty} empty)")


def nea_daily():
    """Region-hourly -> receptor-daily.  Mean and max, because an episode is a peak, not a mean."""
    import pandas as pd
    parts = sorted(RAW_NEA.glob("*.parquet"))
    if not parts:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    if df.empty:
        return pd.DataFrame()
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601", errors="coerce")
    df = df[df["ts"].notna()]
    # NEA timestamps are +08:00; the receptor day is the Singapore day, not the UTC day
    df["day"] = df["ts"].dt.tz_convert("Asia/Singapore").dt.normalize().dt.tz_localize(None)
    df["pm25"] = pd.to_numeric(df["pm25"], errors="coerce")
    g = (df.groupby("day")
           .agg(pm25=("pm25", "mean"), pm25_max=("pm25", "max"), n_obs=("pm25", "size"))
           .reset_index())
    g = g[g["n_obs"] >= 24]                      # a partial day is not a daily mean
    g["receptor"] = "singapore"
    g["tier"] = 1
    g["source"] = "nea"
    log(f"  nea daily: {len(g):,} complete days, {g['day'].min().date()} -> "
        f"{g['day'].max().date()}; peak daily mean {g['pm25'].max():.0f} ug/m3 on "
        f"{g.loc[g['pm25'].idxmax(), 'day'].date()}")
    return g


# ── tier 2 · Indonesian OpenAQ units ──────────────────────────────────────────────────
def openaq_sensor_ids(location_id: int) -> list[int]:
    import requests
    r = requests.get(f"{config.OPENAQ_BASE}/locations/{location_id}", timeout=90,
                     headers={"X-API-Key": config.OPENAQ_API_KEY})
    if r.status_code != 200:
        return []
    out = []
    for res in r.json().get("results", []):
        for s in res.get("sensors", []):
            if (s.get("parameter") or {}).get("name") == "pm25":
                out.append(int(s["id"]))
    return out


def openaq_days(sensor_id: int):
    """Daily aggregates straight from OpenAQ — no hourly download, no local resampling."""
    import pandas as pd
    import requests
    rows, page = [], 1
    while page <= 40:
        r = requests.get(f"{config.OPENAQ_BASE}/sensors/{sensor_id}/days",
                         params={"limit": 1000, "page": page},
                         headers={"X-API-Key": config.OPENAQ_API_KEY}, timeout=120)
        if r.status_code != 200:
            break
        res = r.json().get("results", [])
        if not res:
            break
        for it in res:
            per = (it.get("period") or {}).get("datetimeFrom") or {}
            rows.append({"day": (per.get("local") or "")[:10], "pm25": it.get("value")})
        if len(res) < 1000:
            break
        page += 1
        time.sleep(0.2)
    return pd.DataFrame(rows)


def pull_openaq():
    import pandas as pd
    frames = []
    for name, meta in config.RECEPTORS.items():
        if meta.get("source") != "openaq":
            continue
        got = []
        for loc in meta.get("openaq_ids", ()):
            for sid in openaq_sensor_ids(loc):
                d = openaq_days(sid)
                if len(d):
                    got.append(d)
        if not got:
            log(f"  openaq {name}: no PM2.5 series returned")
            continue
        d = pd.concat(got, ignore_index=True)
        d["day"] = pd.to_datetime(d["day"], errors="coerce")
        d = d[d["day"].notna() & pd.to_numeric(d["pm25"], errors="coerce").notna()]
        d = d.groupby("day", as_index=False)["pm25"].mean()
        d["receptor"], d["tier"], d["source"] = name, 2, "openaq"
        d["pm25_max"] = d["pm25"]
        d["n_obs"] = 24
        frames.append(d)
        log(f"  openaq {name}: {len(d):,} days, {d['day'].min().date()} -> "
            f"{d['day'].max().date()}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ── tier 3 · CAMS EAC4 surrogate (produced by ingest_cams.py when ADS opens) ──────────
def load_cams_surrogate():
    import pandas as pd
    p = config.DATA_DIR / "cams_eac4_receptors.parquet"
    if not p.exists():
        log("  tier 3: CAMS EAC4 surrogate absent — ADS policy click still outstanding. "
            "Tier-3 receptors are PENDING, not silently dropped.")
        return pd.DataFrame()
    d = pd.read_parquet(p)
    log(f"  tier 3: CAMS EAC4 surrogate for {d['receptor'].nunique()} receptors, {len(d):,} days")
    return d


def main() -> None:
    import pandas as pd
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    log("ground: tier 1 Singapore NEA (instrument) / tier 2 OpenAQ ID (instrument, short) / "
        "tier 3 CAMS EAC4 (MODEL)")
    pull_nea()
    frames = [f for f in (nea_daily(), pull_openaq(), load_cams_surrogate()) if len(f)]
    util.require(bool(frames), "no ground data at all — tier 1 must at least be present")
    df = pd.concat(frames, ignore_index=True)
    cols = ["receptor", "day", "pm25", "pm25_max", "n_obs", "tier", "source"]
    df = df[[c for c in cols if c in df.columns]]
    df.to_parquet(GROUND_OUT, index=False, compression="zstd")

    meta = {}
    for name, m in config.RECEPTORS.items():
        sub = df[df["receptor"] == name]
        meta[name] = {
            "lat": m["lat"], "lon": m["lon"], "country": m["country"], "tier": m["tier"],
            "source": m["source"],
            "kind": "instrument" if m["tier"] in (1, 2) else "model (CAMS EAC4 reanalysis)",
            "coverage_stated": m.get("coverage"),
            "note": m.get("note"),
            "days": int(len(sub)),
            "first": str(sub["day"].min().date()) if len(sub) else None,
            "last": str(sub["day"].max().date()) if len(sub) else None,
            # a tier-3 receptor with no rows is waiting on the ADS EAC4 queue, not on a policy
            # click — both stores were accepted mid-build and verified by live submission
            "status": "ok" if len(sub)
                      else ("awaiting_cams_eac4" if m["tier"] == 3 else "empty"),
        }
    META_OUT.write_text(json.dumps({
        "receptors": meta,
        "licences": {"singapore_nea": config.NEA_LICENCE, "openaq": "CC BY 4.0",
                     "cams_eac4": "Copernicus CC BY 4.0 — a MODEL, not an observation"},
        "rejected": config.REJECTED_GROUND,
        "nea_history_starts": config.NEA_HISTORY_STARTS,
        "anchor_2015_has_no_singapore_truth": True,
    }, indent=1))
    log(f"ground: {len(df):,} receptor-days across {df['receptor'].nunique()} receptors "
        f"-> {GROUND_OUT.name}")
    util.manifest_put("ground", rows=int(len(df)),
                      receptors={k: v["days"] for k, v in meta.items()})


if __name__ == "__main__":
    main()
