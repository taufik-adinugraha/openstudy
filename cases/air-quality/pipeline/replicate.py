"""Independent re-derivation of the case's headline, against baselines it did not try.

This file exists because the published skill number is measured against exactly one
trivial baseline — persistence — and persistence has a known pathology for a strongly
diurnal pollutant: at a 24-hour lead it samples the same hour of the day, so it is
handed the entire diurnal cycle for free, and at a 12-hour lead it is handed the cycle
inverted. A skill curve measured against it therefore reports the baseline's phase as
if it were the model's competence.

Nothing here refits the model. Every alternative baseline is scored on precisely the
rows the published model was scored on (joined on location, issue time and horizon),
fitted on the training period only, so the comparison is like-for-like by construction.

What it computes:
  1  the published numbers, re-derived from predictions.parquet
  2  four additional trivial baselines, incl. a seasonal-diurnal climatology and the
     standard meteorological "persist the anomaly" rule
  3  a day-block bootstrap confidence interval on the 24 h skill, which the case
     publishes without one
  4  the autocorrelation function of hourly PM2.5, which explains (2)
  5  the composition of the evaluation panel through the held-out window
  6  per-station skill, and skill on the sub-panel that survives the whole window
  7  a co-location experiment: two independently-operated sensors at one address,
     which measures the observing system's own noise floor
  8  threshold arithmetic — 24-hour guideline values applied to hourly readings

Run on the server, where the data is:
    .venv/bin/python pipeline/replicate.py
"""

from __future__ import annotations

import json
import numpy as np
import pandas as pd

import config

OUT = config.DATA_DIR / "replication.json"
RNG = np.random.default_rng(20260831)
JKT = "Asia/Jakarta"


def log(m: str) -> None:
    print(f"[replicate] {m}", flush=True)


def mae(y, p):
    return float(np.mean(np.abs(np.asarray(p) - np.asarray(y))))


def rmse(y, p):
    return float(np.sqrt(np.mean((np.asarray(p) - np.asarray(y)) ** 2)))


# ─────────────────────────────────────────────────────────────────────────────
def load():
    feats = pd.read_parquet(config.DATA_DIR / "features.parquet")
    preds = pd.read_parquet(config.DATA_DIR / "predictions.parquet")
    meta = json.loads((config.DATA_DIR / "model_meta.json").read_text())
    stations = json.loads((config.DATA_DIR / "stations.json").read_text())["stations"]
    feats["ts_utc"] = pd.to_datetime(feats["ts_utc"], utc=True)
    preds["issue_utc"] = pd.to_datetime(preds["issue_utc"], utc=True)
    cut = pd.Timestamp(meta["split_cut_utc"])
    if cut.tzinfo is None:
        cut = cut.tz_localize("UTC")
    return feats, preds, cut, {s["location_id"]: s for s in stations}


