"""Stage 2b — direct multi-horizon gradient boosting, judged against persistence.

One model per horizon (direct, not recursive: recursive rollout would compound
its own error and hide it). Trained on log1p(PM2.5) because the distribution is
strongly right-skewed and the episodes we care about live in the tail.

Two things this file refuses to do, because both would flatter the result:
  * random train/test splits — the split is by TIME, at a single cut, and every
    reported number comes from the held-out future;
  * grading against nothing — every horizon is reported next to persistence
    (carry the last observation forward) and diurnal climatology. If the model
    loses to persistence at a horizon, that is what gets published.

Uncertainty is quantile regression (10th/90th) rather than a residual
assumption, so the band is allowed to be asymmetric — which, for pollution,
it always is.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

import config

FEATS_PATH = config.DATA_DIR / "features.parquet"
EVAL_OUT = config.DATA_DIR / "model_eval.parquet"
PRED_OUT = config.DATA_DIR / "predictions.parquet"
IMP_OUT = config.DATA_DIR / "importance.parquet"
FC_OUT = config.DATA_DIR / "forecast.parquet"

# location_id stays in the frame as an identifier but never as a feature:
# it is a 7-digit code, and a tree splitting it on magnitude would be reading
# meaning into an arbitrary registry number. station_idx replaces it as a
# declared categorical.
DROP = {"ts_utc", "persistence", "wind_from_sector", "date", "n_sub", "location_id"}


def log(msg: str) -> None:
    print(f"[model] {msg}", flush=True)


def feature_columns(df: pd.DataFrame) -> list[str]:
    targets = {f"y_h{h}" for h in config.HORIZONS}
    return [c for c in df.columns
            if c not in DROP and c not in targets and pd.api.types.is_numeric_dtype(df[c])]


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["wind_from_sin"] = np.sin(np.radians(df["wind_from_deg"]))
    df["wind_from_cos"] = np.cos(np.radians(df["wind_from_deg"]))
    df["station_idx"] = df["location_id"].astype("category").cat.codes.astype("int32")
    return df.drop(columns=["wind_from_deg"])


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    err = p - y
    return {"mae": float(np.mean(np.abs(err))),
            "rmse": float(np.sqrt(np.mean(err**2))),
            "bias": float(np.mean(err)),
            "n": int(len(y))}


def fit_horizon(train: pd.DataFrame, test: pd.DataFrame, feats: list[str], h: int,
                clim: pd.Series) -> tuple[dict, pd.DataFrame, object, list[str]]:
    ycol = f"y_h{h}"
    tr = train.dropna(subset=[ycol, "pm25"])
    te = test.dropna(subset=[ycol, "pm25"])
    if len(tr) < 500 or len(te) < 100:
        log(f"h={h}: too few rows (train {len(tr)}, test {len(te)}) — skipped")
        return {}, pd.DataFrame(), None, []

    # A column that is entirely missing or constant in this horizon's training
    # slice carries no information and breaks the histogram binner outright.
    usable = [c for c in feats if tr[c].notna().any() and tr[c].nunique(dropna=True) > 1]
    dropped = sorted(set(feats) - set(usable))
    if dropped:
        log(f"h={h}: dropping {len(dropped)} empty/constant features ({', '.join(dropped[:6])}"
            f"{'…' if len(dropped) > 6 else ''})")
    feats = usable

    Xtr, ytr = tr[feats], np.log1p(tr[ycol].clip(lower=0))
    Xte, yte = te[feats], te[ycol].to_numpy()

    cat = [i for i, c in enumerate(feats) if c == "station_idx"]
    common = dict(max_iter=400, learning_rate=0.06, max_depth=None, max_leaf_nodes=31,
                  min_samples_leaf=40, l2_regularization=1.0, early_stopping=True,
                  validation_fraction=0.15, random_state=42,
                  categorical_features=cat or None)
    mid = HistGradientBoostingRegressor(**common).fit(Xtr, ytr)
    pred = np.expm1(mid.predict(Xte)).clip(min=0)

    bands = {}
    for q, name in ((0.1, "lo"), (0.9, "hi")):
        m = HistGradientBoostingRegressor(loss="quantile", quantile=q, **common).fit(Xtr, ytr)
        bands[name] = np.expm1(m.predict(Xte)).clip(min=0)

    persist = te["pm25"].to_numpy()
    climo = clim.reindex(
        pd.MultiIndex.from_arrays([te["location_id"], (te["hour_local"] + h) % 24])
    ).to_numpy()
    climo = np.where(np.isnan(climo), np.nanmean(persist), climo)

    row = {"horizon_h": h}
    for name, p in (("model", pred), ("persistence", persist), ("climatology", climo)):
        for k, v in metrics(yte, p).items():
            row[f"{name}_{k}"] = v
    row["skill_mae_vs_persistence"] = 1 - row["model_mae"] / row["persistence_mae"]
    row["skill_rmse_vs_persistence"] = 1 - row["model_rmse"] / row["persistence_rmse"]
    row["skill_mae_vs_climatology"] = 1 - row["model_mae"] / row["climatology_mae"]

    # Episode skill: can it see the bad air coming?
    obs_ep = yte >= config.EPISODE_THRESHOLD
    for name, p in (("model", pred), ("persistence", persist)):
        hit = p >= config.EPISODE_THRESHOLD
        tp = int(np.sum(hit & obs_ep))
        row[f"{name}_episode_recall"] = float(tp / obs_ep.sum()) if obs_ep.sum() else float("nan")
        row[f"{name}_episode_precision"] = float(tp / hit.sum()) if hit.sum() else float("nan")
    row["episode_n_obs"] = int(obs_ep.sum())

    inside = (yte >= bands["lo"]) & (yte <= bands["hi"])
    row["pi80_coverage"] = float(np.mean(inside))

    preds = pd.DataFrame({
        "location_id": te["location_id"].to_numpy(),
        "issue_utc": te["ts_utc"].to_numpy(),
        "horizon_h": h,
        "observed": yte,
        "predicted": pred,
        "lo": bands["lo"],
        "hi": bands["hi"],
        "persistence": persist,
    })
    return row, preds, mid, feats


def main() -> None:
    df = prepare(pd.read_parquet(FEATS_PATH))
    feats = feature_columns(df)
    df = df.sort_values("ts_utc")

    # ONE cut, by time — but placed at the quantile of OBSERVED hours rather
    # than of the calendar. Splitting the calendar would hand the test set
    # whatever months the network happened to be dead in, which is a lottery,
    # not a held-out sample. Nothing after the cut is ever seen in training.
    obs_ts = df.loc[df["pm25"].notna(), "ts_utc"].sort_values()
    cut = obs_ts.iloc[int(len(obs_ts) * (1 - config.TEST_FRACTION))]
    train, test = df[df["ts_utc"] < cut], df[df["ts_utc"] >= cut]
    log(f"time split at {cut} (75th percentile of observed hours) — "
        f"train {len(train):,} rows / {int(train['pm25'].notna().sum()):,} observed "
        f"across {train.loc[train['pm25'].notna(), 'location_id'].nunique()} stations; "
        f"test {len(test):,} rows / {int(test['pm25'].notna().sum()):,} observed "
        f"across {test.loc[test['pm25'].notna(), 'location_id'].nunique()} stations")
    log(f"{len(feats)} features")

    # Diurnal climatology baseline, fitted on TRAIN only.
    clim = train.groupby(["location_id", "hour_local"])["pm25"].mean()

    rows, preds, models = [], [], {}
    for h in config.HORIZONS:
        row, p, m, used = fit_horizon(train, test, feats, h, clim)
        if not row:
            continue
        rows.append(row)
        preds.append(p)
        models[h] = (m, used)
        log(f"h={h:>3}: MAE {row['model_mae']:6.2f} vs persistence {row['persistence_mae']:6.2f} "
            f"({row['skill_mae_vs_persistence'] * 100:+5.1f}%)  RMSE {row['model_rmse']:6.2f} "
            f"vs {row['persistence_rmse']:6.2f}  PI80 cov {row['pi80_coverage'] * 100:.0f}%")

    if not rows:
        log("no horizon could be fitted — nothing written")
        return
    ev = pd.DataFrame(rows)
    ev.to_parquet(EVAL_OUT, index=False)
    pd.concat(preds, ignore_index=True).to_parquet(PRED_OUT, index=False)

    # Permutation importance at 24 h — what the model actually leans on.
    h_imp = 24 if 24 in models else max(models)
    te = test.dropna(subset=[f"y_h{h_imp}", "pm25"])
    sample = te.sample(min(3000, len(te)), random_state=0)
    mdl, used = models[h_imp]
    r = permutation_importance(mdl, sample[used], np.log1p(sample[f"y_h{h_imp}"].clip(lower=0)),
                               n_repeats=5, random_state=0, scoring="neg_mean_absolute_error")
    imp = (pd.DataFrame({"feature": used, "importance": r.importances_mean, "sd": r.importances_std})
           .sort_values("importance", ascending=False))
    imp["horizon_h"] = h_imp
    imp.to_parquet(IMP_OUT, index=False)
    log(f"top drivers at h={h_imp}: " + ", ".join(imp.head(6)["feature"]))

    # Operational forecast from the most recent complete issue time per station.
    fc_rows = []
    for loc, g in df.groupby("location_id"):
        g = g.dropna(subset=["pm25", "blh", "pm25_lag24"])
        if g.empty:
            continue
        last = g.iloc[[-1]]
        for h, (m, used) in models.items():
            if m is None:
                continue
            v = float(np.expm1(m.predict(last[used])[0]))
            fc_rows.append({"location_id": loc,
                            "issue_utc": last["ts_utc"].iloc[0],
                            "valid_utc": last["ts_utc"].iloc[0] + pd.Timedelta(hours=h),
                            "horizon_h": h,
                            "pm25_pred": max(v, 0.0),
                            "pm25_now": float(last["pm25"].iloc[0])})
    if fc_rows:
        fc = pd.DataFrame(fc_rows)
        fc.to_parquet(FC_OUT, index=False)
        log(f"forecast issued for {fc['location_id'].nunique()} station(s) at {fc['issue_utc'].max()}")

    (config.DATA_DIR / "model_meta.json").write_text(json.dumps({
        "split_cut_utc": str(cut), "n_features": len(feats), "features": feats,
        "train_rows": int(len(train)), "test_rows": int(len(test)),
        "horizons": list(config.HORIZONS),
    }, indent=2))


if __name__ == "__main__":
    main()
