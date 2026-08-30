"""Stage 3 — the gates.

Thresholds were fixed in ../README.md (§Gates) BEFORE the first model run, and
they are not moved to fit the answer. A gate that fails is published red on the
dashboard with its diagnosis, the way case C publishes G-C5.

  G-E1  24 h skill        model MAE beats persistence by >= 15%
  G-E2  skill everywhere  model beats persistence (skill > 0) at every horizon
  G-E3  episode recall    at 24 h, recall >= 0.50 at precision >= 0.40 for
                          hours with observed PM2.5 >= 55.5 ug/m3
  G-E4  network coverage  >= 3 Jabodetabek stations at >= 80% hourly
                          completeness over the trailing 90 days
  G-E5  uncertainty       80% prediction interval covers 72-88% of observations
  G-E6  physical drivers  the 24 h model's top-8 permutation importances
                          include >= 1 mixing term (BLH/ventilation) and
                          >= 1 wind term — otherwise it is an autoregression
                          wearing an air-quality costume
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd

import config

STATS = config.DATA_DIR / "stats.json"
MIXING = ("blh", "ventilation")
WINDY = ("wind_speed", "wind_from_sin", "wind_from_cos")


def log(msg: str) -> None:
    print(f"[validate] {msg}", flush=True)


def gate(gid, name, passed, value, threshold, detail):
    return {"id": gid, "name": name,
            "status": "pass" if passed is True else ("fail" if passed is False else "insufficient"),
            "value": value, "threshold": threshold, "detail": detail}


def coverage_90d(ground: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    end = ground["ts_utc"].max()
    win = ground[ground["ts_utc"] > end - pd.Timedelta(days=90)]
    cov = (win.groupby("location_id")["pm25"].count() / (90 * 24)).rename("completeness")
    cov = cov.reset_index()
    return cov, int((cov["completeness"] >= 0.80).sum())


def main() -> None:
    ev = pd.read_parquet(config.DATA_DIR / "model_eval.parquet").set_index("horizon_h")
    imp = pd.read_parquet(config.DATA_DIR / "importance.parquet")
    ground = pd.read_parquet(config.DATA_DIR / "ground_hourly.parquet")
    ground["ts_utc"] = pd.to_datetime(ground["ts_utc"], utc=True)
    stations = json.loads((config.DATA_DIR / "stations.json").read_text())
    meta = json.loads((config.DATA_DIR / "model_meta.json").read_text())
    fire_path = config.DATA_DIR / "fire_daily.parquet"
    fire = pd.read_parquet(fire_path) if fire_path.exists() else pd.DataFrame()

    gates = []

    # G-E1
    h = 24 if 24 in ev.index else int(ev.index.max())
    s24 = float(ev.loc[h, "skill_mae_vs_persistence"])
    gates.append(gate("G-E1", f"{h} h forecast beats persistence by >= 15% MAE",
                      s24 >= 0.15, round(s24, 4), 0.15,
                      f"MAE {ev.loc[h, 'model_mae']:.2f} vs persistence "
                      f"{ev.loc[h, 'persistence_mae']:.2f} \u00b5g/m\u00b3 on the held-out future "
                      f"({s24 * 100:+.1f}%). On RMSE the same comparison is "
                      f"{ev.loc[h, 'skill_rmse_vs_persistence'] * 100:+.1f}%."))

    # G-E2
    skills = ev["skill_mae_vs_persistence"]
    worst_h = int(skills.idxmin())
    gates.append(gate("G-E2", "Beats persistence at every horizon",
                      bool((skills > 0).all()), round(float(skills.min()), 4), 0.0,
                      "weakest horizon is h=%d at %+.1f%%; %s" % (
                          worst_h, skills.min() * 100,
                          "all horizons positive" if (skills > 0).all()
                          else "persistence wins at h=" + ",".join(
                              str(int(i)) for i in skills[skills <= 0].index))))

    # G-E3
    n_ep = int(ev.loc[h, "episode_n_obs"])
    rec, prec = float(ev.loc[h, "model_episode_recall"]), float(ev.loc[h, "model_episode_precision"])
    if n_ep < 30:
        ok = None
    else:
        ok = bool(rec >= 0.50 and prec >= 0.40)
    gates.append(gate("G-E3", f"Episode recall at {h} h (PM2.5 \u2265 {config.EPISODE_THRESHOLD} \u00b5g/m\u00b3)",
                      ok, {"recall": None if np.isnan(rec) else round(rec, 3),
                           "precision": None if np.isnan(prec) else round(prec, 3),
                           "n_episode_hours": n_ep},
                      {"recall": 0.50, "precision": 0.40},
                      f"{n_ep} episode hours in the held-out window. Model recall "
                      f"{rec:.3f} at precision {prec:.3f}; persistence recall "
                      f"{ev.loc[h, 'persistence_episode_recall']:.3f} at precision "
                      f"{ev.loc[h, 'persistence_episode_precision']:.3f}. "
                      f"The threshold is not moved to meet a near miss."))

    # G-E4
    cov, n_ok = coverage_90d(ground)
    live = [s for s in stations["stations"] if s["status"] == "live"]
    gates.append(gate("G-E4", "3+ stations at 80%+ hourly completeness over 90 days",
                      n_ok >= 3, int(n_ok), 3,
                      f"{len(stations['stations'])} PM2.5 stations exist in the bbox; "
                      f"{len(live)} have reported in the last 14 days; {n_ok} clear 80% "
                      f"completeness over the trailing 90 days"))

    # G-E5
    covp = float(ev.loc[h, "pi80_coverage"])
    gates.append(gate("G-E5", "80% prediction interval is calibrated (72-88% coverage)",
                      bool(0.72 <= covp <= 0.88), round(covp, 4), [0.72, 0.88],
                      f"empirical coverage {covp * 100:.1f}% at h={h}"))

    # G-E6
    top8 = list(imp.sort_values("importance", ascending=False).head(8)["feature"])
    has_mix = any(any(k in f for k in MIXING) for f in top8)
    has_wind = any(any(k in f for k in WINDY) for f in top8)
    gates.append(gate("G-E6", "Meteorology drives the model, not autoregression alone",
                      bool(has_mix and has_wind), {"top8": top8}, "mixing term + wind term in top 8",
                      f"mixing term present: {has_mix}; wind term present: {has_wind}"))

    # ── descriptive stats for the dashboard ──────────────────────────────
    obs = ground.set_index("ts_utc").groupby("location_id")["pm25"]
    per_station = []
    covmap = dict(zip(cov["location_id"], cov["completeness"]))
    for s in stations["stations"]:
        loc = s["location_id"]
        if loc not in ground["location_id"].values:
            per_station.append({**s, "n_hours": 0, "mean_pm25": None, "completeness_90d": None})
            continue
        g = ground[ground["location_id"] == loc]
        span_h = max(1.0, (g["ts_utc"].max() - g["ts_utc"].min()).total_seconds() / 3600)
        per_station.append({**s, "n_hours": int(len(g)),
                            # what we actually HOLD, as distinct from what the
                            # provider's metadata claims the station's life to be
                            "obs_first_utc": g["ts_utc"].min().isoformat(),
                            "obs_last_utc": g["ts_utc"].max().isoformat(),
                            "completeness_span": round(float(len(g) / span_h), 4),
                            "mean_pm25": round(float(g["pm25"].mean()), 2),
                            "p95_pm25": round(float(g["pm25"].quantile(0.95)), 2),
                            "completeness_90d": round(float(covmap.get(loc, 0.0)), 4)})

    hourly = ground.copy()
    hourly["hour_local"] = hourly["ts_utc"].dt.tz_convert("Asia/Jakarta").dt.hour
    hourly["month"] = hourly["ts_utc"].dt.tz_convert("Asia/Jakarta").dt.month

    stats = {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "gates": gates,
        "gates_passed": sum(g["status"] == "pass" for g in gates),
        "gates_total": len(gates),
        "vintage": {
            "ground_last_utc": str(ground["ts_utc"].max()),
            "ground_first_utc": str(ground["ts_utc"].min()),
            "fire_last_date": str(fire["acq_date"].max()) if len(fire) else None,
            "era5_split_cut_utc": meta["split_cut_utc"],
        },
        "network": {
            "n_stations_total": len(stations["stations"]),
            "n_live": len(live),
            "n_stale": sum(s["status"] == "stale" for s in stations["stations"]),
            "n_silent": sum(s["status"] == "silent" for s in stations["stations"]),
            "n_modelled": int(ground["location_id"].nunique()),
            "stations": per_station,
        },
        "observed": {
            "mean_pm25": round(float(ground["pm25"].mean()), 2),
            "p95_pm25": round(float(ground["pm25"].quantile(0.95)), 2),
            "hours_above_who_daily_15": round(float((ground["pm25"] > 15).mean()), 4),
            "hours_above_episode": round(float((ground["pm25"] >= config.EPISODE_THRESHOLD).mean()), 4),
            "diurnal": [{"hour": int(k), "pm25": round(float(v), 2)}
                        for k, v in hourly.groupby("hour_local")["pm25"].mean().items()],
            "monthly": [{"month": int(k), "pm25": round(float(v), 2)}
                        for k, v in hourly.groupby("month")["pm25"].mean().items()],
        },
        "model": {
            "n_features": meta["n_features"], "train_rows": meta["train_rows"],
            "test_rows": meta["test_rows"], "horizons": meta["horizons"],
            "eval": json.loads(ev.reset_index().to_json(orient="records")),
            "top_drivers": json.loads(imp.head(15).to_json(orient="records")),
        },
    }
    STATS.write_text(json.dumps(stats, indent=2, allow_nan=False))

    log(f"{stats['gates_passed']}/{stats['gates_total']} gates pass")
    for g in gates:
        log(f"  {g['status'].upper():>12}  {g['id']}  {g['name']}")
        log(f"                 {g['detail']}")


if __name__ == "__main__":
    main()