def build_baselines(feats: pd.DataFrame, preds: pd.DataFrame, cut: pd.Timestamp) -> pd.DataFrame:
    """Attach every alternative baseline to the exact rows the model was scored on."""
    train = feats[feats["ts_utc"] < cut]

    # Climatologies, fitted on TRAIN ONLY. Two of them: the one the case already
    # publishes (station x hour-of-day) and the one it does not (station x month x
    # hour-of-day), which is the same estimator given the season it obviously needs.
    tl = train["ts_utc"].dt.tz_convert(JKT)
    tr = train.assign(month=tl.dt.month)
    clim_h = tr.groupby(["location_id", "hour_local"])["pm25"].mean()
    clim_mh = tr.groupby(["location_id", "month", "hour_local"])["pm25"].mean()
    clim_m_only = tr.groupby(["location_id", "month"])["pm25"].mean()
    grand = float(train["pm25"].mean())

    # Issue-time state the baselines are allowed to see.
    state = feats[["location_id", "ts_utc", "pm25", "pm25_roll24", "hour_local"]].rename(
        columns={"ts_utc": "issue_utc", "pm25": "obs_now"})

    df = preds.merge(state, on=["location_id", "issue_utc"], how="left")
    tgt = df["issue_utc"] + pd.to_timedelta(df["horizon_h"], unit="h")
    tl_t = tgt.dt.tz_convert(JKT)
    df["tgt_hour"] = tl_t.dt.hour
    df["tgt_month"] = tl_t.dt.month
    tl_i = df["issue_utc"].dt.tz_convert(JKT)
    df["iss_hour"] = tl_i.dt.hour
    df["iss_month"] = tl_i.dt.month
    df["day"] = tl_i.dt.floor("D")

    def lookup(series, keys):
        v = series.reindex(pd.MultiIndex.from_arrays(keys)).to_numpy()
        return np.where(np.isnan(v), grand, v)

    df["b_clim_hour"] = lookup(clim_h, [df["location_id"], df["tgt_hour"]])
    df["b_clim_seas"] = lookup(clim_mh, [df["location_id"], df["tgt_month"], df["tgt_hour"]])
    df["b_clim_month"] = lookup(clim_m_only, [df["location_id"], df["tgt_month"]])
    df["b_persist"] = df["persistence"].to_numpy()
    df["b_roll24"] = np.where(df["pm25_roll24"].notna(), df["pm25_roll24"], df["b_persist"])

    # "Persist the anomaly": the standard skilled trivial forecast in meteorology.
    # Carry forward the departure from the seasonal-diurnal norm, not the level.
    clim_at_issue = lookup(clim_mh, [df["location_id"], df["iss_month"], df["iss_hour"]])
    df["b_anom"] = np.clip(df["b_clim_seas"] + (df["obs_now"] - clim_at_issue), 0, None)
    return df


BASELINES = [
    ("b_persist", "Persistence", "carry the last reading forward"),
    ("b_roll24", "Last 24 h mean", "carry the trailing daily mean forward"),
    ("b_clim_hour", "Diurnal climatology", "station mean for this hour of day"),
    ("b_clim_month", "Seasonal climatology", "station mean for this month"),
    ("b_clim_seas", "Seasonal-diurnal climatology", "station mean for this month and hour"),
    ("b_anom", "Persist the anomaly", "seasonal-diurnal norm plus today's departure"),
]


def per_horizon(df: pd.DataFrame) -> list[dict]:
    rows = []
    for h, g in df.groupby("horizon_h"):
        y, p = g["observed"].to_numpy(), g["predicted"].to_numpy()
        r = {"horizon_h": int(h), "n": int(len(g)),
             "model_mae": mae(y, p), "model_rmse": rmse(y, p),
             "baselines": []}
        for col, name, spec in BASELINES:
            b = g[col].to_numpy()
            r["baselines"].append({
                "key": col, "name": name, "spec": spec,
                "mae": mae(y, b), "rmse": rmse(y, b),
                "skill_mae": 1 - r["model_mae"] / mae(y, b),
                "skill_rmse": 1 - r["model_rmse"] / rmse(y, b)})
        best = min(r["baselines"], key=lambda b: b["mae"])
        r["best_baseline"] = best["key"]
        r["best_baseline_name"] = best["name"]
        r["best_baseline_mae"] = best["mae"]
        r["skill_vs_best"] = 1 - r["model_mae"] / best["mae"]
        r["skill_vs_persistence"] = 1 - r["model_mae"] / mae(y, g["b_persist"].to_numpy())
        rows.append(r)
    return sorted(rows, key=lambda r: r["horizon_h"])


