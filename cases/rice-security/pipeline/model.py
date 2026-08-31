"""Stage 8 · model — calibration to KSA, and the forward-looking harvest-timing prediction.

TWO MODELS, AND THE ORDER MATTERS
---------------------------------
1. CALIBRATION.  The detector produces hectares; BPS KSA produces hectares; they will not be
   equal.  A single national scale factor would make the headline agree and teach us nothing.
   Instead a small, interpretable OLS maps detected harvested area to KSA per kabupaten-month
   with cropping intensity, prior share and observation density as covariates, so the residual
   structure stays VISIBLE — where we over- or under-detect, and against what.  It is fitted on
   ``config.CAL_YEARS`` only and never on the hold-out.  Both the calibrated and the
   UNCALIBRATED comparisons are exported, because a gate applied only after fitting to the
   benchmark is not a gate.

2. TIMING PREDICTION.  The commercially interesting output is not last year's area, which BPS
   already publishes — it is when this season's harvest will land, said before BPS says it.
   Transplanting is detected as it happens; harvest follows by a crop duration that variety,
   temperature and water stress modulate.  The predictor is deliberately thin and honest: the
   duration model is a kabupaten-and-transplant-month effect estimated on the calibration years
   with a rainfall-anomaly term where CHIRPS is available, so a forecast issued at transplanting
   has ``config.LEAD_WEEKS`` weeks of lead over the official monthly release.  That lead is the
   product; the model behind it is intentionally simple enough to defend line by line.

VALIDATION IS BY TIME, NOT BY RANDOM SPLIT
------------------------------------------
``config.HOLDOUT_SEASON`` is excluded from calibration entirely.  Random cross-validation over a
spatio-temporally autocorrelated panel produces a flattering number with nothing to do with
forecasting.  The hold-out is scored once, and reported whether or not it flatters.

WHAT IS DELIBERATELY NOT MODELLED
---------------------------------
Yield.  Backscatter carries some yield signal but nothing like enough to claim tonnage at
kabupaten level, and Indonesian rice production is politically live.  Production is reported as
OUR AREA x BPS's PUBLISHED PRODUCTIVITY, with the arithmetic shown and labelled as exactly that.

OUTPUT: data/model.parquet, data/model_meta.json.
"""

from __future__ import annotations

import json

import config
import util
from util import log


DESIGN = ["intercept", "detected_kha", "detected_kha_x_inv_vh_range",
          "cropping_intensity", "prior_share", "obs_density"]


def _design(df):
    """The calibration's design matrix — five interpretable terms and one that is the point.

    ``detected_kha_x_inv_vh_range`` was intended as the mechanism rather than a fudge: a 100 m
    cell holds several 0.3-0.5 ha plots transplanted on different days, so the cell MEAN never
    swings as far as a single plot does, and the size of that damping is directly observable per
    kabupaten as the cell's VH seasonal range.  Measured, that range is 8.3 dB in Indramayu
    against 5.9 dB in Lamongan, and the recovered share of KSA moves with it (33 % against 2.5 %
    in 2023).

    ** REVIEW FINDING, 2026-08-31 — this term does not survive scrutiny and is scheduled for
    replacement.  ``pipeline/audit.py`` test E thins Karawang's own record from a 6-day to a
    24-day revisit and moves its VH range only 8.50 -> 6.91 dB while costing 99.7 % of its
    detected fields, so the range is a weak function of sampling and cannot be the channel
    through which revisit destroys detection.  Worse, the fitted interaction gives the detected
    area an EFFECTIVE SLOPE of 1.444 - 9.638 / vh_range, which changes sign below 6.68 dB — so in
    Bojonegoro, Grobogan and Lamongan the calibration says more detected rice implies less KSA
    area.  Across the panel the satellite supplies 5.0 % of the calibrated hectares and the
    monthly R2 is 0.06 against the 0.82 the annual aggregate shows.  Kept here unchanged because
    the published gate is evaluated on it and results are not retro-fitted; see
    ``web/src/pages/article.astro`` sections 9 and 10. **
    """
    import numpy as np

    det = df["harvested_ha"].to_numpy("float64") / 1000.0
    vhr = df["vh_range_db"].fillna(df["vh_range_db"].median()).to_numpy("float64")
    # Covariates are centred on fixed, published constants rather than on this frame's own mean:
    # a design that recentres itself would give the hold-out a different model from the one that
    # was fitted, which is the quiet way to leak the test set into the fit.  Observation density
    # around 250 against a slope near 1 also makes the normal equations badly conditioned and
    # produced a nonsense first fit (intercept 119.8 kha/month against a ~15 kha/month truth).
    x = np.column_stack([
        np.ones(len(df)),
        det,
        det / np.maximum(vhr, 1.0) * 10.0,
        df["ci"].fillna(1.3).to_numpy("float64") - 1.3,
        df["prior_share"].fillna(0.45).to_numpy("float64") - 0.45,
        (df["obs_density"].fillna(250.0).to_numpy("float64") - 250.0) / 50.0,
    ])
    return x


