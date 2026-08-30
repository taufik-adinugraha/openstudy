"""Stage 3 · train — LightGBM on the official regency poverty rate, cross-validated in space.

The headline number is leave-one-province-out (38 folds, the current BPS province vintage
including the 2022 Papua splits). Random k-fold is computed too, but only so the page can
show how much it flatters: neighbouring regencies share roofs, lights and markets, so a
random fold leaves a near-copy of every test unit in the training set.

Schemes evaluated
  lopo     leave-one-province-out           G-F1 headline, G-F2 splits
  block    200 km equal-area grid blocks    spatial sensitivity
  random   10-fold over regencies           published only as the inflation reference
  temporal train year <= 2023, test 2024/25 G-F3

Also fitted: a ridge baseline (same folds, median-imputed + standardised) so the gradient
boosting has to earn its place, and p10/p90 quantile boosters for the downscale intervals.

Outputs: data/cv_predictions.parquet, data/shap_adm2.parquet, data/model/*.txt,
data/model_stats.json.
"""

from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd

import config
import features as F

BLOCK_M = 200_000.0
RANDOM_FOLDS = 10
SEED = 7


# ------------------------------------------------------------------------------- metrics
def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    from scipy import stats as sstats

    m = np.isfinite(y) & np.isfinite(p)
    y, p = y[m], p[m]
    if len(y) < 3:
        return {"n": int(len(y)), "r2": None, "spearman": None, "pearson": None,
                "rmse": None, "mae": None, "bias": None}
    ss_res = float(((y - p) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {
        "n": int(len(y)),
        "r2": round(1.0 - ss_res / ss_tot, 4) if ss_tot > 0 else None,
        "spearman": round(float(sstats.spearmanr(y, p).statistic), 4),
        "pearson": round(float(np.corrcoef(y, p)[0, 1]), 4),
        "rmse": round(float(np.sqrt(ss_res / len(y))), 4),
        "mae": round(float(np.abs(y - p).mean()), 4),
        "bias": round(float((p - y).mean()), 4),
    }


# -------------------------------------------------------------------------------- learner
def fit_lgbm(X: pd.DataFrame, y: np.ndarray, params: dict | None = None):
    import lightgbm as lgb

    p = dict(config.LGBM_PARAMS)
    p.update(params or {})
    p.setdefault("objective", "regression")
    p.setdefault("verbose", -1)
    n_est = p.pop("n_estimators", 600)
    seed = p.pop("seed", SEED)
    p.update({"seed": seed, "bagging_seed": seed, "feature_fraction_seed": seed,
              "bagging_fraction": p.pop("subsample", 0.8), "bagging_freq": 1,
              "feature_fraction": p.pop("colsample_bytree", 0.8),
              "min_data_in_leaf": p.pop("min_child_samples", 10),
              "num_leaves": p.get("num_leaves", 15)})
    ds = lgb.Dataset(X, label=y, free_raw_data=False)
    return lgb.train(p, ds, num_boost_round=n_est)


def fit_ridge(X: pd.DataFrame, y: np.ndarray):
    """Linear baseline. Median imputation + standardisation, alpha by internal 5-fold."""
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import RidgeCV
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    pipe = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                         RidgeCV(alphas=np.logspace(-2, 3, 24)))
    return pipe.fit(X, y)


# ------------------------------------------------------------------------------ CV drivers
def cv_predict(df: pd.DataFrame, cols: list[str], fold: pd.Series, eval_mask: np.ndarray,
               learner: str = "lgbm") -> np.ndarray:
    """Out-of-fold predictions for the rows in eval_mask. A fold is held out ENTIRELY —
    every year of a held-out province leaves the training set, so no temporal copy of a
    test regency can leak in through the panel."""
    y = df[config.TARGET].to_numpy("float64")
    out = np.full(len(df), np.nan)
    trainable = np.isfinite(y)
    for f in sorted(fold.dropna().unique()):
        te = (fold == f).to_numpy() & eval_mask
        if not te.any():
            continue
        tr = (fold != f).to_numpy() & trainable
        if tr.sum() < 50:
            continue
        X = df.loc[tr, cols]
        model = fit_lgbm(X, y[tr]) if learner == "lgbm" else fit_ridge(X, y[tr])
        out[te] = model.predict(df.loc[te, cols])
    return out


def adm2_centroids() -> pd.DataFrame:
    """Equal-area centroids of the reconciled ADM2 units — used for the 200 km blocks and
    exported so the page can place a regency without loading its geometry."""
    import geopandas as gpd

    recode = pd.read_csv(config.DATA_DIR / "adm2_recode.csv", dtype=str)
    m = dict(zip(recode["codab_code"], recode["bps_code"]))
    g = gpd.read_parquet(config.BOUNDARIES_ADM2)[["bps_code", "geometry"]].copy()
    g["target"] = g["bps_code"].map(m)
    g = g[g["target"].notna()].dissolve(by="target").reset_index()
    ea = g.to_crs(6933).geometry.centroid
    ll = g.to_crs(4326).geometry.representative_point()
    return pd.DataFrame({"bps_code": g["target"].values, "x_m": ea.x.values, "y_m": ea.y.values,
                         "lon": ll.x.values, "lat": ll.y.values})


def run() -> dict:
    if not config.FEATURES_ADM2.exists():
        sys.exit("[model] features_adm2.parquet missing — run features.py first")
    df = pd.read_parquet(config.FEATURES_ADM2)
    cols = F.feature_columns(df)
    df = df.sort_values(["year", "bps_code"]).reset_index(drop=True)
    y = df[config.TARGET].to_numpy("float64")
    latest = int(df.loc[np.isfinite(y), "year"].max())
    is_latest = (df["year"] == latest).to_numpy() & np.isfinite(y)
    print(f"[model] {len(df)} rows, {df['bps_code'].nunique()} regencies, {len(cols)} features; "
          f"headline cross-section {latest} ({int(is_latest.sum())} regencies)", flush=True)

    cen = adm2_centroids()
    df = df.merge(cen, on="bps_code", how="left")
    bx = np.floor(df["x_m"] / BLOCK_M).astype("Int64").astype(str)
    by = np.floor(df["y_m"] / BLOCK_M).astype("Int64").astype(str)
    df["block"] = bx + "_" + by
    rng = np.random.default_rng(SEED)
    codes = np.sort(df["bps_code"].unique())
    rfold = pd.Series(rng.integers(0, RANDOM_FOLDS, len(codes)), index=codes)
    df["rfold"] = df["bps_code"].map(rfold).astype(str)

    t0 = time.time()
    all_obs = np.isfinite(y)
    preds = {}
    # One LOPO pass over the whole panel: the training set of a fold does not depend on
    # which rows are scored, so the headline cross-section is a slice of the panel fit.
    # (It also sets the per-province residual spread the downscale widens intervals by.)
    preds["lopo_panel"] = cv_predict(df, cols, df["prov_code"], all_obs)
    preds["lopo"] = np.where(is_latest, preds["lopo_panel"], np.nan)
    print(f"[model] leave-one-province-out: {df['prov_code'].nunique()} folds, "
          f"{time.time()-t0:.0f}s", flush=True)
    preds["block"] = cv_predict(df, cols, df["block"], is_latest)
    preds["random"] = cv_predict(df, cols, df["rfold"], is_latest)
    preds["lopo_ridge"] = cv_predict(df, cols, df["prov_code"], is_latest, learner="ridge")

    # ---- G-F3 temporal: train on <= 2023 only, predict the last two releases.
    # NOTE the same regencies appear in training in earlier years, and the roof and
    # land-cover features are single-vintage, so this measures how persistent a regency's
    # rate is far more than how well the model generalises. It is the test the spec asks
    # for; the strict version below holds out the province AND the year, and that is the
    # number the page quotes next to it.
    tr = (df["year"] <= 2023).to_numpy() & all_obs
    temporal = np.full(len(df), np.nan)
    if tr.sum() > 100:
        mt = fit_lgbm(df.loc[tr, cols], y[tr])
        te = (df["year"] >= 2024).to_numpy() & all_obs
        temporal[te] = mt.predict(df.loc[te, cols])
    preds["temporal"] = temporal

    strict = np.full(len(df), np.nan)
    te_all = (df["year"] >= 2024).to_numpy() & all_obs
    for p in sorted(df["prov_code"].dropna().unique()):
        te = te_all & (df["prov_code"] == p).to_numpy()
        trp = tr & (df["prov_code"] != p).to_numpy()
        if te.any() and trp.sum() > 100:
            strict[te] = fit_lgbm(df.loc[trp, cols], y[trp]).predict(df.loc[te, cols])
    preds["temporal_strict"] = strict

    out = df[["bps_code", "bps_name", "prov_code", "prov_name", "year", "is_java", "is_kota",
              "lon", "lat", config.TARGET, "p1_gap", "p2_severity", "poverty_line_idr",
              "pop", "n_adm3"]].copy()
    for k, v in preds.items():
        out[f"pred_{k}"] = v
    out.to_parquet(config.CV_PREDICTIONS, index=False)

    # ---- final models on the full panel (the ones downscale applies)
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    final = fit_lgbm(df.loc[all_obs, cols], y[all_obs])
    final.save_model(str(config.MODEL_DIR / "lgbm_p0.txt"))
    for q, alpha in (("p10", 0.10), ("p90", 0.90)):
        qm = fit_lgbm(df.loc[all_obs, cols], y[all_obs],
                      {"objective": "quantile", "alpha": alpha})
        qm.save_model(str(config.MODEL_DIR / f"lgbm_{q}.txt"))
    (config.MODEL_DIR / "features.json").write_text(json.dumps(cols, indent=1))

    # ---- SHAP for the latest cross-section (drilldown + family bars)
    shap_rows = None
    try:
        import shap

        expl = shap.TreeExplainer(final)
        Xl = df.loc[is_latest, cols]
        sv = expl.shap_values(Xl)
        shap_rows = pd.DataFrame(sv, columns=cols)
        shap_rows.insert(0, "bps_code", df.loc[is_latest, "bps_code"].values)
        shap_rows["_base"] = float(np.ravel(expl.expected_value)[0])
        shap_rows.to_parquet(config.DATA_DIR / "shap_adm2.parquet", index=False)
    except Exception as err:                                   # never block the run on SHAP
        print(f"[model] SHAP unavailable ({err}) — drilldown falls back to gain importance",
              flush=True)

    gain = dict(zip(cols, final.feature_importance("gain").tolist()))
    stats = {
        "latest_year": latest,
        "n_features": len(cols), "features": cols,
        "n_regencies": int(df["bps_code"].nunique()),
        "n_rows": int(all_obs.sum()),
        "folds": {"lopo": int(df["prov_code"].nunique()),
                  "block_200km": int(df["block"].nunique()),
                  "random": RANDOM_FOLDS},
        "skill": {k: metrics(y[is_latest], preds[k][is_latest])
                  for k in ("lopo", "block", "random", "lopo_ridge")},
        "skill_panel_lopo": metrics(y[all_obs], preds["lopo_panel"][all_obs]),
        "gain_importance": gain,
        "shap": bool(shap_rows is not None),
        "params": config.LGBM_PARAMS,
    }
    for name, mask in (("java", df["is_java"] == 1), ("offjava", df["is_java"] == 0),
                       ("kota", df["is_kota"] == 1), ("kabupaten", df["is_kota"] == 0)):
        m = is_latest & mask.to_numpy()
        stats["skill"][f"lopo_{name}"] = metrics(y[m], preds["lopo"][m])
    for yr in config.TEMPORAL_HOLDOUT_YEARS:
        m = (df["year"] == yr).to_numpy() & all_obs
        stats["skill"][f"temporal_{yr}"] = metrics(y[m], temporal[m])
        stats["skill"][f"temporal_strict_{yr}"] = metrics(y[m], strict[m])

    # ---- where the error actually lives: is it a province-level offset, or misordering
    # inside the province? The benchmark supplies the level, so this is the decomposition
    # that says whether the model is fit for the job it is actually asked to do.
    lo = preds["lopo"]
    m = is_latest & np.isfinite(lo)
    dd = pd.DataFrame({"prov": df.loc[m, "prov_code"].values, "y": y[m], "p": lo[m]})
    gy = dd.groupby("prov")
    dd["yc"] = dd["y"] - gy["y"].transform("mean")
    dd["pc"] = dd["p"] - gy["p"].transform("mean")
    sse_within = float(((dd["yc"] - dd["pc"]) ** 2).sum())
    sst_within = float((dd["yc"] ** 2).sum())
    sse_total = float(((dd["y"] - dd["p"]) ** 2).sum())
    offs = gy.apply(lambda g: len(g) * (g["p"].mean() - g["y"].mean()) ** 2, include_groups=False)
    from scipy import stats as sstats

    rhos = [(len(g), float(sstats.spearmanr(g["y"], g["p"]).statistic))
            for _, g in dd.groupby("prov") if len(g) >= 5 and g["y"].nunique() > 2]
    stats["decomposition"] = {
        "within_province_r2": round(1 - sse_within / sst_within, 4) if sst_within > 0 else None,
        "within_province_spearman": round(sum(n * r for n, r in rhos) / sum(n for n, _ in rhos), 4)
        if rhos else None,
        "provinces_scored": len(rhos),
        "offset_share_of_sse": round(float(offs.sum()) / sse_total, 4) if sse_total > 0 else None,
        "note": ("Fraction of the squared error that is a constant offset for a whole province, "
                 "versus misordering of regencies inside it."),
    }

    (config.DATA_DIR / "model_stats.json").write_text(json.dumps(stats, indent=1))
    s = stats["skill"]["lopo"]
    print(f"[model] LOPO {latest}: R² {s['r2']} · Spearman {s['spearman']} · RMSE {s['rmse']} pp "
          f"(random k-fold R² {stats['skill']['random']['r2']}, "
          f"ridge R² {stats['skill']['lopo_ridge']['r2']})", flush=True)
    return stats


def main() -> None:
    run()


if __name__ == "__main__":
    main()