def bootstrap_skill(df: pd.DataFrame, h: int, n_boot: int = 2000) -> dict:
    """Day-block bootstrap. Hourly PM2.5 is autocorrelated for days, so resampling
    hours independently would understate the interval by a large factor. Days are
    resampled whole, within station."""
    g = df[df["horizon_h"] == h]
    blocks = list(g.groupby(["location_id", "day"]).indices.values())
    y = g["observed"].to_numpy()
    p = g["predicted"].to_numpy()
    cols = {c: g[c].to_numpy() for c, _, _ in BASELINES}
    out = {"h": int(h), "n_blocks": len(blocks)}
    per_block = {"m": [np.abs(p[b] - y[b]).sum() for b in blocks],
                 "n": [len(b) for b in blocks]}
    for c in cols:
        per_block[c] = [np.abs(cols[c][b] - y[b]).sum() for b in blocks]
    per_block = {k: np.asarray(v, dtype=float) for k, v in per_block.items()}
    idx = RNG.integers(0, len(blocks), size=(n_boot, len(blocks)))
    n = per_block["n"][idx].sum(axis=1)
    mm = per_block["m"][idx].sum(axis=1) / n
    for c in cols:
        bb = per_block[c][idx].sum(axis=1) / n
        s = 1 - mm / bb
        out[c] = {"skill": float(1 - np.abs(p - y).mean() / np.abs(cols[c] - y).mean()),
                  "lo": float(np.percentile(s, 2.5)), "hi": float(np.percentile(s, 97.5)),
                  "p_le_0": float(np.mean(s <= 0)), "p_lt_15": float(np.mean(s < 0.15))}
    return out


def acf(feats: pd.DataFrame, max_lag: int = 72) -> list[dict]:
    """Autocorrelation of hourly PM2.5, pooled within station on a complete hourly grid."""
    out = []
    for lag in range(1, max_lag + 1):
        a, b = [], []
        for _, g in feats.groupby("location_id"):
            s = g.sort_values("ts_utc")["pm25"].to_numpy()
            x, y = s[:-lag], s[lag:]
            m = ~np.isnan(x) & ~np.isnan(y)
            a.append(x[m]); b.append(y[m])
        x, y = np.concatenate(a), np.concatenate(b)
        out.append({"lag": lag, "r": float(np.corrcoef(x, y)[0, 1]), "n": int(len(x))})
    return out


def panel(df: pd.DataFrame, feats: pd.DataFrame, cut, names) -> dict:
    """How the evaluation panel changes shape across the held-out window."""
    g24 = df[df["horizon_h"] == 24].copy()
    g24["ym"] = g24["issue_utc"].dt.tz_convert(JKT).dt.strftime("%Y-%m")
    months = []
    for ym, s in g24.groupby("ym"):
        st = sorted(s["location_id"].unique())
        months.append({"ym": ym, "n_rows": int(len(s)), "n_stations": len(st),
                       "stations": [int(x) for x in st],
                       "model_mae": mae(s["observed"], s["predicted"]),
                       "persist_mae": mae(s["observed"], s["b_persist"]),
                       "clim_seas_mae": mae(s["observed"], s["b_clim_seas"])})
    months.sort(key=lambda m: m["ym"])

    # Europe's forecast acceptance criterion (Vitali et al. 2023, GMD): the
    # Modelling Quality Indicator MQI_f = RMSE(forecast) / RMSE(persistence), which
    # must be <= 1 at 90% or more of monitoring stations. It is a per-station test,
    # not a pooled one, precisely so that a good average cannot hide a bad site.
    by_station = []
    for loc, s in g24.groupby("location_id"):
        by_station.append({
            "location_id": int(loc), "name": names.get(loc, {}).get("name", str(loc)),
            "provider": names.get(loc, {}).get("provider", "—"),
            "n": int(len(s)), "share": float(len(s) / len(g24)),
            "obs_mean": float(s["observed"].mean()),
            "model_mae": mae(s["observed"], s["predicted"]),
            "persist_mae": mae(s["observed"], s["b_persist"]),
            "clim_seas_mae": mae(s["observed"], s["b_clim_seas"]),
            "mqi_f": rmse(s["observed"], s["predicted"]) / rmse(s["observed"], s["b_persist"]),
            "skill_persist": 1 - mae(s["observed"], s["predicted"]) / mae(s["observed"], s["b_persist"]),
            "skill_clim": 1 - mae(s["observed"], s["predicted"]) / mae(s["observed"], s["b_clim_seas"]),
            "first": str(s["issue_utc"].min())[:10], "last": str(s["issue_utc"].max())[:10]})
    by_station.sort(key=lambda r: -r["n"])
    n_ok = sum(1 for r in by_station if r["mqi_f"] <= 1.0)
    fmqo = {"n_stations": len(by_station), "n_pass": n_ok,
            "share_pass": n_ok / len(by_station), "threshold": 0.90,
            "passes": n_ok / len(by_station) >= 0.90,
            "worst": max(by_station, key=lambda r: r["mqi_f"])["name"],
            "worst_mqi": max(r["mqi_f"] for r in by_station)}

    # The sub-panel that is present in the first AND last month of the window —
    # the only stations on which a skill comparison is a like-for-like panel.
    first_set, last_set = set(months[0]["stations"]), set(months[-1]["stations"])
    survivors = sorted(first_set & last_set)
    sub = g24[g24["location_id"].isin(survivors)]
    balanced = None
    if len(sub) > 200:
        balanced = {"stations": [int(x) for x in survivors], "n": int(len(sub)),
                    "model_mae": mae(sub["observed"], sub["predicted"]),
                    "persist_mae": mae(sub["observed"], sub["b_persist"]),
                    "clim_seas_mae": mae(sub["observed"], sub["b_clim_seas"])}
        balanced["skill_persist"] = 1 - balanced["model_mae"] / balanced["persist_mae"]
        balanced["skill_clim"] = 1 - balanced["model_mae"] / balanced["clim_seas_mae"]

    # Completeness of every station over the whole record, by month.
    f = feats.copy()
    f["ym"] = f["ts_utc"].dt.tz_convert(JKT).dt.strftime("%Y-%m")
    comp = (f.groupby(["location_id", "ym"])["pm25"]
              .agg(obs="count", hrs="size").reset_index())
    comp["completeness"] = comp["obs"] / comp["hrs"]
    cov = [{"ym": ym, "n_any": int((s["obs"] > 0).sum()),
            "n_80": int((s["completeness"] >= 0.8).sum()),
            "n_50": int((s["completeness"] >= 0.5).sum()),
            "obs_hours": int(s["obs"].sum())}
           for ym, s in comp.groupby("ym")]
    cov.sort(key=lambda r: r["ym"])
    return {"months": months, "by_station": by_station, "balanced": balanced,
            "coverage": cov, "cut": str(cut), "fmqo": fmqo,
            "n_test_rows": int(len(g24)),
            "share_before_2026": float((g24["issue_utc"] < pd.Timestamp("2026-01-01", tz="UTC")).mean())}