def vh_range(kab: str, cells) -> float:
    """Median VH seasonal range (max - min) over the kabupaten's rice-prior cells, in dB.

    This is the observable that measures how badly a 100 m cell mean damps a plot-scale crop
    cycle, and it is what the calibration's interaction term is built on.
    """
    import numpy as np

    f = config.DATA_DIR / "bs" / f"{kab}.npz"
    if not f.exists():
        return float("nan")
    z = np.load(f)
    ck = cells[cells["kabupaten"] == kab].reset_index(drop=True)
    sel = np.flatnonzero(ck.get("mask_class", 0) > 0) if "mask_class" in ck else np.arange(len(ck))
    if sel.size == 0:
        sel = np.arange(len(ck))
    sel = sel[:: max(1, sel.size // 20000)]
    a = z["vh"][sel].astype("float32")
    a[z["vh"][sel] == -32768] = np.nan
    a /= 100.0
    with np.errstate(all="ignore"):
        return float(np.nanmedian(np.nanmax(a, axis=1) - np.nanmin(a, axis=1)))


def calibrate(panel):
    """OLS of KSA hectares on detected hectares plus three interpretable covariates."""
    import numpy as np

    fit = panel[panel["in_calibration"] & panel["ksa_ha"].notna() &
                panel["benchmark_usable"]].copy()
    util.require(len(fit) >= 30, f"calibration: only {len(fit)} usable rows")
    x = _design(fit)
    y = fit["ksa_ha"].to_numpy("float64") / 1000.0
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    yhat = x @ beta
    ss_res = float(((y - yhat) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    names = DESIGN
    return beta, {
        "form": "ksa_kha ~ " + " + ".join(names[1:]),
        "coefficients": {n: round(float(b), 4) for n, b in zip(names, beta)},
        "n_fit_rows": int(len(fit)),
        "fit_years": list(config.CAL_YEARS),
        "r2_in_sample": round(1 - ss_res / ss_tot, 4) if ss_tot else None,
        "note": ("fitted on kabupaten-month rows of the calibration years only; the hold-out "
                 "season never enters the fit"),
    }


def predict_timing(ph, climate=None):
    """Harvest date from transplanting date — a duration model, fitted on calibration years.

    The unit of the forecast is the WEEK the harvest peak lands in for a kabupaten, because that
    is the unit BPS's monthly KSA release can actually referee.
    """
    import numpy as np
    import pandas as pd

    d = ph.copy()
    d["t_year"] = d["transplant"].dt.year
    d["t_month"] = d["transplant"].dt.month
    train = d[d["t_year"].isin(config.CAL_YEARS)]
    base = float(train["cycle_days"].median()) if len(train) else config.MIN_CYCLE_DAYS
    eff = (train.groupby(["kabupaten", "t_month"])["cycle_days"].median() - base).rename("eff")
    kab_eff = (train.groupby("kabupaten")["cycle_days"].median() - base).rename("kab_eff")
    d = d.merge(eff, on=["kabupaten", "t_month"], how="left").merge(
        kab_eff, on="kabupaten", how="left")
    d["duration_pred"] = base + d["eff"].fillna(d["kab_eff"]).fillna(0.0)
    if climate is not None and len(climate):
        d = d.merge(climate, on=["kabupaten", "t_year", "t_month"], how="left")
        if "rain_anom_mm" in d:
            # a late/dry start lengthens the cycle; the coefficient is estimated, not asserted
            tr = d[d["t_year"].isin(config.CAL_YEARS) & d["rain_anom_mm"].notna()]
            if len(tr) > 200:
                a = np.polyfit(tr["rain_anom_mm"], tr["cycle_days"] - tr["duration_pred"], 1)
                d["duration_pred"] = d["duration_pred"] + np.where(
                    d["rain_anom_mm"].notna(), a[0] * d["rain_anom_mm"].fillna(0) + a[1], 0.0)
                log(f"model: rainfall-anomaly term {a[0]:+.4f} days per mm")
    d["harvest_pred"] = d["transplant"] + pd.to_timedelta(d["duration_pred"].round(), "D")
    return d, dict(base_duration_days=round(base, 1),
                   month_effects={f"{k[0]}|{int(k[1])}": round(float(v), 1)
                                  for k, v in eff.items()},
                   note="issued at transplanting; the lead over the official release is the "
                        f"{config.LEAD_WEEKS}-week product")


def production_from_area(area_year, ksa_prod):
    """Our area x BPS's published productivity.  Not an independent production estimate."""
    import pandas as pd

    if ksa_prod is None or not len(ksa_prod):
        return None
    m = area_year.merge(ksa_prod, on=["province", "year"], how="left")
    m["production_t"] = m["harvested_ha"] * m["productivity_ku_ha"] / 10.0
    m["arithmetic"] = ("our harvested area (ha) x BPS published productivity (ku/ha) / 10 "
                       "= tonnes GKG — BPS's productivity, our area, nothing modelled")
    return m


def main() -> None:
    import numpy as np
    import pandas as pd

    util.guard_disk()
    D = config.DATA_DIR
    am = pd.read_parquet(D / "area_month.parquet")
    ksa_m = pd.read_parquet(D / "bps_kab_month.parquet")
    ci = pd.read_parquet(D / "cropping_intensity.parquet")
    ph = pd.read_parquet(D / "phenology.parquet")
    cells = pd.read_parquet(D / "cells.parquet")
    bs_meta = json.loads((D / "backscatter_meta.json").read_text())

    prior = (cells.assign(p=(cells.get("mask_class", pd.Series(0, index=cells.index)) > 0))
             .groupby("kabupaten")["p"].mean().rename("prior_share").reset_index())
    obs = pd.DataFrame([{"kabupaten": k, "obs_density": v["median_obs_per_cell"]}
                        for k, v in bs_meta.items()])
    obs["vh_range_db"] = [vh_range(k, cells) for k in obs["kabupaten"]]
    log("model: VH seasonal range over rice-prior cells (the damping term) — "
        + ", ".join(f"{r.kabupaten} {r.vh_range_db:.2f} dB" for r in obs.itertuples()))

    ksa_m = ksa_m[(ksa_m["month"] > 0) & (~ksa_m["region"].mod(100).eq(0))]
    panel = am.merge(
        ksa_m[["kab", "year", "month", "ha", "benchmark_usable"]]
        .rename(columns={"kab": "kabupaten", "ha": "ksa_ha"}),
        on=["kabupaten", "year", "month"], how="left")
    panel = panel.merge(ci, on=["kabupaten", "year"], how="left") \
                 .merge(prior, on="kabupaten", how="left") \
                 .merge(obs, on="kabupaten", how="left")
    panel["benchmark_usable"] = panel["benchmark_usable"].fillna(False)
    panel["in_calibration"] = panel["year"].isin(config.CAL_YEARS)
    panel["is_holdout"] = panel["season"] == config.HOLDOUT_SEASON

    beta, calmeta = calibrate(panel)
    panel["calibrated_kha"] = _design(panel) @ beta
    panel["calibrated_ha"] = panel["calibrated_kha"] * 1000.0
    panel = panel.drop(columns=["calibrated_kha"])
    log(f"model: calibration {calmeta['coefficients']} R2(in-sample)={calmeta['r2_in_sample']}")

    climate = None
    cf = D / "climate_kab_month.parquet"
    if cf.exists():
        climate = pd.read_parquet(cf)
    timed, tmeta = predict_timing(ph, climate)
    timed_small = timed[["kabupaten", "kab_bps", "cell_i", "transplant", "harvest",
                         "harvest_pred", "duration_pred", "confidence", "ha"]]
    timed_small.to_parquet(D / "timing.parquet", index=False)

    # predicted monthly harvested-area curve, issued at transplanting
    pred = (timed.assign(year=timed["harvest_pred"].dt.year,
                         month=timed["harvest_pred"].dt.month)
            .groupby(["kabupaten", "year", "month"], observed=True)["ha"]
            .sum().reset_index(name="harvested_ha_pred"))
    panel = panel.merge(pred, on=["kabupaten", "year", "month"], how="left")
    panel.to_parquet(D / "model.parquet", index=False)

    prod = None
    pv = D / "bps_prov.parquet"
    if pv.exists():
        p = pd.read_parquet(pv)
        pr = p[p["quantity"].astype(str).str.contains("Produktivitas", na=False)]
        if len(pr):
            pr = (pr.groupby(["province", "year"])["value"].mean()
                  .reset_index(name="productivity_ku_ha"))
            ay = pd.read_parquet(D / "area_year.parquet")
            prod = production_from_area(ay, pr)
            if prod is not None:
                prod.to_parquet(D / "production.parquet", index=False)
                log(f"model: production = area x BPS productivity for {len(prod)} kabupaten-years")

    (D / "model_meta.json").write_text(json.dumps({
        "calibration": calmeta, "timing": tmeta,
        "holdout_season": config.HOLDOUT_SEASON,
        "lead_weeks": config.LEAD_WEEKS,
        "production": ("our harvested area x BPS published provincial productivity; "
                       "NOT an independent production estimate"
                       if prod is not None else "not computed — BPS productivity unavailable"),
        "not_modelled": ["yield", "national coverage", "non-rice crops",
                         "irrigation infrastructure"],
    }, indent=1, default=str))
    log(f"model -> {D/'model.parquet'} ({len(panel):,} kabupaten-month rows)")


if __name__ == "__main__":
    main()
