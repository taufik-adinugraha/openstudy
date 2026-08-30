"""Stages: deseason + calibrate (incl. nowcast) + validate (spec §A3-A4).

deseason  Coverage-weighted seasonal adjustment per regency. Monsoon months
          are mostly cloud-masked (Dec–Feb coverage often < 10 %), so the raw
          sum-of-lights collapses every wet season for optical, not economic,
          reasons. We model the coverage-normalised level
              y = log(SOL_deflared / coverage)      coverage = n_px / peak n_px
          as  y = trend + month-of-year + β·Ramadan-overlap + noise,
          with weights w = min(1, coverage/0.6)² (zero below 5 % coverage),
          fitted by backfitting: weighted local-linear trend (tricube, ±7 mo)
          ⇄ weighted least squares for the 12 month effects and β. Ramadan
          drifts ~11 days/year so its overlap share (hijridate) is separately
          identified from the calendar months; β is shrunk toward the national
          estimate (precision-weighted). sol_sa = exp(y − season − β·Ramadan);
          trend fills months with no usable composite (flag_no_data), and
          coverage < 30 % is flagged low-confidence.
calibrate Gate G-A1 — levels cross-section log(PDRB) ~ log(lights) per year
          (R² ≥ 0.65; scoped to 2018→ because the ledger starts 2018) plus
          within-regency panel elasticities (one-way and two-way FE, cluster
          SE), a growth calibration (annual and quarterly YoY), an
          out-of-sample test on the latest BPS year, and the nowcast: lights-
          implied YoY activity growth for the months BPS has not published,
          with uncertainty bands. Weak-fit regencies are listed, not hidden.
          Never labeled "GDP" — it is a lights-implied activity index.
validate  Gate results from stats.json; nonzero exit if G-A1 fails.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import sys

import numpy as np
import pandas as pd

import config

GATE_LEVELS_R2 = 0.65      # G-A1
GATE_NOWCAST_WIN = 0.60    # G-A2: beat naive baseline in >=60% of provinces
GATE_XSENSOR_CORR = 0.90   # G-A4: national series vs Black Marble VJ146A3

INDEX = config.DATA_DIR / "index_monthly.parquet"
CALIB = config.DATA_DIR / "calibration.parquet"
NOWCAST = config.DATA_DIR / "nowcast_regency.parquet"
BPS = config.DATA_DIR / "bps_pdrb.parquet"
FLARES = config.DATA_DIR / "flares_regency.parquet"

C_MIN = 0.05      # below this coverage a month has no usable composite
C_LOW = 0.30      # flag_low_coverage
W_FULL = 0.60     # coverage at which a month gets full weight
BW = 7.0          # months — tricube half-width of the trend smoother
MIN_OBS = 15      # months with weight needed to fit a seasonal pattern
FLARE_FLAG = 0.05 # ≥5 % of SOL inside flare buffers → "flare regency" in rankings
PRODUCT = "VJ146A3"


# ----------------------------------------------------------------------------- helpers
def month_range(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def ramadan_overlap(year: int, month: int) -> float:
    """Fraction of the Gregorian month that falls inside Ramadan (Hijri month 9)."""
    from hijridate import Gregorian, Hijri

    start = dt.date(year, month, 1)
    ndays = calendar.monthrange(year, month)[1]
    end = start + dt.timedelta(days=ndays)
    hy = Gregorian(year, month, 15).to_hijri().year
    days = 0
    for y in (hy - 1, hy, hy + 1):
        r0, r1 = Hijri(y, 9, 1).to_gregorian(), Hijri(y, 10, 1).to_gregorian()
        r0, r1 = dt.date(r0.year, r0.month, r0.day), dt.date(r1.year, r1.month, r1.day)
        days += max(0, (min(end, r1) - max(start, r0)).days)
    return days / ndays


def crosswalk() -> pd.DataFrame:
    xw = pd.read_csv(config.CROSSWALK.parent / "region_crosswalk.csv", dtype=str)
    return xw[xw["match"] != "EXCLUDED"]


def smooth(t: np.ndarray, y: np.ndarray, w: np.ndarray, bw: float) -> np.ndarray:
    """Weighted local-linear smoother (tricube kernel). Windows widen where data
    are sparse; one-sided windows fall back to a local mean so the edges do not
    extrapolate a slope through months with no composite."""
    n = len(t)
    out = np.full(n, np.nan)
    yy = np.where(w > 0, y, 0.0)
    for i in range(n):
        h = bw
        for _ in range(6):
            d = np.abs(t - t[i]) / h
            k = np.where(d < 1, (1 - d**3) ** 3, 0.0) * w
            if (k > 0).sum() >= 4 and k.sum() > 0.5:
                break
            h *= 1.8
        pos = k > 0
        if pos.sum() < 2:
            continue
        one_sided = t[pos].min() > t[i] or t[pos].max() < t[i]
        if one_sided:
            out[i] = np.sum(k * yy) / k.sum()
            continue
        X = np.column_stack([np.ones(n), t - t[i]])
        A = X.T @ (X * k[:, None])
        b = X.T @ (k * yy)
        try:
            out[i] = np.linalg.solve(A, b)[0]
        except np.linalg.LinAlgError:
            out[i] = np.sum(k * yy) / k.sum()
    return pd.Series(out).interpolate(limit_direction="both").to_numpy()


def seasonal_fit(resid, w, R, moy, beta_fixed=None):
    """Weighted LS of the detrended series on 12 month-of-year dummies (+ Ramadan
    share unless fixed). Months-of-year with < 0.5 full-month-equivalents of
    weight are left unadjusted. Returns (S[12], beta, se_beta)."""
    ok = w > 0
    S = np.zeros(12)
    beta = 0.0 if beta_fixed is None else float(beta_fixed)
    se = np.nan
    if ok.sum() < MIN_OBS:
        return S, beta, se
    D = np.eye(12)[moy]
    mw = (D * w[:, None]).sum(0)
    ident = mw >= 0.5
    cols = [D[:, m] for m in range(12) if ident[m]]
    if beta_fixed is None:
        cols.append(R)
    X = np.column_stack(cols)[ok]
    yv = (resid if beta_fixed is None else resid - beta * R)[ok]
    sw = np.sqrt(w[ok])
    coef, *_ = np.linalg.lstsq(X * sw[:, None], yv * sw, rcond=None)
    if beta_fixed is None:
        beta = float(coef[-1])
        e = yv - X @ coef
        dof = max(ok.sum() - X.shape[1], 1)
        sigma2 = np.sum(w[ok] * e**2) / dof
        try:
            se = float(np.sqrt(sigma2 * np.linalg.inv((X * w[ok][:, None]).T @ X)[-1, -1]))
        except np.linalg.LinAlgError:
            se = np.nan
    S[ident] = coef[: ident.sum()]
    S[ident] -= np.average(S[ident], weights=mw[ident])   # level stays in the trend
    return S, beta, se


def fit_region(y, w, Rc, moy, beta_fixed=None):
    t = np.arange(len(y), dtype=float)
    if (w > 0).sum() < MIN_OBS:
        T = smooth(t, y, w, BW * 2)
        beta = 0.0 if beta_fixed is None else beta_fixed
        return T, np.zeros(12), beta, np.nan, y - T + T
    T = smooth(t, y, w, BW)
    S, beta, se = np.zeros(12), (0.0 if beta_fixed is None else beta_fixed), np.nan
    for _ in range(3):
        S, beta, se = seasonal_fit(y - T, w, Rc, moy, beta_fixed)
        T = smooth(t, y - S[moy] - beta * Rc, w, BW)
    adj = y - S[moy] - beta * Rc
    return T, S, beta, se, adj


# ----------------------------------------------------------------------------- deseason
def deseasonalize() -> None:
    xw = crosswalk()
    led = pd.read_parquet(config.LEDGER)
    led = led[led["product"] == PRODUCT]
    led = led.merge(xw[["shapeID", "bps_code", "shapeName"]], left_on="region_id", right_on="shapeID", how="inner")
    months = month_range(led["month"].min(), led["month"].max())
    regions = sorted(led["region_id"].unique())
    names = led.drop_duplicates("region_id").set_index("region_id")
    sol = led.pivot(index="region_id", columns="month", values="sol").reindex(index=regions, columns=months)
    npx = led.pivot(index="region_id", columns="month", values="n_px").reindex(index=regions, columns=months).fillna(0.0)
    peak = npx.max(axis=1).replace(0, np.nan)
    cov = (npx.T / peak).T.fillna(0.0).clip(upper=1.0)

    # flare share by (region, year) → per month; 2026 reuses the latest annual composite
    share = pd.DataFrame(0.0, index=regions, columns=months)
    flare_note = "no flare table — flare_share = 0"
    if FLARES.exists():
        fl = pd.read_parquet(FLARES)
        fl = fl[fl["year"].str.len() == 4]
        years_avail = sorted(fl["year"].unique())
        piv = fl.pivot(index="region_id", columns="year", values="share").reindex(index=regions).fillna(0.0)
        for m in months:
            yr = m[:4]
            use = yr if yr in years_avail else max([y for y in years_avail if y <= yr] or [years_avail[0]])
            share[m] = piv[use].values
        flare_note = (f"flare share from annual composites {years_avail[0]}–{years_avail[-1]} "
                      f"({config.FLARE_BUFFER_KM} km buffers)")
    print(f"[deseason] {len(regions)} regencies × {len(months)} months ({months[0]}..{months[-1]}); {flare_note}")

    deflared = sol * (1 - share)
    with np.errstate(divide="ignore", invalid="ignore"):
        Y = np.log((deflared / cov).where((cov >= C_MIN) & (deflared > 0))).to_numpy()
    W = np.where(np.isfinite(Y), np.clip(cov.to_numpy() / W_FULL, 0, 1) ** 2, 0.0)
    R = np.array([ramadan_overlap(int(m[:4]), int(m[5:])) for m in months])
    Rc = R - R.mean()
    moy = np.array([int(m[5:]) - 1 for m in months])

    # pass 1: per-regency β, then shrink toward the national estimate
    betas, ses = np.full(len(regions), np.nan), np.full(len(regions), np.nan)
    for i in range(len(regions)):
        _, _, betas[i], ses[i], _ = fit_region(Y[i], W[i], Rc, moy)
    good = np.isfinite(betas) & np.isfinite(ses) & (ses > 0)
    prec = 1 / ses[good] ** 2
    beta_nat = float(np.sum(prec * betas[good]) / prec.sum())
    tau2 = max(float(np.var(betas[good]) - np.mean(ses[good] ** 2)), 1e-4)
    shrunk = np.where(good, (betas / np.where(good, ses, 1) ** 2 + beta_nat / tau2) / (1 / np.where(good, ses, 1) ** 2 + 1 / tau2), beta_nat)
    print(f"[deseason] Ramadan β national {beta_nat:+.3f} (a fully-Ramadan month lights {np.exp(beta_nat) - 1:+.1%}); "
          f"regency β median {np.nanmedian(betas[good]):+.3f}, IQR {np.nanpercentile(betas[good], 25):+.3f}..{np.nanpercentile(betas[good], 75):+.3f}; "
          f"shrinkage τ {np.sqrt(tau2):.3f}")

    # pass 2: final fit with the shrunk β
    T = np.full_like(Y, np.nan)
    ADJ = np.full_like(Y, np.nan)
    SEAS = np.zeros((len(regions), 12))
    for i in range(len(regions)):
        T[i], SEAS[i], _, _, ADJ[i] = fit_region(Y[i], W[i], Rc, moy, beta_fixed=shrunk[i])

    sol_sa = np.exp(ADJ)
    trend = np.exp(T)
    fill = np.where(np.isfinite(sol_sa), sol_sa, trend)
    rows = []
    covv = cov.to_numpy()
    for i, rid in enumerate(regions):
        rows.append(pd.DataFrame({
            "region_id": rid, "region_name": names.at[rid, "shapeName"], "bps_code": names.at[rid, "bps_code"],
            "level": "regency", "month": months,
            "sol_raw": sol.loc[rid].to_numpy(), "n_px": npx.loc[rid].to_numpy(), "n_px_peak": float(peak[rid]),
            "coverage": covv[i], "flare_share": share.loc[rid].to_numpy(), "sol_deflared": deflared.loc[rid].to_numpy(),
            "ramadan_share": R, "sol_sa": sol_sa[i], "trend": trend[i], "sol_fill": fill[i],
            "seasonal_factor": np.exp(SEAS[i][moy]), "ramadan_beta": shrunk[i],
            "flag_low_coverage": covv[i] < C_LOW, "flag_no_data": ~np.isfinite(sol_sa[i]),
        }))
    reg = pd.concat(rows, ignore_index=True)

    # aggregates: national + provinces (sum of trend-filled regency series)
    def aggregate(df: pd.DataFrame, rid: str, name: str, code: str) -> pd.DataFrame:
        g = df.groupby("month", sort=True)
        peak_sum = df.drop_duplicates("region_id")["n_px_peak"].sum()
        out = pd.DataFrame({
            "region_id": rid, "region_name": name, "bps_code": code, "level": "national" if rid == "IDN" else "province",
            "month": months,
            "sol_raw": g["sol_raw"].sum(min_count=1).reindex(months).to_numpy(),
            "n_px": g["n_px"].sum().reindex(months).to_numpy(), "n_px_peak": peak_sum,
        })
        out["coverage"] = (out["n_px"] / peak_sum).clip(upper=1.0)
        out["flare_share"] = 1 - g["sol_deflared"].sum(min_count=1).reindex(months).to_numpy() / out["sol_raw"].replace(0, np.nan)
        out["sol_deflared"] = g["sol_deflared"].sum(min_count=1).reindex(months).to_numpy()
        out["ramadan_share"] = R
        out["sol_sa"] = g["sol_fill"].sum().reindex(months).to_numpy()
        out["trend"] = g["trend"].sum().reindex(months).to_numpy()
        out["sol_fill"] = out["sol_sa"]
        out["seasonal_factor"] = np.nan
        out["ramadan_beta"] = beta_nat
        out["flag_low_coverage"] = out["coverage"] < C_LOW
        out["flag_no_data"] = out["coverage"] < C_MIN
        return out

    aggs = [aggregate(reg, "IDN", "Indonesia", "0000")]
    for pp, sub in reg.groupby(reg["bps_code"].str[:2]):
        aggs.append(aggregate(sub, f"PROV{pp}", f"Province {pp}", f"{pp}00"))
    idx = pd.concat([reg] + aggs, ignore_index=True)
    idx.to_parquet(INDEX, index=False)

    nat = aggs[0]
    n_low = int(nat["flag_low_coverage"].sum())
    print(f"[deseason] {len(idx)} rows → {INDEX.name}; national months flagged low coverage: {n_low}/{len(months)}; "
          f"regency-months without a usable composite: {reg['flag_no_data'].mean():.1%}")
    lo, hi = nat.iloc[12], nat.iloc[-1]
    print(f"[deseason] national SA level {lo['month']} {lo['sol_sa']:,.0f} → {hi['month']} {hi['sol_sa']:,.0f}")


# ----------------------------------------------------------------------------- calibration
def fe_ols(df: pd.DataFrame, y: str, x: str, unit: str, time: str | None = None) -> dict:
    """Within estimator with cluster-robust (by unit) SE. Two-way FE via alternating demeaning."""
    d = df[[unit, y, x] + ([time] if time else [])].dropna().copy()

    def dm(col):
        v = d[col] - d.groupby(unit)[col].transform("mean")
        if time is not None:
            for _ in range(15):
                v = v - v.groupby(d[time]).transform("mean")
                v = v - v.groupby(d[unit]).transform("mean")
        return v.to_numpy()

    yt, xt = dm(y), dm(x)
    sxx = float(np.sum(xt * xt))
    beta = float(np.sum(xt * yt) / sxx)
    e = yt - beta * xt
    g = d[unit].to_numpy()
    G = len(np.unique(g))
    score = pd.Series(xt * e).groupby(g).sum().to_numpy()
    se = float(np.sqrt(np.sum(score**2) / sxx**2 * G / max(G - 1, 1)))
    return {"beta": beta, "se": se, "lo": beta - 1.96 * se, "hi": beta + 1.96 * se,
            "within_r2": float(1 - np.sum(e**2) / np.sum(yt**2)), "n": int(len(d)), "n_units": int(G),
            "fe": "unit+time" if time else "unit"}


def ols_growth(df: pd.DataFrame, y: str, x: str, unit: str) -> dict:
    """Δlog PDRB = a + β Δlog lights; cluster SE by unit; residual sd for bands."""
    d = df[[unit, y, x]].dropna()
    Y, Xv = d[y].to_numpy(), d[x].to_numpy()
    X = np.column_stack([np.ones(len(d)), Xv])
    XtX_inv = np.linalg.inv(X.T @ X)
    coef = XtX_inv @ X.T @ Y
    e = Y - X @ coef
    g = d[unit].to_numpy()
    G = len(np.unique(g))
    S = np.zeros((2, 2))
    for grp in np.unique(g):
        s = (X[g == grp] * e[g == grp, None]).sum(0)
        S += np.outer(s, s)
    V = XtX_inv @ S @ XtX_inv * G / max(G - 1, 1)
    r2 = float(1 - np.sum(e**2) / np.sum((Y - Y.mean()) ** 2))
    return {"a": float(coef[0]), "beta": float(coef[1]), "se_a": float(np.sqrt(V[0, 0])), "se": float(np.sqrt(V[1, 1])),
            "cov_ab": float(V[0, 1]), "lo": float(coef[1] - 1.96 * np.sqrt(V[1, 1])), "hi": float(coef[1] + 1.96 * np.sqrt(V[1, 1])),
            "r2": r2, "sigma": float(np.std(e, ddof=2)), "n": int(len(d)), "n_units": int(G)}


def predict_growth(model: dict, gl: np.ndarray, sigma: float):
    g = model["a"] + model["beta"] * gl
    var = model["se_a"] ** 2 + gl**2 * model["se"] ** 2 + 2 * gl * model["cov_ab"] + sigma**2
    band = 1.96 * np.sqrt(var)
    return g, g - band, g + band


def _ols_levels(x: np.ndarray, y: np.ndarray) -> dict:
    slope, intercept = np.polyfit(x, y, 1)
    fitted = intercept + slope * x
    resid = y - fitted
    r2 = float(1 - np.sum(resid**2) / np.sum((y - y.mean()) ** 2))
    return {"slope": float(slope), "intercept": float(intercept), "r2": r2, "n": int(len(x)),
            "fitted": fitted, "resid": resid}


def calibrate() -> dict:
    if not INDEX.exists():
        sys.exit("[calibrate] run `models.py deseason` first")
    if not BPS.exists():
        sys.exit("[calibrate] run `bps.py` first (BPS PDRB table missing)")
    idx = pd.read_parquet(INDEX)
    reg = idx[idx["level"] == "regency"].copy()
    nat = idx[idx["level"] == "national"].set_index("month")
    months = list(nat.index)
    latest_month = months[-1]
    bps = pd.read_parquet(BPS)
    bps = bps.dropna(subset=["xw_code"])
    annual = bps[bps["quarter"] == 0][["xw_code", "year", "pdrb", "bps_name"]]
    quarterly = bps[bps["quarter"] > 0][["xw_code", "year", "quarter", "pdrb"]]
    reg["year"] = reg["month"].str[:4].astype(int)
    reg["quarter"] = (reg["month"].str[5:].astype(int) - 1) // 3 + 1
    full_years = sorted(y for y, c in reg.groupby("year")["month"].nunique().items() if c == 12)
    flare_share_year = reg.groupby(["region_id", "year"])["flare_share"].mean()

    # --- annual lights level per regency-year (trend-filled mean) ------------------------------------
    La = reg.groupby(["region_id", "year"]).agg(
        L=("sol_fill", "mean"), L_obs=("sol_sa", "mean"), cov_year=("coverage", "mean"),
        obs_months=("sol_sa", "count"), region_name=("region_name", "first"), xw_code=("bps_code", "first"),
        n_px_peak=("n_px_peak", "first")).reset_index()
    La = La[La["year"].isin(full_years)]
    pa = La.merge(annual, on=["xw_code", "year"], how="inner")
    pa = pa[(pa["L"] > 0) & (pa["pdrb"] > 0)].copy()
    pa["lL"], pa["lP"] = np.log(pa["L"]), np.log(pa["pdrb"])
    pa["flare_share"] = [flare_share_year.get((r, y), 0.0) for r, y in zip(pa["region_id"], pa["year"])]
    cal_years = sorted(pa["year"].unique())
    latest_bps_year = max(cal_years)

    # --- G-A1: levels cross-section per year ---------------------------------------------------------
    levels = []
    pa["fitted"], pa["resid"], pa["std_resid"] = np.nan, np.nan, np.nan
    for y in cal_years:
        sel = pa["year"] == y
        fit = _ols_levels(pa.loc[sel, "lL"].to_numpy(), pa.loc[sel, "lP"].to_numpy())
        pa.loc[sel, "fitted"], pa.loc[sel, "resid"] = fit["fitted"], fit["resid"]
        pa.loc[sel, "std_resid"] = fit["resid"] / fit["resid"].std(ddof=2)
        levels.append({"year": int(y), "r2": fit["r2"], "slope": fit["slope"], "intercept": fit["intercept"], "n": fit["n"]})
        print(f"[calibrate] levels {y}: R² {fit['r2']:.3f}  slope {fit['slope']:.3f}  n {fit['n']}")
    min_r2 = min(l["r2"] for l in levels)
    gate_a1 = bool(min_r2 >= GATE_LEVELS_R2)
    print(f"[calibrate] G-A1 levels R² min {min_r2:.3f} over {cal_years[0]}–{cal_years[-1]} → {'PASS' if gate_a1 else 'FAIL'} (≥ {GATE_LEVELS_R2})")

    # --- panel elasticities ---------------------------------------------------------------------------
    fe1 = fe_ols(pa, "lP", "lL", "region_id")
    fe2 = fe_ols(pa, "lP", "lL", "region_id", time="year")
    fe1w = fe_ols(pa[pa["obs_months"] >= 6], "lP", "lL", "region_id")   # attenuation check: well-observed years only
    print(f"[calibrate] annual FE elasticity: regency FE {fe1['beta']:.3f} ± {1.96 * fe1['se']:.3f} (within R² {fe1['within_r2']:.3f}); "
          f"regency+year FE {fe2['beta']:.3f} ± {1.96 * fe2['se']:.3f} (within R² {fe2['within_r2']:.3f}); n {fe1['n']}; "
          f"≥6-observed-months subsample {fe1w['beta']:.3f} ± {1.96 * fe1w['se']:.3f} (within R² {fe1w['within_r2']:.3f}, n {fe1w['n']})")

    # annual growth (first differences)
    pa = pa.sort_values(["region_id", "year"])
    pa["dlP"] = pa.groupby("region_id")["lP"].diff()
    pa["dlL"] = pa.groupby("region_id")["lL"].diff()
    ga = ols_growth(pa, "dlP", "dlL", "region_id")
    ga_fe = fe_ols(pa, "dlP", "dlL", "region_id", time="year")
    print(f"[calibrate] annual growth: Δlog PDRB = {ga['a']:.4f} + {ga['beta']:.3f} Δlog lights (± {1.96 * ga['se']:.3f}, R² {ga['r2']:.3f}, σ {ga['sigma']:.3f}); "
          f"with year FE β {ga_fe['beta']:.3f}")

    # quarterly panel
    Lq = reg.groupby(["region_id", "year", "quarter"]).agg(
        L=("sol_fill", "mean"), cov_q=("coverage", "mean"), obs_months=("sol_sa", "count"),
        xw_code=("bps_code", "first"), region_name=("region_name", "first")).reset_index()
    pq = Lq.merge(quarterly, on=["xw_code", "year", "quarter"], how="inner")
    pq = pq[(pq["L"] > 0) & (pq["pdrb"] > 0)].copy()
    pq["lL"], pq["lP"] = np.log(pq["L"]), np.log(pq["pdrb"])
    pq["t"] = pq["year"] * 4 + pq["quarter"]
    fq2 = fe_ols(pq, "lP", "lL", "region_id", time="t")
    fq1 = fe_ols(pq, "lP", "lL", "region_id")
    pq = pq.sort_values(["region_id", "t"])
    prev = pq.set_index(["region_id", "t"])
    key_prev = list(zip(pq["region_id"], pq["t"] - 4))
    pq["dlP"] = pq["lP"].to_numpy() - prev["lP"].reindex(key_prev).to_numpy()
    pq["dlL"] = pq["lL"].to_numpy() - prev["lL"].reindex(key_prev).to_numpy()
    gq = ols_growth(pq, "dlP", "dlL", "region_id")
    gq_fe = fe_ols(pq, "dlP", "dlL", "region_id", time="t")
    q_years = sorted(pq["year"].unique())
    latest_q = pq.sort_values("t").iloc[-1]
    latest_quarter = f"{int(latest_q['year'])}Q{int(latest_q['quarter'])}"
    print(f"[calibrate] quarterly {q_years[0]}–{q_years[-1]}: FE elasticity regency+quarter {fq2['beta']:.3f} ± {1.96 * fq2['se']:.3f}; "
          f"YoY growth β {gq['beta']:.3f} ± {1.96 * gq['se']:.3f} (a {gq['a']:.4f}, R² {gq['r2']:.3f}, σ {gq['sigma']:.3f}, n {gq['n']})")

    # --- out-of-sample: latest BPS year -----------------------------------------------------------------
    def oos_annual():
        train = pa[pa["year"] < latest_bps_year]
        test = pa[pa["year"] == latest_bps_year].dropna(subset=["dlP", "dlL"]).copy()
        m = ols_growth(train, "dlP", "dlL", "region_id")
        test["pred"] = m["a"] + m["beta"] * test["dlL"]
        prev_g = pa[pa["year"] == latest_bps_year - 1].set_index("region_id")["dlP"]
        test["naive"] = test["region_id"].map(prev_g)
        test = test.dropna(subset=["naive"])
        corr = float(np.corrcoef(test["pred"], test["dlP"])[0, 1])
        mae_l, mae_n = float((test["pred"] - test["dlP"]).abs().mean()), float((test["naive"] - test["dlP"]).abs().mean())
        # provinces: PDRB-weighted aggregation of growth
        test["prov"] = test["xw_code"].str[:2]
        test["w"] = np.exp(test["lP"])
        agg = test.groupby("prov").apply(lambda d: pd.Series({
            "act": np.average(d["dlP"], weights=d["w"]), "pred": np.average(d["pred"], weights=d["w"]),
            "naive": np.average(d["naive"], weights=d["w"])}), include_groups=False)
        win = float(((agg["pred"] - agg["act"]).abs() < (agg["naive"] - agg["act"]).abs()).mean())
        return {"year": int(latest_bps_year), "n": int(len(test)), "corr": corr, "mae_lights": mae_l, "mae_naive": mae_n,
                "prov_win_rate": win, "n_prov": int(len(agg)), "beta_train": m["beta"], "a_train": m["a"],
                "regency_win_rate": float(((test["pred"] - test["dlP"]).abs() < (test["naive"] - test["dlP"]).abs()).mean())}

    def oos_quarterly():
        yq = int(pq["year"].max())
        train = pq[pq["year"] < yq].dropna(subset=["dlP", "dlL"])
        test = pq[pq["year"] == yq].dropna(subset=["dlP", "dlL"]).copy()
        if len(train) < 100 or len(test) == 0:
            return None
        m = ols_growth(train, "dlP", "dlL", "region_id")
        test["pred"] = m["a"] + m["beta"] * test["dlL"]
        naive_src = pq.set_index(["region_id", "t"])["dlP"]
        test["naive"] = naive_src.reindex(list(zip(test["region_id"], test["t"] - 4))).to_numpy()
        test = test.dropna(subset=["naive"])
        test["prov"] = test["xw_code"].str[:2]
        test["w"] = np.exp(test["lP"])
        agg = test.groupby(["prov", "t"]).apply(lambda d: pd.Series({
            "act": np.average(d["dlP"], weights=d["w"]), "pred": np.average(d["pred"], weights=d["w"]),
            "naive": np.average(d["naive"], weights=d["w"])}), include_groups=False).reset_index()
        prov = agg.groupby("prov").apply(lambda d: pd.Series({
            "mae_l": (d["pred"] - d["act"]).abs().mean(), "mae_n": (d["naive"] - d["act"]).abs().mean()}), include_groups=False)
        return {"year": yq, "n": int(len(test)), "corr": float(np.corrcoef(test["pred"], test["dlP"])[0, 1]),
                "mae_lights": float((test["pred"] - test["dlP"]).abs().mean()),
                "mae_naive": float((test["naive"] - test["dlP"]).abs().mean()),
                "prov_win_rate": float((prov["mae_l"] < prov["mae_n"]).mean()), "n_prov": int(len(prov)),
                "regency_win_rate": float(((test["pred"] - test["dlP"]).abs() < (test["naive"] - test["dlP"]).abs()).mean()),
                "beta_train": m["beta"], "a_train": m["a"]}

    oa = oos_annual()
    oq = oos_quarterly()
    print(f"[calibrate] OOS annual {oa['year']}: corr {oa['corr']:.2f}, MAE lights {oa['mae_lights']:.4f} vs naive {oa['mae_naive']:.4f}, "
          f"provinces beaten {oa['prov_win_rate']:.0%} of {oa['n_prov']}")
    if oq:
        print(f"[calibrate] OOS quarterly {oq['year']}: corr {oq['corr']:.2f}, MAE lights {oq['mae_lights']:.4f} vs naive {oq['mae_naive']:.4f}, "
              f"provinces beaten {oq['prov_win_rate']:.0%} of {oq['n_prov']} → G-A2 {'PASS' if oq['prov_win_rate'] >= GATE_NOWCAST_WIN else 'FAIL'}")

    # --- BPS reference growth series (sum of the 514 regencies) -------------------------------------------
    nat_a = annual.groupby("year")["pdrb"].sum().sort_index()
    bps_annual = [{"year": int(y), "g": float(np.log(nat_a[y] / nat_a[y - 1]))} for y in nat_a.index if y - 1 in nat_a.index]
    nat_q = quarterly.groupby(["year", "quarter"])["pdrb"].sum().sort_index()
    bps_quarterly = []
    for (y, q), v in nat_q.items():
        if (y - 1, q) in nat_q.index:
            bps_quarterly.append({"q": f"{y}Q{q}", "year": int(y), "quarter": int(q), "g": float(np.log(v / nat_q[(y - 1, q)]))})

    # --- nowcast: national monthly (trailing 3-month window, YoY) through the quarterly growth model -----
    model = gq
    nf = nat["sol_fill"].to_numpy()
    roll = pd.Series(nf).rolling(3).sum().to_numpy()
    gl = np.full(len(months), np.nan)
    gl[12:] = np.log(roll[12:] / roll[:-12])
    gl1 = np.full(len(months), np.nan)
    gl1[12:] = np.log(nf[12:] / nf[:-12])
    # national residual sd: quarterly BPS YoY vs the model applied to the national lights aggregate
    nat_q_lights = nat.assign(year=nat.index.str[:4].astype(int), quarter=(nat.index.str[5:].astype(int) - 1) // 3 + 1) \
                      .groupby(["year", "quarter"])["sol_fill"].mean()
    resid_nat = []
    for row in bps_quarterly:
        k, kp = (row["year"], row["quarter"]), (row["year"] - 1, row["quarter"])
        if k in nat_q_lights.index and kp in nat_q_lights.index:
            resid_nat.append(row["g"] - (model["a"] + model["beta"] * np.log(nat_q_lights[k] / nat_q_lights[kp])))
    sigma_nat = float(np.sqrt(np.mean(np.square(resid_nat)))) if len(resid_nat) >= 4 else model["sigma"]
    g, lo, hi = predict_growth(model, gl, sigma_nat)
    last_bps_q_month = f"{latest_q['year']:.0f}-{int(latest_q['quarter']) * 3:02d}"
    series = []
    for i, m in enumerate(months):
        if not np.isfinite(gl[i]):
            continue
        series.append({"m": m, "gl": float(gl[i]), "gl1": float(gl1[i]) if np.isfinite(gl1[i]) else None,
                       "g": float(g[i]), "lo": float(lo[i]), "hi": float(hi[i]),
                       "cov": float(nat.loc[m, "coverage"]), "nowcast": m > last_bps_q_month})
    head = series[-1]
    q_last = bps_quarterly[-1] if bps_quarterly else None
    headline = {
        "month": latest_month, "window": f"{months[-3]}..{latest_month}", "g": head["g"], "lo": head["lo"], "hi": head["hi"],
        "lights_growth": head["gl"], "bps_last_quarter": q_last, "bps_last_annual": bps_annual[-1] if bps_annual else None,
        "model": "quarterly YoY growth calibration (a + β·Δlog lights), σ from national residuals",
        "months_beyond_bps": len([s for s in series if s["nowcast"]]),
    }
    print(f"[nowcast] {headline['window']}: lights-implied YoY activity growth {head['g']:+.1%} "
          f"[{head['lo']:+.1%}, {head['hi']:+.1%}] (lights {head['gl']:+.1%}); last BPS quarter "
          f"{q_last['q'] if q_last else '—'} {q_last['g']:+.1%}" if q_last else "")

    # --- regency nowcast + movers -------------------------------------------------------------------------
    piv_sa = reg.pivot(index="region_id", columns="month", values="sol_sa").reindex(columns=months)
    piv_fill = reg.pivot(index="region_id", columns="month", values="sol_fill").reindex(columns=months)
    piv_cov = reg.pivot(index="region_id", columns="month", values="coverage").reindex(columns=months)
    cur, prev_w = months[-3:], months[-15:-12]
    obs_ok = ((piv_cov[cur] >= C_LOW).sum(axis=1) >= 2) & ((piv_cov[prev_w] >= C_LOW).sum(axis=1) >= 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        gl_obs = np.log(piv_sa[cur].mean(axis=1) / piv_sa[prev_w].mean(axis=1))
        gl_fill = np.log(piv_fill[cur].mean(axis=1) / piv_fill[prev_w].mean(axis=1))
    gl_r = np.where(obs_ok & np.isfinite(gl_obs), gl_obs, gl_fill)
    gr, lor, hir = predict_growth(model, gl_r, model["sigma"])
    meta = reg.drop_duplicates("region_id").set_index("region_id")
    last_share = reg[reg["month"] == latest_month].set_index("region_id")["flare_share"]
    nc = pd.DataFrame({"region_id": piv_sa.index, "region_name": meta.loc[piv_sa.index, "region_name"].to_numpy(),
                       "bps_code": meta.loc[piv_sa.index, "bps_code"].to_numpy(),
                       "window": headline["window"], "lights_growth": gl_r, "g": gr, "lo": lor, "hi": hir,
                       "observed": obs_ok.to_numpy(), "flare_share": last_share.reindex(piv_sa.index).to_numpy()})
    nc["flare_flag"] = nc["flare_share"] >= FLARE_FLAG
    nc["level_12m"] = piv_fill[months[-12:]].mean(axis=1).to_numpy()
    nc.to_parquet(NOWCAST, index=False)
    # movers are ranked on the OBSERVABLE (deseasonalised lights growth), not the mapped growth —
    # the growth β is so small that mapped values compress to the mean. Size floor keeps tiny,
    # noisy regencies out of the board.
    floor = max(300.0, float(np.nanmedian(nc["level_12m"])))
    rank = nc[nc["observed"] & ~nc["flare_flag"] & np.isfinite(nc["lights_growth"])
              & (nc["level_12m"] >= floor) & (nc["lights_growth"].abs() <= 1.0)]
    movers = lambda df: [{"id": r.region_id, "name": r.region_name, "g": float(r.g), "lo": float(r.lo), "hi": float(r.hi),
                          "lights_growth": float(r.lights_growth)} for r in df.itertuples()]
    risers, fallers = movers(rank.nlargest(6, "lights_growth")), movers(rank.nsmallest(6, "lights_growth"))
    print("[nowcast] lights risers: " + ", ".join(f"{r['name']} {r['lights_growth']:+.0%}" for r in risers[:3]) +
          " · fallers: " + ", ".join(f"{r['name']} {r['lights_growth']:+.0%}" for r in fallers[:3]))

    # --- weak fit (latest year levels) ---------------------------------------------------------------------
    last = pa[pa["year"] == latest_bps_year].copy()
    last["abs_sr"] = last["std_resid"].abs()
    weak = []
    for r in last.nlargest(12, "abs_sr").itertuples():
        tags = []
        if r.flare_share >= FLARE_FLAG:
            tags.append("gas flares")
        if str(r.xw_code).startswith("31"):
            tags.append("DKI service economy")
        if r.n_px_peak < 500:
            tags.append("small area (<500 px)")
        if r.cov_year < 0.35:
            tags.append("low annual coverage")
        weak.append({"id": r.region_id, "name": r.region_name, "std_resid": float(r.std_resid), "resid": float(r.resid),
                     "lights_vs_fit": float(np.exp(-r.resid) - 1), "tags": tags, "pdrb": float(r.pdrb), "L": float(r.L)})
    n_weak = int((last["abs_sr"] > 2).sum())
    print(f"[calibrate] weak fit {latest_bps_year}: {n_weak} regencies with |std resid| > 2 — " +
          ", ".join(f"{w['name']} ({w['std_resid']:+.1f}σ{'; ' + '/'.join(w['tags']) if w['tags'] else ''})" for w in weak[:6]))

    # --- flare summary --------------------------------------------------------------------------------------
    flares = None
    if FLARES.exists():
        fl = pd.read_parquet(FLARES)
        fla = fl[fl["year"].str.len() == 4]
        yr = fla["year"].max()
        cur_f = fla[fla["year"] == yr]
        xw_ids = set(reg["region_id"].unique())
        cur_f = cur_f[cur_f["region_id"].isin(xw_ids)]
        top = cur_f[cur_f["share"] >= 0.01].nlargest(12, "share")
        monthly = fl[fl["year"].str.len() == 7]
        flares = {
            "source": "Elvidge & Zhizhin (2021), Global Gas Flare Survey by Infrared Imaging, VIIRS Nightfire 2012–2019, ORNL DAAC",
            "doi": "10.3334/ORNLDAAC/1874", "licence": "NASA EOSDIS data policy: openly shared, without restriction (cite the dataset)",
            "buffer_km": config.FLARE_BUFFER_KM, "min_survey_years": 3,
            "buffer_note": "3 km captures ~84% of an isolated flare's excess light (measured on the 2025 composite); "
                           "the spec's 5 km swallows whole towns and is kept as a sensitivity column",
            "year": str(yr), "n_sites": int(cur_f["n_sites"].sum()),
            "national_share": float(cur_f["sol_flare"].sum() / cur_f["sol_total"].sum()),
            "national_share_5km": float(cur_f["sol_flare_wide"].sum() / cur_f["sol_total"].sum()),
            "national_share_15km": float(cur_f["sol_flare_tight"].sum() / cur_f["sol_total"].sum()),
            "n_regencies_flagged": int((cur_f["share"] >= FLARE_FLAG).sum()),
            "regencies": [{"id": r.region_id, "name": r.region_name, "share": float(r.share),
                           "share_wide": float(r.share_wide), "share_tight": float(r.share_tight),
                           "sites": int(r.n_sites)} for r in top.itertuples()],
            "monthly_check": ({"month": monthly["year"].iat[0],
                               "national_share": float(monthly["sol_flare"].sum() / monthly["sol_total"].sum())}
                              if len(monthly) else None),
        }

    stats = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "latest_month": latest_month,
        "bps": {"annual_var": 2194, "quarterly_var": 2534, "domain": "0000", "annual_years": [int(y) for y in sorted(annual["year"].unique())],
                "quarterly_years": [int(y) for y in q_years], "latest_annual": int(latest_bps_year), "latest_quarter": latest_quarter,
                "n_regencies": int(annual["xw_code"].nunique())},
        "gates": {"G-A1": {"pass": gate_a1, "min_r2": min_r2, "threshold": GATE_LEVELS_R2, "years": [cal_years[0], cal_years[-1]],
                           "note": "levels cross-section only; scoped to 2018→ (ledger start), spec says 2015→"},
                  "G-A2": ({"pass": bool(oq["prov_win_rate"] >= GATE_NOWCAST_WIN), "prov_win_rate": oq["prov_win_rate"], "threshold": GATE_NOWCAST_WIN,
                            "note": f"one out-of-sample year ({oq['year']}, quarterly YoY), not the 2016–2024 rolling backtest of the spec"}
                           if oq else {"pass": None, "note": "quarterly BPS series too short"})},
        "levels": levels,
        "panel": {"annual_fe_regency": fe1, "annual_fe_regency_year": fe2, "annual_fe_regency_wellobs": fe1w,
                  "quarterly_fe_regency": fq1, "quarterly_fe_regency_quarter": fq2},
        "growth": {"annual": ga, "annual_year_fe_beta": ga_fe["beta"], "quarterly_yoy": gq, "quarterly_yoy_time_fe_beta": gq_fe["beta"],
                   "sigma_national": sigma_nat},
        "oos": {"annual": oa, "quarterly": oq},
        "nowcast": {"model": "quarterly_yoy", "series": series, "headline": headline, "bps_annual": bps_annual, "bps_quarterly": bps_quarterly,
                    "risers": risers, "fallers": fallers, "last_bps_quarter_month": last_bps_q_month},
        "weak_fit": {"year": int(latest_bps_year), "n_over_2sigma": n_weak, "list": weak},
        "deseason": {"ramadan_beta_national": float(nat["ramadan_beta"].iat[0]),
                     "ramadan_effect_full_month": float(np.exp(nat["ramadan_beta"].iat[0]) - 1),
                     "ramadan_beta_regency_quantiles": {k: float(v) for k, v in reg.drop_duplicates("region_id")["ramadan_beta"].quantile([0.1, 0.25, 0.5, 0.75, 0.9]).items()},
                     "national_low_coverage_months": int(nat["flag_low_coverage"].sum()), "months": len(months),
                     "regency_months_no_data": float(reg["flag_no_data"].mean()),
                     "weights": f"w = min(1, coverage/{W_FULL})², zero below {C_MIN:.0%}; low-coverage flag < {C_LOW:.0%}"},
        "flares": flares,
    }
    config.STATS_JSON.write_text(json.dumps(stats, indent=1, allow_nan=False, default=_json_default))
    pa.drop(columns=["fitted"]).to_parquet(CALIB, index=False)
    print(f"[calibrate] stats → {config.STATS_JSON.name}; {len(pa)} regency-years → {CALIB.name}")
    return stats


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(f"not serialisable: {type(o)}")


def nowcast() -> None:
    """The nowcast is produced inside calibrate(); this stage re-runs it so `make month` stays one DAG."""
    calibrate()


def validate() -> int:
    if not config.STATS_JSON.exists():
        print("[validate] stats.json missing — run calibrate first")
        return 1
    stats = json.loads(config.STATS_JSON.read_text())
    rc = 0
    for gate, res in stats["gates"].items():
        status = {True: "PASS", False: "FAIL", None: "n/a"}[res.get("pass")]
        print(f"[validate] {gate}: {status} — {res.get('note', '')}")
        if gate == "G-A1" and res.get("pass") is False:
            rc = 1
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["deseason", "calibrate", "nowcast", "validate"])
    args = parser.parse_args()
    if args.stage == "validate":
        return validate()
    {"deseason": deseasonalize, "calibrate": calibrate, "nowcast": nowcast}[args.stage]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
