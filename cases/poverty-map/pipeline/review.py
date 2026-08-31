"""Adversarial review — independent recomputation and four pre-specified extra tests.

Nothing here is taken on trust from stats.json. Every published skill number is
re-derived from data/cv_predictions.parquet, and four questions the case does not
answer are put to its own data under the case's own folds:

  A · Horse race.  Does the 31-feature booster beat a population-density-only or a
      night-lights-only model on the identical leave-one-province-out folds? A case
      whose headline is "roofs and light predict poverty" must beat "people per km²".

  B · The line, as oracle.  The case's defence of its failing check is that the target
      is a headcount against a nominal, region-specific poverty line the satellite
      cannot see. That defence makes a sharp prediction: hand the model the official
      poverty line and the missing skill should come back. This is circular as a
      deployment feature (the line comes from the survey being predicted) and is run
      here only as a diagnostic. It is decisive either way.

  C · Can a satellite see the price level?  The same features, the same folds, but the
      target is the official poverty line itself. The case asserts "no satellite can see
      a price level"; this measures it.

  D · The product test.  Chapter 4 ships a benchmarked disaggregator: the parent's
      official rate is distributed among its children by the model. There is no ground
      truth one level below the regency — but there is one level above. Benchmark the
      held-out predictions to each province's official population-weighted rate and score
      them against the official regency rates, against the null of giving every regency
      its province's rate. This is the shipped procedure, run one level up, where it can
      be checked.

  E · The operational baseline.  Last year's published rate, as a predictor of this
      year's. Any satellite model at a level BPS already publishes must beat it.

Outputs data/review.json (and web/src/data/article.json via article.py).
"""

from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd

import config
import features as F
import model as M

LATEST = 2025


# --------------------------------------------------------------------------- helpers
def sk(y, p) -> dict:
    from scipy import stats as sstats

    y = np.asarray(y, "float64")
    p = np.asarray(p, "float64")
    m = np.isfinite(y) & np.isfinite(p)
    y, p = y[m], p[m]
    if len(y) < 3:
        return {"n": int(len(y))}
    sse = float(((y - p) ** 2).sum())
    sst = float(((y - y.mean()) ** 2).sum())
    return {
        "n": int(len(y)),
        "r2": round(1 - sse / sst, 4),
        "spearman": round(float(sstats.spearmanr(y, p).statistic), 4),
        "pearson": round(float(np.corrcoef(y, p)[0, 1]), 4),
        "rmse": round(float(np.sqrt(sse / len(y))), 4),
        "mae": round(float(np.abs(y - p).mean()), 4),
        "bias": round(float((p - y).mean()), 4),
    }


def ols(x, y) -> dict:
    x = np.asarray(x, "float64")
    y = np.asarray(y, "float64")
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    n = len(x)
    mx, my = x.mean(), y.mean()
    sxx = float(((x - mx) ** 2).sum())
    sxy = float(((x - mx) * (y - my)).sum())
    syy = float(((y - my) ** 2).sum())
    b = sxy / sxx if sxx > 0 else np.nan
    r = sxy / np.sqrt(sxx * syy) if sxx > 0 and syy > 0 else np.nan
    return {"slope": round(float(b), 5), "intercept": round(float(my - b * mx), 5),
            "r": round(float(r), 4), "r2": round(float(r * r), 4), "n": int(n)}


FAMILY = {
    "population": ["pop_density", "log_pop_density", "pop_growth_5y"],
    "lights": ["lights_mean", "lights_per_capita", "lights_per_km2",
               "log_lights_per_capita", "lights_per_built_km2", "lights_trend_5y"],
    "buildings": ["bld_per_capita", "roof_m2_per_capita", "bld_per_km2", "roof_area_share",
                  "roof_mean_m2", "roof_cv", "roof_share_lt40", "roof_share_lt60",
                  "roof_share_gt300", "roof_p50_m2", "roof_p90_m2"],
    "landcover": ["lc_tree", "lc_shrub", "lc_grass", "lc_crop", "lc_built", "lc_bare",
                  "lc_water", "lc_wetland", "lc_mangrove"],
    "geography": ["is_kota", "is_java"],
}