def episodes(df: pd.DataFrame) -> dict:
    """What the forecast can and cannot say about the hours that matter.

    The case grades episode skill at one threshold, 55.5 ug/m3. The US AQI has three
    more above it, and those are the ones a city acts on. A model trained on log1p and
    scored on absolute error regresses to the middle, so the question is not only how
    often it is right but whether it is *capable* of the call at all.
    """
    LADDER = [(35.5, "Unhealthy for sensitive groups"), (55.5, "Unhealthy"),
              (125.5, "Very unhealthy"), (225.5, "Hazardous")]
    g = df[df["horizon_h"] == 24]
    y, p = g["observed"].to_numpy(), g["predicted"].to_numpy()
    rungs = []
    for thr, label in LADDER:
        obs, hit = y >= thr, p >= thr
        tp = int((obs & hit).sum())
        rungs.append({"threshold": thr, "label": label,
                      "n_observed": int(obs.sum()), "n_predicted": int(hit.sum()),
                      "hits": tp,
                      "recall": float(tp / obs.sum()) if obs.sum() else None,
                      "precision": float(tp / hit.sum()) if hit.sum() else None})
    ep = g[g["observed"] >= 55.5]
    cl = g[g["observed"] < 55.5]
    ceiling = df.groupby("horizon_h").agg(
        pred_max=("predicted", "max"), obs_max=("observed", "max"),
        pred_p99=("predicted", lambda s: s.quantile(0.99)),
        obs_p99=("observed", lambda s: s.quantile(0.99))).reset_index()
    return {
        "ladder": rungs,
        "episode": {"n": int(len(ep)), "mae": mae(ep["observed"], ep["predicted"]),
                    "persist_mae": mae(ep["observed"], ep["b_persist"]),
                    "bias": float((ep["predicted"] - ep["observed"]).mean())},
        "clean": {"n": int(len(cl)), "mae": mae(cl["observed"], cl["predicted"]),
                  "persist_mae": mae(cl["observed"], cl["b_persist"]),
                  "bias": float((cl["predicted"] - cl["observed"]).mean())},
        "ceiling": [{"horizon_h": int(r.horizon_h), "pred_max": float(r.pred_max),
                     "obs_max": float(r.obs_max), "pred_p99": float(r.pred_p99),
                     "obs_p99": float(r.obs_p99)} for r in ceiling.itertuples()],
    }


