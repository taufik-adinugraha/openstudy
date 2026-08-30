"""Stage 4 · downscale — kecamatan estimates, benchmarked to the official regency rate.

The regency model is applied to kecamatan features built by the SAME derive() call, then
every kecamatan in a regency is rescaled by one factor so that the population-weighted mean
reproduces the official BPS rate exactly (G-F4). The model therefore only ever decides how
the official number is DISTRIBUTED inside a regency; it never moves the regency total.

Benchmarking is multiplicative (poverty rates are non-negative and roughly log-scaled), with
a clipped-unit redistribution loop so that a kecamatan pinned at 0 or 100 % cannot break the
identity — the residual it cannot absorb is pushed onto the units that are still free.

Intervals: p10/p90 quantile boosters, rescaled by the same factor, then widened in
quadrature by the province's own leave-one-province-out residual RMSE. The widening is
deliberately outside the benchmark: the point estimate is exact, the interval is honest.

Output: data/estimates_adm3.parquet  (also consumed by Case G, transit equity)
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

import config
import features as F

MAX_ITER = 12


def benchmark(pred: np.ndarray, pop: np.ndarray, official: float) -> tuple[np.ndarray, float]:
    """Rescale so sum(pop*est)/sum(pop) == official, keeping every est in [0, 100]."""
    w = np.where(np.isfinite(pop) & (pop > 0), pop, 0.0)
    p = np.where(np.isfinite(pred), np.clip(pred, 0.0, 100.0), np.nan)
    ok = np.isfinite(p) & (w > 0)
    if not ok.any() or not np.isfinite(official) or w[ok].sum() <= 0:
        return p, np.nan
    base = float((w[ok] * p[ok]).sum() / w[ok].sum())
    factor = official / base if base > 1e-9 else np.nan
    est = p.copy()
    if not np.isfinite(factor):
        # a regency whose model surface is flat zero: fall back to the official rate itself
        est[ok] = official
        return est, 1.0
    est[ok] = np.clip(p[ok] * factor, 0.0, 100.0)
    target = official * w[ok].sum()
    for _ in range(MAX_ITER):
        gap = target - float((w[ok] * est[ok]).sum())
        if abs(gap) < 1e-9 * max(target, 1.0):
            break
        free = ok.copy()
        free[ok] = (est[ok] > 1e-12) if gap < 0 else (est[ok] < 100.0 - 1e-12)
        mass = float((w[free] * est[free]).sum())
        if not free.any() or mass <= 1e-12:
            break
        est[free] = np.clip(est[free] * (1.0 + gap / mass), 0.0, 100.0)
    return est, float(factor)


def run() -> pd.DataFrame:
    if not (config.MODEL_DIR / "lgbm_p0.txt").exists():
        sys.exit("[downscale] no trained model — run model.py first")
    import lightgbm as lgb

    cols = json.loads((config.MODEL_DIR / "features.json").read_text())
    f3 = pd.read_parquet(config.FEATURES_ADM3)
    for c in cols:
        if c not in f3.columns:
            f3[c] = np.nan
    boosters = {k: lgb.Booster(model_file=str(config.MODEL_DIR / f"lgbm_{k}.txt"))
                for k in ("p0", "p10", "p90")}
    X = f3[cols]
    f3["pred_raw"] = boosters["p0"].predict(X)
    f3["q10_raw"] = boosters["p10"].predict(X)
    f3["q90_raw"] = boosters["p90"].predict(X)

    bps = pd.read_parquet(config.DATA_DIR / "bps_poverty.parquet")
    f3 = f3.merge(bps[["bps_code", "year", "p0_pct", "p1_gap", "p2_severity"]].rename(
        columns={"p0_pct": "official_p0", "p1_gap": "official_p1",
                 "p2_severity": "official_p2"}), on=["bps_code", "year"], how="left")

    # province residual spread from the full-panel LOPO fit — the interval widening term
    cv = pd.read_parquet(config.CV_PREDICTIONS)
    cv = cv[np.isfinite(cv["pred_lopo_panel"]) & np.isfinite(cv[config.TARGET])]
    resid = (cv["pred_lopo_panel"] - cv[config.TARGET])
    prov_rmse = (resid.pow(2).groupby(cv["prov_code"]).mean() ** 0.5).to_dict()
    national_rmse = float((resid.pow(2).mean()) ** 0.5)

    est = np.full(len(f3), np.nan)
    lo = np.full(len(f3), np.nan)
    hi = np.full(len(f3), np.nan)
    factors = np.full(len(f3), np.nan)
    grp = f3.groupby(["bps_code", "year"], sort=False).indices
    for (code, year), idx in grp.items():
        official = f3["official_p0"].to_numpy()[idx]
        official = official[np.isfinite(official)]
        if not len(official):
            continue
        pop = f3["pop"].to_numpy()[idx]
        e, fac = benchmark(f3["pred_raw"].to_numpy()[idx], pop, float(official[0]))
        est[idx] = e
        factors[idx] = fac
        if np.isfinite(fac):
            q10 = np.clip(f3["q10_raw"].to_numpy()[idx] * fac, 0, 100)
            q90 = np.clip(f3["q90_raw"].to_numpy()[idx] * fac, 0, 100)
            half = np.maximum(np.abs(e - q10), np.abs(q90 - e))
            s = prov_rmse.get(str(code)[:2], national_rmse)
            half = np.sqrt(half ** 2 + s ** 2)
            lo[idx] = np.clip(e - half, 0, 100)
            hi[idx] = np.clip(e + half, 0, 100)

    f3["p0_est"] = est
    f3["p0_lo"] = lo
    f3["p0_hi"] = hi
    f3["benchmark_factor"] = factors
    out_cols = ["pcode", "name", "bps_code", "adm2_code", "prov_code", "prov_name", "year",
                "pop", "area_km2", "official_p0", "p0_est", "p0_lo", "p0_hi", "pred_raw",
                "benchmark_factor", "is_java", "is_kota",
                "bld_per_km2", "roof_share_lt40", "roof_p50_m2", "lights_per_capita",
                "lights_mean", "lc_built", "lc_crop", "lc_tree", "pop_density"]
    out = f3[[c for c in out_cols if c in f3.columns]].copy()
    out.to_parquet(config.ESTIMATES_ADM3, index=False)

    have = out[np.isfinite(out["p0_est"])]
    print(f"[downscale] {len(have):,} kecamatan-year estimates over "
          f"{have['pcode'].nunique():,} kecamatan × {have['year'].nunique()} years; "
          f"benchmark factor median {np.nanmedian(factors):.3f} "
          f"(p05 {np.nanpercentile(factors,5):.2f} / p95 {np.nanpercentile(factors,95):.2f})",
          flush=True)
    missing = out[~np.isfinite(out["p0_est"]) & (out["year"] == out["year"].max())]
    if len(missing):
        print(f"[downscale] {len(missing)} kecamatan without an estimate in the latest year "
              f"(no official regency rate or no population) — listed on the page", flush=True)
    return out


def main() -> None:
    run()


if __name__ == "__main__":
    main()