def main() -> int:
    t0 = time.time()
    df = pd.read_parquet(config.FEATURES_ADM2).sort_values(["year", "bps_code"]).reset_index(drop=True)
    cv = pd.read_parquet(config.CV_PREDICTIONS)
    ms = json.loads((config.DATA_DIR / "model_stats.json").read_text())
    cols = ms["features"]
    out: dict = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "vintage": json.loads((config.DATA_DIR / "stats.json").read_text())["vintage"],
                 "latest_year": LATEST, "n_features": len(cols),
                 "families": {k: len(v) for k, v in FAMILY.items()}}

    # ══ 0 · independent recomputation of every published skill number ═══════════════
    L = cv[cv["year"] == LATEST].copy()
    y = L["p0_pct"].to_numpy("float64")
    rep = {}
    for k in ("lopo", "block", "random", "lopo_ridge"):
        rep[k] = sk(y, L[f"pred_{k}"])
    for name, mask in (("java", L["is_java"] == 1), ("offjava", L["is_java"] == 0),
                       ("kota", L["is_kota"] == 1), ("kabupaten", L["is_kota"] == 0)):
        rep[f"lopo_{name}"] = sk(y[mask.to_numpy()], L.loc[mask, "pred_lopo"])
    for yr in (2024, 2025):
        s = cv[cv["year"] == yr]
        rep[f"temporal_{yr}"] = sk(s["p0_pct"], s["pred_temporal"])
        rep[f"temporal_strict_{yr}"] = sk(s["p0_pct"], s["pred_temporal_strict"])
    rep["panel_lopo"] = sk(cv["p0_pct"], cv["pred_lopo_panel"])
    out["recomputed"] = rep

    published = json.loads((config.DATA_DIR / "stats.json").read_text())["skill"]
    diffs = []
    for k, v in rep.items():
        pk = {"panel_lopo": "skill_panel_lopo"}.get(k, k)
        pv = published.get(pk)
        if not pv or v.get("r2") is None:
            continue
        d = abs(pv["r2"] - v["r2"])
        diffs.append({"key": k, "published": pv["r2"], "recomputed": v["r2"], "abs_diff": round(d, 5)})
    out["audit"] = {"rows": diffs, "max_abs_diff_r2": round(max(d["abs_diff"] for d in diffs), 5)}

    # ══ 1 · how the target is structured: between- vs within-province variance ══════
    g = L.groupby("prov_code")
    L["y_prov_mean"] = g["p0_pct"].transform("mean")
    sst = float(((y - y.mean()) ** 2).sum())
    ss_between = float(((L["y_prov_mean"] - y.mean()) ** 2).sum())
    ss_within = float(((y - L["y_prov_mean"]) ** 2).sum())
    out["variance"] = {
        "sst": round(sst, 1),
        "between_share": round(ss_between / sst, 4),
        "within_share": round(ss_within / sst, 4),
        "sd_total": round(float(y.std(ddof=1)), 3),
        "sd_within": round(float(np.sqrt(ss_within / (len(y) - L["prov_code"].nunique()))), 3),
        "n_prov": int(L["prov_code"].nunique()),
    }

    # ══ 2 · where the error lives, both statistics ═════════════════════════════════
    p = L["pred_lopo"].to_numpy("float64")
    ok = np.isfinite(p)
    d = pd.DataFrame({"prov": L.loc[ok, "prov_code"].values, "prov_name": L.loc[ok, "prov_name"].values,
                      "y": y[ok], "p": p[ok], "pop": L.loc[ok, "pop"].values,
                      "line": L.loc[ok, "poverty_line_idr"].values,
                      "kota": L.loc[ok, "is_kota"].values})
    gp = d.groupby("prov")
    d["yc"] = d["y"] - gp["y"].transform("mean")
    d["pc"] = d["p"] - gp["p"].transform("mean")
    sse_w = float(((d["yc"] - d["pc"]) ** 2).sum())
    sst_w = float((d["yc"] ** 2).sum())
    from scipy import stats as sstats
    rhos = [(len(s), float(sstats.spearmanr(s["y"], s["p"]).statistic))
            for _, s in d.groupby("prov") if len(s) >= 5 and s["y"].nunique() > 2]
    out["within_province"] = {
        "r2": round(1 - sse_w / sst_w, 4),
        "spearman": round(sum(n * r for n, r in rhos) / sum(n for n, _ in rhos), 4),
        "provinces_scored": len(rhos),
        "rmse": round(float(np.sqrt(((d["yc"] - d["pc"]) ** 2).mean())), 4),
    }

    # per-province table: offset, own poverty line, own rho
    rows = []
    for prov, s in d.groupby("prov"):
        r = float(sstats.spearmanr(s["y"], s["p"]).statistic) if len(s) >= 5 and s["y"].nunique() > 2 else None
        rows.append({"prov": prov, "name": str(s["prov_name"].iloc[0]), "n": int(len(s)),
                     "official": round(float(s["y"].mean()), 3),
                     "pred": round(float(s["p"].mean()), 3),
                     "offset": round(float(s["p"].mean() - s["y"].mean()), 3),
                     "line": round(float(s["line"].mean()), 0),
                     "rho": None if r is None or not np.isfinite(r) else round(r, 3)})
    rows.sort(key=lambda r: r["offset"])
    out["provinces"] = rows

    # ══ 3 · TEST D-support: does the poverty line explain the province offset? ══════
    off = np.array([r["offset"] for r in rows], "float64")
    line = np.array([r["line"] for r in rows], "float64")
    offi = np.array([r["official"] for r in rows], "float64")
    out["offset_explained"] = {
        "by_poverty_line": ols(line, off),
        "by_log_poverty_line": ols(np.log(line), off),
        "by_official_level": ols(offi, off),
        "line_range_idr": [int(line.min()), int(line.max())],
        "line_ratio": round(float(line.max() / line.min()), 3),
        "offset_range_pp": [round(float(off.min()), 2), round(float(off.max()), 2)],
    }
    # calibration slope: attenuation shows up as slope > 1 of truth on prediction
    out["calibration"] = ols(d["p"], d["y"])

    # ══ 4 · TEST A/B/C — the folds, exactly as the case defines them ═══════════════
    dfm = df.copy()
    ally = dfm["p0_pct"].to_numpy("float64")
    is_latest = (dfm["year"] == LATEST).to_numpy() & np.isfinite(ally)
    all_obs = np.isfinite(ally)

    def lopo_r2(feature_cols, target="p0_pct", eval_mask=None, log_target=False):
        w = dfm.copy()
        if log_target:
            w[target] = np.log(w[target])
        old = config.TARGET
        config.TARGET = target
        try:
            pr = M.cv_predict(w, feature_cols, w["prov_code"],
                              eval_mask if eval_mask is not None else is_latest)
        finally:
            config.TARGET = old
        t = w[target].to_numpy("float64")
        msk = eval_mask if eval_mask is not None else is_latest
        return sk(t[msk], pr[msk]), pr

    ladder = []
    specs = [
        ("Population only", FAMILY["population"]),
        ("Night lights only", FAMILY["lights"]),
        ("Buildings only", FAMILY["buildings"]),
        ("Land cover only", FAMILY["landcover"]),
        ("Population + lights", FAMILY["population"] + FAMILY["lights"]),
        ("Population + lights + buildings", FAMILY["population"] + FAMILY["lights"] + FAMILY["buildings"]),
        ("All 31 features (published)", cols),
    ]
    for label, fc in specs:
        s, _ = lopo_r2(fc)
        s["label"] = label
        s["k"] = len(fc)
        ladder.append(s)
        print(f"[review] A · {label:34s} k={len(fc):2d}  R² {s['r2']:+.4f}  ρ {s['spearman']:.3f}  "
              f"RMSE {s['rmse']:.3f}", flush=True)
    # the true null: predict the national mean of the training folds
    nullp = np.full(len(dfm), np.nan)
    for prov in sorted(dfm["prov_code"].dropna().unique()):
        te = (dfm["prov_code"] == prov).to_numpy() & is_latest
        tr = (dfm["prov_code"] != prov).to_numpy() & all_obs
        nullp[te] = ally[tr].mean()
    s = sk(ally[is_latest], nullp[is_latest])
    s["label"] = "National mean (null)"
    s["k"] = 0
    ladder.insert(0, s)
    out["testA_ladder"] = ladder

    # ── TEST B · the official poverty line as an oracle feature ───────────────────
    dfm["log_line"] = np.log(dfm["poverty_line_idr"])
    prov_line = dfm.groupby(["prov_code", "year"])["poverty_line_idr"].transform("mean")
    dfm["log_prov_line"] = np.log(prov_line)
    oracle = []
    for label, fc in (("Published model", cols),
                      ("+ province mean poverty line", cols + ["log_prov_line"]),
                      ("+ regency poverty line", cols + ["log_line"])):
        s, _ = lopo_r2(fc)
        s["label"] = label
        oracle.append(s)
        print(f"[review] B · {label:34s} R² {s['r2']:+.4f}  ρ {s['spearman']:.3f}  "
              f"RMSE {s['rmse']:.3f}", flush=True)
    out["testB_oracle"] = oracle

    # ── TEST C · can the satellite see the price level? ───────────────────────────
    s, predline = lopo_r2(cols, target="poverty_line_idr", log_target=True)
    # report on the natural scale too
    yl = dfm.loc[is_latest, "poverty_line_idr"].to_numpy("float64")
    pl = np.exp(predline[is_latest])
    out["testC_line"] = {"log": s, "level": sk(yl, pl),
                         "mape": round(float(np.nanmean(np.abs(pl - yl) / yl)), 4)}
    print(f"[review] C · poverty line as target: log R² {s['r2']:+.4f} ρ {s['spearman']:.3f} · "
          f"level R² {out['testC_line']['level']['r2']:+.4f} · MAPE "
          f"{out['testC_line']['mape']*100:.1f}%", flush=True)

    # ══ 5 · TEST D — the shipped procedure, one level up ══════════════════════════
    # Benchmark the held-out predictions to each province's official population-weighted
    # rate (exactly downscale.py's rule, one level higher), then score against the
    # official regency rates. Null: give every regency its province's rate.
    b = d.copy()
    gb = b.groupby("prov")
    off_prov = gb.apply(lambda s: np.average(s["y"], weights=s["pop"]), include_groups=False)
    pred_prov = gb.apply(lambda s: np.average(s["p"], weights=s["pop"]), include_groups=False)
    b["prov_official"] = b["prov"].map(off_prov)
    b["factor"] = b["prov"].map(off_prov / pred_prov)
    b["bench"] = (b["p"] * b["factor"]).clip(0, 100)
    out["testD_disaggregation"] = {
        "flat_null": sk(b["y"], b["prov_official"]),
        "benchmarked_model": sk(b["y"], b["bench"]),
        "unbenchmarked_model": sk(b["y"], b["p"]),
        "win_rate": round(float((np.abs(b["bench"] - b["y"]) < np.abs(b["prov_official"] - b["y"])).mean()), 4),
        "mae_reduction_pp": round(float(np.abs(b["prov_official"] - b["y"]).mean()
                                        - np.abs(b["bench"] - b["y"]).mean()), 4),
        "n": int(len(b)),
    }
    td = out["testD_disaggregation"]
    print(f"[review] D · flat province rate R² {td['flat_null']['r2']:+.4f} MAE {td['flat_null']['mae']:.3f} vs "
          f"benchmarked model R² {td['benchmarked_model']['r2']:+.4f} MAE {td['benchmarked_model']['mae']:.3f} · "
          f"win {td['win_rate']*100:.1f}%", flush=True)

    # ══ 6 · TEST E — last year's official rate ════════════════════════════════════
    wide = cv.pivot_table(index="bps_code", columns="year", values="p0_pct")
    lag = []
    for k in (1, 2, 5, 9):
        if LATEST - k in wide.columns:
            s = sk(wide[LATEST], wide[LATEST - k])
            s["lag"] = k
            s["label"] = f"Official rate, {LATEST - k}"
            lag.append(s)
    out["testE_persistence"] = lag
    print(f"[review] E · official {LATEST-1} predicts {LATEST}: R² {lag[0]['r2']:.4f} "
          f"RMSE {lag[0]['rmse']:.3f} pp", flush=True)

    # survey noise floor: residual of each regency's rate about its own smooth trend
    resid = []
    for code, s in cv.sort_values("year").groupby("bps_code"):
        v = s["p0_pct"].to_numpy("float64")
        if np.isfinite(v).sum() < 8:
            continue
        t = s["year"].to_numpy("float64")
        m = np.isfinite(v)
        co = np.polyfit(t[m], v[m], 2)
        resid.append(v[m] - np.polyval(co, t[m]))
    r = np.concatenate(resid)
    out["survey_noise"] = {"rmse_about_quadratic_trend_pp": round(float(np.sqrt((r ** 2).mean())), 4),
                           "n_regencies": len(resid), "n_obs": int(len(r))}

    # ══ 7 · coverage and where the model breaks ═══════════════════════════════════
    lat = df[df["year"] == LATEST].set_index("bps_code")
    b2 = b.copy()
    b2["bps_code"] = L.loc[ok, "bps_code"].values
    b2 = b2.join(lat[["bld_per_km2", "lights_mean", "pop_density", "lc_crop", "roof_mean_m2"]],
                 on="bps_code")
    b2["abserr"] = np.abs(b2["p"] - b2["y"])
    q = pd.qcut(b2["bld_per_km2"].rank(method="first"), 5, labels=False)
    out["coverage_quintiles"] = [
        {"q": int(i) + 1,
         "bld_per_km2_median": round(float(b2.loc[q == i, "bld_per_km2"].median()), 1),
         "mae": round(float(b2.loc[q == i, "abserr"].mean()), 3),
         "n": int((q == i).sum())}
        for i in range(5)]

    # ══ 8 · the published scatter, for the figures ════════════════════════════════
    out["scatter"] = [
        {"n": str(r.bps_name), "pv": str(r.prov_name), "y": round(float(r.p0_pct), 2),
         "p": round(float(r.pred_lopo), 2), "k": int(r.is_kota), "j": int(r.is_java)}
        for r in L.itertuples() if np.isfinite(r.pred_lopo)]

    (config.DATA_DIR / "review.json").write_text(json.dumps(out, indent=1))
    print(f"[review] -> data/review.json in {time.time()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