def unseen(df: pd.DataFrame, feats: pd.DataFrame, cut, names) -> dict:
    """Station identity is the model's single strongest feature. That predicts a
    specific failure — a station the model never trained on should be much weaker —
    and the panel happens to contain a natural experiment for it."""
    obs = feats[feats["pm25"].notna()]
    tr = obs[obs["ts_utc"] < cut].groupby("location_id").size()
    g = df[df["horizon_h"] == 24]
    rows = []
    for loc, s in g.groupby("location_id"):
        n_train = int(tr.get(loc, 0))
        rows.append({
            "location_id": int(loc), "name": names.get(loc, {}).get("name", str(loc)),
            "provider": names.get(loc, {}).get("provider", "—"),
            "n_train": n_train, "n_test": int(len(s)),
            "skill_persist": 1 - mae(s["observed"], s["predicted"]) / mae(s["observed"], s["b_persist"]),
            "pi80": float(((s["observed"] >= s["lo"]) & (s["observed"] <= s["hi"])).mean()),
            "obs_mean": float(s["observed"].mean())})
    rows.sort(key=lambda r: -r["n_test"])
    big = [r for r in rows if r["n_test"] >= 1000]
    seen = [r for r in big if r["n_train"] > 0]
    fresh = [r for r in big if r["n_train"] == 0]
    return {"rows": rows,
            "seen_mean_skill": float(np.mean([r["skill_persist"] for r in seen])) if seen else None,
            "seen_mean_pi80": float(np.mean([r["pi80"] for r in seen])) if seen else None,
            "fresh": fresh,
            "min_rows": 1000}


def provider_grade(feats: pd.DataFrame, names) -> list[dict]:
    """OpenAQ pools reference-grade regulatory monitors with consumer-grade optical
    sensors under one field name. Grouped by operator, the level they report differs
    by more than any spatial gradient inside a 60 km city."""
    o = feats[feats["pm25"].notna()].copy()
    o["provider"] = o["location_id"].map(lambda k: names.get(k, {}).get("provider", "—"))
    rows = []
    for prov, s in o.groupby("provider"):
        rows.append({"provider": str(prov), "n_stations": int(s["location_id"].nunique()),
                     "n_hours": int(len(s)), "mean": float(s["pm25"].mean()),
                     "median": float(s["pm25"].median())})
    rows.sort(key=lambda r: -r["mean"])
    ref = o[o["provider"] == "AirNow"]         # the two US-Embassy regulatory monitors
    return {"rows": rows,
            "panel_mean": float(o["pm25"].mean()),
            "reference_mean": float(ref["pm25"].mean()) if len(ref) else None,
            "reference_n": int(len(ref)),
            "consumer_mean": float(o[o["provider"] == "AirGradient"]["pm25"].mean()),
            "consumer_n": int((o["provider"] == "AirGradient").sum())}


