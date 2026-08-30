"""Stage 4 — NaN-safe view models for the dashboard.

Everything the page needs is precomputed here; the browser never sees a row of
raw data. json.dumps(..., allow_nan=False) is deliberate: a NaN would sail
silently through JSON.parse as `null` and surface as a hole in a chart, so we
would rather the export fail loudly at build time.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

import config

OUT = config.WEB_DATA
SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
RINGS = ["near", "mid", "far"]


def log(msg: str) -> None:
    print(f"[export] {msg}", flush=True)


def clean(o):
    """Recursively replace NaN/Inf with None so allow_nan=False can hold."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return o


def write(name: str, payload, also_src: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    body = json.dumps(clean(payload), allow_nan=False, separators=(",", ":"))
    path = OUT / name
    path.write_text(body)
    if also_src:                       # small payloads the page renders server-side
        config.WEB_SRC_DATA.mkdir(parents=True, exist_ok=True)
        (config.WEB_SRC_DATA / name).write_text(body)
    log(f"{name}: {path.stat().st_size / 1024:.0f} kB")


def main() -> None:
    stats = json.loads((config.DATA_DIR / "stats.json").read_text())
    feats = pd.read_parquet(config.DATA_DIR / "features.parquet")
    preds = pd.read_parquet(config.DATA_DIR / "predictions.parquet")
    fc_path = config.DATA_DIR / "forecast.parquet"
    fc = pd.read_parquet(fc_path) if fc_path.exists() else pd.DataFrame()
    fire_path = config.DATA_DIR / "fire_daily.parquet"
    fire = pd.read_parquet(fire_path) if fire_path.exists() else pd.DataFrame()

    write("summary.json", stats, also_src=True)

    # ── the live chapter: last 21 days of observation, plus the forecast ──
    feats["ts_utc"] = pd.to_datetime(feats["ts_utc"], utc=True)
    primary = int(max(stats["network"]["stations"],
                      key=lambda s: (s["status"] == "live", s.get("n_hours") or 0))["location_id"])
    g = feats[feats["location_id"] == primary].sort_values("ts_utc")
    recent = g[g["ts_utc"] > g["ts_utc"].max() - pd.Timedelta(days=21)]
    live = {
        "location_id": primary,
        "observed": [{"t": t.isoformat(), "pm25": v}
                     for t, v in zip(recent["ts_utc"], recent["pm25"]) if pd.notna(v)],
        "meteo": [{"t": t.isoformat(), "blh": b, "wind": w, "precip": p}
                  for t, b, w, p in zip(recent["ts_utc"], recent["blh"],
                                        recent["wind_speed"], recent["precip_mm"])
                  if pd.notna(b)],
        "forecast": [],
    }
    if len(fc):
        f = fc[fc["location_id"] == primary].sort_values("horizon_h")
        # Reuse the empirical PI80 width per horizon from the backtest as the
        # honest band on the live forecast.
        band = (preds.assign(w_lo=lambda d: d["predicted"] - d["lo"],
                             w_hi=lambda d: d["hi"] - d["predicted"])
                     .groupby("horizon_h")[["w_lo", "w_hi"]].median())
        for r in f.itertuples():
            w = band.loc[r.horizon_h] if r.horizon_h in band.index else {"w_lo": 0, "w_hi": 0}
            live["forecast"].append({
                "t": pd.Timestamp(r.valid_utc).isoformat(), "h": int(r.horizon_h),
                "pm25": float(r.pm25_pred),
                "lo": max(0.0, float(r.pm25_pred) - float(w["w_lo"])),
                "hi": float(r.pm25_pred) + float(w["w_hi"]),
            })
        live["issue_utc"] = pd.Timestamp(f["issue_utc"].max()).isoformat() if len(f) else None
        live["pm25_now"] = float(f["pm25_now"].iloc[0]) if len(f) else None
    write("live.json", live)

    # ── the validation chapter: 24 h backtest trace ──────────────────────
    h = 24 if 24 in preds["horizon_h"].unique() else int(preds["horizon_h"].max())
    b = preds[(preds["horizon_h"] == h) & (preds["location_id"] == primary)].copy()
    b["valid_utc"] = pd.to_datetime(b["issue_utc"], utc=True) + pd.Timedelta(hours=h)
    b = b.sort_values("valid_utc")
    if len(b) > 2400:                              # keep the payload lean, keep the shape
        b = b.iloc[:: max(1, len(b) // 2400)]
    write("backtest.json", {
        "horizon_h": h, "location_id": primary,
        "rows": [{"t": t.isoformat(), "y": y, "p": p, "lo": lo, "hi": hi, "b": pb}
                 for t, y, p, lo, hi, pb in zip(b["valid_utc"], b["observed"], b["predicted"],
                                                b["lo"], b["hi"], b["persistence"])],
        "eval": stats["model"]["eval"],
    })

    # ── the airshed chapter: wind rose + fire rose ───────────────────────
    fr = feats.dropna(subset=["wind_from_sector", "pm25"])
    rose = []
    for s in SECTORS:
        sub = fr[fr["wind_from_sector"] == s]
        rose.append({
            "sector": s,
            "hours": int(len(sub)),
            "share": float(len(sub) / max(len(fr), 1)),
            "mean_pm25": float(sub["pm25"].mean()) if len(sub) else None,
            "p90_pm25": float(sub["pm25"].quantile(0.9)) if len(sub) else None,
            "episode_rate": float((sub["pm25"] >= config.EPISODE_THRESHOLD).mean()) if len(sub) else None,
        })
    fire_rose = []
    if len(fire):
        fd = fire.copy()
        fd["acq_date"] = pd.to_datetime(fd["acq_date"])
        tot = fd.groupby(["sector", "ring"], observed=True)["n_fire"].sum()
        for s in SECTORS:
            for r in RINGS:
                fire_rose.append({"sector": s, "ring": r, "n_fire": int(tot.get((s, r), 0))})
        monthly = (fd.assign(m=fd["acq_date"].dt.to_period("M").astype(str))
                     .groupby("m")["n_fire"].sum())
        fire_series = [{"m": k, "n": int(v)} for k, v in monthly.items()]
    else:
        fire_series = []
    write("airshed.json", {"wind_rose": rose, "fire_rose": fire_rose,
                           "fire_monthly": fire_series,
                           "jakarta": {"lat": config.JKT_LAT, "lon": config.JKT_LON},
                           "rings_km": [0, 100, 400, 1200]})

    # ── the network chapter: the station map (2-D canvas, no WebGL) ──────
    write("network.json", {
        "bbox": list(config.AQ_BBOX),
        "stations": [{k: s.get(k) for k in
                      ("location_id", "name", "lat", "lon", "provider", "status",
                       "days_stale", "first_utc", "last_utc", "n_hours",
                       "mean_pm25", "completeness_90d")}
                     for s in stats["network"]["stations"]],
    })

    # ── drivers ──────────────────────────────────────────────────────────
    write("drivers.json", {"horizon_h": int(stats["model"]["top_drivers"][0].get("horizon_h", 24)),
                           "features": stats["model"]["top_drivers"]})


if __name__ == "__main__":
    main()
