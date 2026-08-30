"""Stage 6 · equity — how unequally the hour is shared.

  Lorenz + Gini + Palma of population-weighted 60-minute jobs-proxy access across kelurahan
  (per scenario, so the rail and bus layers can be priced).
  DKI vs Bodetabek population-weighted medians — the core/periphery split.
  Mode-layer attribution: the Gini and the median access with and without rail, and with
  walking only, i.e. what each system buys.
  Access vs poverty: Case F's kecamatan estimates when they exist (build-order dependency
  F → G); when they do not, the link is reported as pending rather than faked.
  Winners and losers: the best- and worst-served kelurahan, and the Menteng/Bekasi pairing.

Output: data/equity.json.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

import config
import util
from util import log


def lorenz(values: np.ndarray, weights: np.ndarray, points_out: int = 60):
    """Population-weighted Lorenz curve of an access measure; returns (curve, gini, palma)."""
    o = np.argsort(values)
    v, w = np.asarray(values)[o], np.asarray(weights)[o]
    tot_w, tot_v = w.sum(), (v * w).sum()
    if tot_w <= 0 or tot_v <= 0:
        return [], float("nan"), float("nan")
    x = np.cumsum(w) / tot_w
    y = np.cumsum(v * w) / tot_v
    gini = float(1.0 - np.sum((x[1:] - x[:-1]) * (y[1:] + y[:-1])))
    grid = np.linspace(0, 1, points_out)
    curve = [[round(float(a), 4), round(float(b), 4)]
             for a, b in zip(grid, np.interp(grid, np.concatenate([[0], x]), np.concatenate([[0], y])))]
    top10 = float(np.interp(1.0, x, y) - np.interp(0.9, x, y))
    bot40 = float(np.interp(0.4, x, y))
    palma = float(top10 / bot40) if bot40 > 1e-9 else None
    return curve, (gini if np.isfinite(gini) else None), palma


def wmedian(v, w) -> float:
    o = np.argsort(v)
    v, w = np.asarray(v)[o], np.asarray(w)[o]
    c = np.cumsum(w)
    if c[-1] <= 0:
        return float("nan")
    return float(v[np.searchsorted(c, c[-1] / 2.0)])


def poverty_link(a60: pd.DataFrame) -> dict:
    """Case F cross-link — only if Case F has actually produced its estimates."""
    p = config.CASE_F_ESTIMATES
    if not p.exists():
        return {"available": False,
                "reason": f"Case F estimates not found at {p.relative_to(config.REPO_ROOT)} — "
                          "the F → G build-order dependency is unmet; the poverty axis is pending."}
    try:
        from scipy.stats import spearmanr
        f = pd.read_parquet(p)
        key = next((c for c in f.columns if "adm3" in c.lower() and "code" in c.lower()), None)
        val = next((c for c in f.columns if "pov" in c.lower() or "p0" in c.lower()), None)
        if key is None or val is None:
            return {"available": False, "reason": f"unrecognised Case F schema: {list(f.columns)[:12]}"}
        a = a60.copy()
        a["adm3_pcode"] = a["id"].str[:-3]
        j = a.merge(f[[key, val]].rename(columns={key: "adm3_pcode", val: "poverty"}),
                    on="adm3_pcode", how="inner")
        if len(j) < 50:
            return {"available": False, "reason": f"only {len(j)} kelurahan matched Case F"}
        rho, pval = spearmanr(j["jobs_share"], j["poverty"])
        qa = j["jobs_share"].quantile(0.2)
        qp = j["poverty"].quantile(0.8)
        dd = j[(j["jobs_share"] <= qa) & (j["poverty"] >= qp)]
        return {"available": True, "matched": int(len(j)), "spearman_rho": float(rho),
                "p_value": float(pval), "double_disadvantage_count": int(len(dd)),
                "double_disadvantage_pop": float(dd["pop"].sum()),
                "double_disadvantage": json.loads(dd.nlargest(25, "pop")[
                    ["id", "adm4_name", "adm2_name", "jobs_share", "poverty", "pop"]].to_json(orient="records"))}
    except Exception as e:                                  # never let the cross-link break the build
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


def main() -> None:
    acc = pd.read_parquet(config.ACCESS_ADM4)
    out: dict = {"cutoff_min": 60, "window": list(config.DEPARTURE_WINDOW),
                 "date": config.DEPARTURE_DATE, "measure": "share of Jabodetabek job-dense "
                 "floorspace (GHS-BUILT-S NRES) reachable, scheduled p50", "scenarios": {}}
    for s in sorted(acc.scenario.unique()):
        a = acc[(acc.scenario == s) & (acc.cutoff == 60)]
        curve, gini, palma = lorenz(a.jobs_share.values, a["pop"].values)
        dki = a[a.adm1_name.str.contains("Jakarta", case=False, na=False)]
        bod = a[~a.adm1_name.str.contains("Jakarta", case=False, na=False)]
        out["scenarios"][s] = {
            "gini": None if gini is None else round(gini, 4),
            "palma": None if palma is None or not np.isfinite(palma) else round(palma, 3),
            "palma_note": None if palma is not None else
                "undefined — the bottom 40 % of the population reach essentially nothing",
            "lorenz": curve,
            "median_jobs_share": round(wmedian(a.jobs_share.values, a["pop"].values), 4),
            "mean_jobs_share": round(float(np.average(a.jobs_share, weights=a["pop"])), 4),
            "dki_median": round(wmedian(dki.jobs_share.values, dki["pop"].values), 4),
            "bodetabek_median": round(wmedian(bod.jobs_share.values, bod["pop"].values), 4),
            "median_nearest_hospital_min": (float(np.nanmedian(a.nearest_hosp_min))
                                            if a.nearest_hosp_min.notna().any() else None),
            "pop_no_hospital_60min": float(a.loc[a.hospitals == 0, "pop"].sum()),
            "pop_share_no_hospital_60min": float(a.loc[a.hospitals == 0, "pop"].sum() / a["pop"].sum()),
        }
    if "all" in out["scenarios"] and "no_rail" in out["scenarios"]:
        A, N = out["scenarios"]["all"], out["scenarios"]["no_rail"]
        out["rail_contribution"] = {
            "gini_delta": None if A["gini"] is None or N["gini"] is None else round(A["gini"] - N["gini"], 4),
            "median_access_delta": round(A["median_jobs_share"] - N["median_jobs_share"], 4),
            "mean_access_delta": round(A["mean_jobs_share"] - N["mean_jobs_share"], 4),
            "note": "what the KRL/MRT/LRT layer adds on top of TransJakarta + walking; "
                    "rail times are hand-encoded from published headways (±15 %)."}
    if "all" in out["scenarios"] and "walk" in out["scenarios"]:
        A, W = out["scenarios"]["all"], out["scenarios"]["walk"]
        out["transit_contribution"] = {
            "gini_delta": None if A["gini"] is None or W["gini"] is None else round(A["gini"] - W["gini"], 4),
            "median_access_delta": round(A["median_jobs_share"] - W["median_jobs_share"], 4),
            "mean_access_delta": round(A["mean_jobs_share"] - W["mean_jobs_share"], 4),
            "note": "the whole public-transport system against walking alone."}

    a60 = acc[(acc.scenario == "all") & (acc.cutoff == 60)].copy()
    cols = ["id", "adm4_name", "adm3_name", "adm2_name", "jobs_share", "hospitals",
            "nearest_hosp_min", "pop"]
    def recs(df):
        return json.loads(df[cols].round(4).to_json(orient="records"))   # NaN → null
    out["best"] = recs(a60.nlargest(15, "jobs_share"))
    out["worst"] = recs(a60[a60["pop"] > 5000].nsmallest(15, "jobs_share"))
    for label, needle in (("menteng", "Menteng"), ("bekasi", "Bekasi")):
        sel = a60[a60.adm3_name.str.contains(needle, case=False, na=False)]
        if len(sel):
            out.setdefault("pairing", {})[label] = {
                "kelurahan": int(len(sel)),
                "pop_weighted_jobs_share": round(float(np.average(sel.jobs_share, weights=sel["pop"])), 4),
                "median_nearest_hospital_min": (float(np.nanmedian(sel.nearest_hosp_min))
                                                if sel.nearest_hosp_min.notna().any() else None),
                "pop": float(sel["pop"].sum())}
    out["poverty_link"] = poverty_link(a60)
    out["kepulauan_seribu_note"] = (
        "Kepulauan Seribu kelurahan are kept in every aggregate (boat-only access); excluding "
        "them is decision 5 pending user verification.")
    config.EQUITY_JSON.write_text(json.dumps(out, indent=2, allow_nan=False))
    log("equity →", config.EQUITY_JSON)
    for s, v in out["scenarios"].items():
        log(f"  {s}: Gini {v['gini']} Palma {v['palma']} "
            f"median {v['median_jobs_share']:.1%} DKI {v['dki_median']:.1%} "
            f"Bodetabek {v['bodetabek_median']:.1%}")


if __name__ == "__main__":
    main()