def colocation(feats: pd.DataFrame, names) -> dict:
    """Two instruments at one address disagree by how much?

    Several OpenAQ registrations in this bbox sit within ~50 m of each other but are
    run by different organisations on different hardware. Where two of them report the
    same hours, the difference between them is a lower bound on the observing system's
    own error — no forecast can be scored below it.
    """
    st = pd.DataFrame([{"location_id": k, "lat": v["lat"], "lon": v["lon"],
                        "name": v["name"], "provider": v["provider"]}
                       for k, v in names.items()])
    obs = feats[feats["pm25"].notna()][["location_id", "ts_utc", "pm25"]]
    have = set(obs["location_id"].unique())
    pairs = []
    ids = sorted(have)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ra = st[st["location_id"] == a].iloc[0]
            rb = st[st["location_id"] == b].iloc[0]
            # ~111 km per degree; 0.01 deg is about 1.1 km.
            dkm = float(np.hypot((ra["lat"] - rb["lat"]) * 111.0,
                                 (ra["lon"] - rb["lon"]) * 111.0 * np.cos(np.radians(ra["lat"]))))
            if dkm > 3.0:
                continue
            m = (obs[obs["location_id"] == a].set_index("ts_utc")["pm25"]
                 .to_frame("a")
                 .join(obs[obs["location_id"] == b].set_index("ts_utc")["pm25"].to_frame("b"),
                       how="inner").dropna())
            if len(m) < 500:
                continue
            d = m["a"] - m["b"]
            ym = m.index.tz_convert(JKT).strftime("%Y-%m")
            monthly = [{"ym": k, "a": float(v["a"].mean()), "b": float(v["b"].mean()),
                        "n": int(len(v))} for k, v in m.groupby(ym)]
            monthly.sort(key=lambda r: r["ym"])
            # Agreement is not constant. Split the overlap in half by time and see.
            mid = m.index[len(m) // 2]
            halves = []
            for lab, sub in (("first half", m[m.index < mid]), ("second half", m[m.index >= mid])):
                dd = sub["a"] - sub["b"]
                halves.append({"half": lab, "n": int(len(sub)),
                               "first": str(sub.index.min())[:10], "last": str(sub.index.max())[:10],
                               "a_mean": float(sub["a"].mean()), "b_mean": float(sub["b"].mean()),
                               "mae": float(dd.abs().mean()), "bias": float(dd.mean()),
                               "r": float(sub["a"].corr(sub["b"]))})
            pairs.append({
                "a": int(a), "b": int(b), "a_name": ra["name"], "b_name": rb["name"],
                "a_provider": ra["provider"], "b_provider": rb["provider"],
                "km": round(dkm, 3), "m": round(dkm * 1000), "n": int(len(m)),
                "a_mean": float(m["a"].mean()), "b_mean": float(m["b"].mean()),
                "mae": float(d.abs().mean()), "rmse": float(np.sqrt((d ** 2).mean())),
                "bias": float(d.mean()), "r": float(m["a"].corr(m["b"])),
                "first": str(m.index.min())[:10], "last": str(m.index.max())[:10],
                "monthly": monthly, "halves": halves,
                "scatter": [[round(float(x), 1), round(float(y), 1)]
                            for x, y in m.sample(min(900, len(m)), random_state=1).to_numpy()]})
    pairs.sort(key=lambda p: p["km"])

    # The full monthly life of every station that ever reported, so the reader can see
    # what happened to the last one standing after its neighbour went dark.
    o = feats[feats["pm25"].notna()].copy()
    o["ym"] = o["ts_utc"].dt.tz_convert(JKT).dt.strftime("%Y-%m")
    series = {}
    for loc, s in o.groupby("location_id"):
        series[str(int(loc))] = {
            "name": names.get(loc, {}).get("name", str(loc)),
            "provider": names.get(loc, {}).get("provider", "—"),
            "monthly": [{"ym": k, "mean": float(v.mean()), "n": int(len(v))}
                        for k, v in s.groupby("ym")["pm25"]]}
    return {"pairs": pairs, "series": series}


def thresholds(feats: pd.DataFrame, names) -> dict:
    """The case grades hourly readings against 24-hour guideline values. Both the
    WHO 2021 daily guideline (15 ug/m3) and the US EPA 'Unhealthy' breakpoint
    (55.5 ug/m3) are defined on a 24-hour mean. Applied hour-by-hour they answer a
    different question. Both versions, so the reader can see the size of the gap."""
    o = feats[feats["pm25"].notna()].copy()
    o["day"] = o["ts_utc"].dt.tz_convert(JKT).dt.floor("D")
    daily = o.groupby(["location_id", "day"]).agg(pm25=("pm25", "mean"), n=("pm25", "size"))
    full = daily[daily["n"] >= 18]                      # a day needs 18 h to be a day
    per_station = [{"location_id": int(k), "name": names.get(k, {}).get("name", str(k)),
                    "provider": names.get(k, {}).get("provider", "—"),
                    "n": int(v.size), "mean": float(v.mean()), "median": float(v.median()),
                    "p95": float(v.quantile(0.95))}
                   for k, v in o.groupby("location_id")["pm25"]]
    per_station.sort(key=lambda r: -r["mean"])
    station_means = np.array([r["mean"] for r in per_station])
    return {
        "hours_over_who15": float((o["pm25"] >= 15).mean()),
        "days_over_who15": float((full["pm25"] >= 15).mean()),
        "hours_over_ep": float((o["pm25"] >= config.EPISODE_THRESHOLD).mean()),
        "days_over_ep": float((full["pm25"] >= config.EPISODE_THRESHOLD).mean()),
        "n_days": int(len(full)),
        "station_hour_mean": float(o["pm25"].mean()),
        "station_balanced_mean": float(station_means.mean()),
        "station_mean_min": float(station_means.min()),
        "station_mean_max": float(station_means.max()),
        "per_station": per_station,
        "episode_threshold": config.EPISODE_THRESHOLD}


def fire_tail(days: int = 400) -> dict:
    """The airshed series ends on a spike an order of magnitude above anything else
    in the record. Whatever it is, it lands inside the held-out window."""
    f = pd.read_parquet(config.DATA_DIR / "fire_daily.parquet")
    f["date"] = pd.to_datetime(f["acq_date"])
    daily = f.groupby("date")["n_fire"].sum().sort_index()
    monthly = daily.resample("MS").sum()
    tail = daily.tail(days)
    return {"daily": [{"d": str(d)[:10], "n": int(v)} for d, v in tail.items()],
            "monthly": [{"m": str(m)[:7], "n": int(v)} for m, v in monthly.items()],
            "median_month": float(monthly.median()),
            "peak_month": str(monthly.idxmax())[:7], "peak_n": int(monthly.max()),
            "ratio_to_median": float(monthly.max() / max(monthly.median(), 1)),
            "prev_max_month": str(monthly.drop(monthly.idxmax()).idxmax())[:7],
            "prev_max_n": int(monthly.drop(monthly.idxmax()).max())}


def main() -> None:
    feats, preds, cut, names = load()
    log(f"features {feats.shape} · predictions {preds.shape} · cut {cut}")
    df = build_baselines(feats, preds, cut)
    log(f"baselines attached to {len(df):,} scored rows")

    horizons = per_horizon(df)
    for r in horizons:
        log(f"h={r['horizon_h']:>3}  model {r['model_mae']:6.2f}  "
            f"best trivial {r['best_baseline_mae']:6.2f} ({r['best_baseline_name']})  "
            f"skill vs persistence {r['skill_vs_persistence'] * 100:+5.1f}%  "
            f"vs best {r['skill_vs_best'] * 100:+5.1f}%")

    boot = bootstrap_skill(df, 24)
    log(f"24 h bootstrap: vs persistence {boot['b_persist']['skill'] * 100:+.1f}% "
        f"[{boot['b_persist']['lo'] * 100:+.1f}, {boot['b_persist']['hi'] * 100:+.1f}]  "
        f"vs seasonal-diurnal {boot['b_clim_seas']['skill'] * 100:+.1f}% "
        f"[{boot['b_clim_seas']['lo'] * 100:+.1f}, {boot['b_clim_seas']['hi'] * 100:+.1f}]")

    out = {
        "generated_utc": pd.Timestamp.now("UTC").strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "cut": str(cut),
        "horizons": horizons,
        "bootstrap24": boot,
        "acf": acf(feats),
        "panel": panel(df, feats, cut, names),
        "episodes": episodes(df),
        "unseen": unseen(df, feats, cut, names),
        "providers": provider_grade(feats, names),
        "colocation": colocation(feats, names),
        "thresholds": thresholds(feats, names),
        "fire": fire_tail(),
    }
    OUT.write_text(json.dumps(out, indent=1))
    log(f"-> {OUT} — now run pipeline/article.py to fold it into the web data layer")


if __name__ == "__main__":
    main()
