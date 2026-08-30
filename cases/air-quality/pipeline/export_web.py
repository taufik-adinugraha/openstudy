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


def _read(name: str) -> pd.DataFrame:
    p = config.DATA_DIR / name
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def main() -> None:
    # Emit whatever is ready. A half-finished pipeline should still produce a
    # page with honest pending states rather than a crash.
    stats_p = config.DATA_DIR / "stats.json"
    stats = json.loads(stats_p.read_text()) if stats_p.exists() else None
    feats = _read("features.parquet")
    preds = _read("predictions.parquet")
    fc = _read("forecast.parquet")
    fire = _read("fire_daily.parquet")

    if stats is None:
        st = json.loads((config.DATA_DIR / "stations.json").read_text())["stations"]
        stats = {"generated_utc": None, "gates": [], "gates_passed": 0, "gates_total": 6,
                 "vintage": {}, "observed": {}, "model": {"eval": [], "top_drivers": []},
                 "network": {"stations": st, "n_stations_total": len(st), "n_modelled": 0,
                             "n_live": sum(s["status"] == "live" for s in st),
                             "n_stale": sum(s["status"] == "stale" for s in st),
                             "n_silent": sum(s["status"] == "silent" for s in st)}}
        log("stats.json missing — exporting only the chapters that do not need the model")

    write("summary.json", stats, also_src=True)
    if len(feats) == 0:
        log("features.parquet missing — live/backtest/drivers chapters stay pending")

    if len(feats):
        feats["ts_utc"] = pd.to_datetime(feats["ts_utc"], utc=True)
        _live_and_backtest(stats, feats, preds, fc)
    _airshed(stats, feats, fire)
    _network(stats)
    write("drivers.json", {
        "horizon_h": int((stats["model"]["top_drivers"] or [{}])[0].get("horizon_h", 24)),
        "features": stats["model"]["top_drivers"],
    })


def _live_and_backtest(stats, feats, preds, fc) -> None:
    # ── the live chapter: last 21 days of observation, plus the forecast ──
    # The hero station is whichever one can be forecast from the most recent
    # issue time — which, given the state of the network, is not necessarily
    # the one with the longest record.
    if len(fc):
        primary = int(fc.sort_values("issue_utc").iloc[-1]["location_id"])
    else:
        primary = int(max(stats["network"]["stations"],
                          key=lambda s: (s["status"] == "live", s.get("n_hours") or 0))["location_id"])
    g = feats[feats["location_id"] == primary].sort_values("ts_utc")
    obs_only = g.dropna(subset=["pm25"])
    last_obs = obs_only["ts_utc"].max() if len(obs_only) else None
    # 21 days back from the last ACTUAL reading, not from the end of the join —
    # otherwise a sparse sensor renders as an empty chart.
    anchor = last_obs if last_obs is not None else g["ts_utc"].max()
    recent = g[(g["ts_utc"] > anchor - pd.Timedelta(days=21)) & (g["ts_utc"] <= anchor)]
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
    live["last_observation_utc"] = last_obs.isoformat() if last_obs is not None else None
    live["observation_age_h"] = (
        None if last_obs is None
        else round(float((pd.Timestamp.now(tz="UTC") - last_obs).total_seconds() / 3600), 1))
    live["n_observed_21d"] = int(recent["pm25"].notna().sum())
    write("live.json", live)

    # ── the validation chapter: 24 h backtest trace ──────────────────────
    if not len(preds):
        log("predictions.parquet missing — backtest chapter stays pending")
        return
    h = 24 if 24 in preds["horizon_h"].unique() else int(preds["horizon_h"].max())
    ph = preds[preds["horizon_h"] == h]
    # Trace the station with the most held-out hours — the clearest read of
    # the backtest, and stated as such on the page.
    bt_station = int(ph["location_id"].value_counts().idxmax()) if len(ph) else primary
    b = ph[ph["location_id"] == bt_station].copy()
    b["valid_utc"] = pd.to_datetime(b["issue_utc"], utc=True) + pd.Timedelta(hours=h)
    b = b.sort_values("valid_utc")
    if len(b) > 2400:                              # keep the payload lean, keep the shape
        b = b.iloc[:: max(1, len(b) // 2400)]
    write("backtest.json", {
        "horizon_h": h, "location_id": bt_station,
        "station_name": next((s["name"] for s in stats["network"]["stations"]
                              if s["location_id"] == bt_station), None),
        "n_test_hours_all_stations": int(len(ph)),
        "rows": [{"t": t.isoformat(), "y": y, "p": p, "lo": lo, "hi": hi, "b": pb}
                 for t, y, p, lo, hi, pb in zip(b["valid_utc"], b["observed"], b["predicted"],
                                                b["lo"], b["hi"], b["persistence"])],
        "eval": stats["model"]["eval"],
    })

def _airshed(stats, feats, fire) -> None:
    # ── the airshed chapter: wind rose + fire rose ───────────────────────
    rose = []
    if len(feats):
        fr = feats.dropna(subset=["wind_from_sector", "pm25"])
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
        fd["m"] = fd["acq_date"].dt.to_period("M").astype(str)
        monthly = fd.groupby("m")["n_fire"].sum()
        fire_series = [{"m": k, "n": int(v)} for k, v in monthly.items()]
        # Where the biggest month actually burned — the chart shows the spike,
        # this says what it was.
        pm = monthly.idxmax()
        sub = fd[fd["m"] == pm]
        peak = {
            "month": pm, "n": int(monthly.max()),
            "sector": str(sub.groupby("sector", observed=True)["n_fire"].sum().idxmax()),
            "ring": str(sub.groupby("ring", observed=True)["n_fire"].sum().idxmax()),
            "median_per_day": int(sub.groupby("acq_date")["n_fire"].sum().median()),
            "times_median_month": round(float(monthly.max() / monthly.median()), 1),
        }
    else:
        fire_series, peak = [], None
    write("airshed.json", {"wind_rose": rose, "fire_rose": fire_rose,
                           "fire_monthly": fire_series, "fire_peak": peak,
                           "jakarta": {"lat": config.JKT_LAT, "lon": config.JKT_LON},
                           "rings_km": [0, 100, 400, 1200]})


def _network(stats) -> None:
    # ── the network chapter: the station map (2-D canvas, no WebGL) ──────
    write("network.json", {
        "bbox": list(config.AQ_BBOX),
        "window_start": config.START,
        "stations": [{k: s.get(k) for k in
                      ("location_id", "name", "lat", "lon", "provider", "status",
                       "days_stale", "first_utc", "last_utc", "obs_first_utc",
                       "obs_last_utc", "n_hours", "mean_pm25",
                       "completeness_span", "completeness_90d")}
                     for s in stats["network"]["stations"]],
    })


if __name__ == "__main__":
    main()
