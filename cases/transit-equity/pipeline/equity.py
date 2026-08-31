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
    """Case F cross-link — only if Case F has actually produced its estimates.

    Case F publishes one row per kecamatan per year: `pcode` is the ADM3 P-code (ID + 7
    digits, the kelurahan P-code with its last three characters removed) and `p0_est` is the
    benchmarked small-area poverty headcount. Earlier code looked for a column whose name
    contained both "adm3" and "code", found none, and reported the schema as unrecognised —
    so the equity axis stayed pending after Case F had in fact published. Column choice is
    explicit here, and the year is pinned to the latest, so a silent mismatch cannot recur.
    """
    p = config.CASE_F_ESTIMATES
    if not p.exists():
        return {"available": False,
                "reason": f"Case F estimates not found at {p.relative_to(config.REPO_ROOT)} — "
                          "the F → G build-order dependency is unmet; the poverty axis is pending."}
    try:
        from scipy.stats import spearmanr
        f = pd.read_parquet(p)
        key = "pcode" if "pcode" in f.columns else next(
            (c for c in f.columns if "adm3" in c.lower() and "code" in c.lower()), None)
        val = next((c for c in ("p0_est", "poverty", "official_p0") if c in f.columns), None)
        if key is None or val is None:
            return {"available": False, "reason": f"unrecognised Case F schema: {list(f.columns)[:12]}"}
        year = int(f["year"].max()) if "year" in f.columns else None
        if year is not None:
            f = f[f["year"] == year]
        a = a60.copy()
        a["adm3_pcode"] = a["id"].str[:-3]
        j = a.merge(f[[key, val]].rename(columns={key: "adm3_pcode", val: "poverty"}),
                    on="adm3_pcode", how="inner")
        if len(j) < 50:
            return {"available": False, "reason": f"only {len(j)} kelurahan matched Case F"}
        rho, pval = spearmanr(j["jobs_share"], j["poverty"])
        # Population-weighted concentration index of access over the poverty ranking:
        # positive means access accrues to the less poor. This is the distributional
        # statistic the word "equity" needs; a rank correlation alone does not weight people.
        o = j.sort_values("poverty", ascending=False)
        w = o["pop"].to_numpy(float)
        r = (np.cumsum(w) - 0.5 * w) / w.sum()
        mu = float(np.average(o["jobs_share"], weights=w))
        ci = float(2 * np.average((o["jobs_share"].to_numpy(float) - mu) * (r - 0.5), weights=w) / mu)
        q = pd.qcut(j["poverty"], 5, labels=False, duplicates="drop")
        quint = [{"q": int(k) + 1,
                  "poverty_lo": round(float(g["poverty"].min()), 2),
                  "poverty_hi": round(float(g["poverty"].max()), 2),
                  "mean_access": round(float(np.average(g["jobs_share"], weights=g["pop"])), 6),
                  "median_access": round(float(wmedian(g["jobs_share"].values, g["pop"].values)), 6),
                  "zero_share": round(float((g["jobs_share"] <= 0).mean()), 4),
                  "pop": round(float(g["pop"].sum()), 0), "units": int(len(g))}
                 for k, g in j.groupby(q)]
        qa = j["jobs_share"].quantile(0.2)
        qp = j["poverty"].quantile(0.8)
        dd = j[(j["jobs_share"] <= qa) & (j["poverty"] >= qp)]
        return {"available": True, "matched": int(len(j)), "source_year": year,
                "kecamatan_matched": int(j["adm3_pcode"].nunique()),
                "spearman_rho": float(rho), "p_value": float(pval),
                "concentration_index": round(ci, 4), "quintiles": quint,
                "double_disadvantage_count": int(len(dd)),
                "double_disadvantage_pop": float(dd["pop"].sum()),
                "double_disadvantage_pop_share": float(dd["pop"].sum() / j["pop"].sum()),
                "note": "poverty is Case F's benchmarked kecamatan estimate, so it is constant "
                        "within a kecamatan; access varies within one.",
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
            # Six decimals: the medians here are ~2e-3 and their ratio is a headline, so
            # rounding at 1e-4 made the same gap read 86x here and 88x in by_cutoff.
            "median_jobs_share": round(wmedian(a.jobs_share.values, a["pop"].values), 6),
            "mean_jobs_share": round(float(np.average(a.jobs_share, weights=a["pop"])), 6),
            "dki_median": round(wmedian(dki.jobs_share.values, dki["pop"].values), 6),
            "bodetabek_median": round(wmedian(bod.jobs_share.values, bod["pop"].values), 6),
            "median_nearest_hospital_min": (float(np.nanmedian(a.nearest_hosp_min))
                                            if a.nearest_hosp_min.notna().any() else None),
            "pop_no_hospital_60min": float(a.loc[a.hospitals == 0, "pop"].sum()),
            "pop_share_no_hospital_60min": float(a.loc[a.hospitals == 0, "pop"].sum() / a["pop"].sum()),
        }
    if "all" in out["scenarios"] and "no_rail" in out["scenarios"]:
        A, N = out["scenarios"]["all"], out["scenarios"]["no_rail"]
        out["rail_contribution"] = {
            "gini_delta": None if A["gini"] is None or N["gini"] is None else round(A["gini"] - N["gini"], 4),
            "median_access_delta": round(A["median_jobs_share"] - N["median_jobs_share"], 6),
            "mean_access_delta": round(A["mean_jobs_share"] - N["mean_jobs_share"], 6),
            "note": "what the KRL/MRT/LRT layer adds on top of TransJakarta + walking; "
                    "rail times are hand-encoded from published headways (±15 %)."}
    if "all" in out["scenarios"] and "walk" in out["scenarios"]:
        A, W = out["scenarios"]["all"], out["scenarios"]["walk"]
        out["transit_contribution"] = {
            "gini_delta": None if A["gini"] is None or W["gini"] is None else round(A["gini"] - W["gini"], 4),
            "median_access_delta": round(A["median_jobs_share"] - W["median_jobs_share"], 6),
            "mean_access_delta": round(A["mean_jobs_share"] - W["mean_jobs_share"], 6),
            "note": "the whole public-transport system against walking alone."}

    # The hour is a choice, so publish what the other choices give. A cumulative-opportunity
    # measure is only as stable as its cutoff, and here it is not stable at all: inequality
    # rises steeply with the time budget because a longer hour compounds for the already
    # connected and does nothing for anyone with no service to compound.
    by_cut = []
    for c in sorted(acc.cutoff.unique()):
        a = acc[(acc.scenario == "all") & (acc.cutoff == c)]
        curve, gini, palma = lorenz(a.jobs_share.values, a["pop"].values)
        dki = a[a.adm1_name.str.contains("Jakarta", case=False, na=False)]
        bod = a[~a.adm1_name.str.contains("Jakarta", case=False, na=False)]
        dm = wmedian(dki.jobs_share.values, dki["pop"].values)
        bm = wmedian(bod.jobs_share.values, bod["pop"].values)
        by_cut.append({
            "cutoff": int(c),
            "gini": None if gini is None else round(gini, 4),
            "palma": None if palma is None or not np.isfinite(palma) else round(palma, 3),
            "median_jobs_share": round(float(wmedian(a.jobs_share.values, a["pop"].values)), 6),
            "mean_jobs_share": round(float(np.average(a.jobs_share, weights=a["pop"])), 6),
            "dki_median": round(float(dm), 6), "bodetabek_median": round(float(bm), 6),
            "ratio": round(float(dm / bm), 1) if bm > 0 else None,
            "pop_share_no_hospital": round(float(a.loc[a.hospitals == 0, "pop"].sum() / a["pop"].sum()), 4),
            "lorenz": curve})
    out["by_cutoff"] = by_cut

    # Who captures what each layer adds. A mean delta says how much opportunity the system
    # creates; it does not say who gets it, and the answer here is not the median resident.
    piv = acc[acc.cutoff == 60].pivot_table(index="id", columns="scenario", values="jobs_share")
    meta = acc[(acc.scenario == "all") & (acc.cutoff == 60)].set_index("id")[["pop", "adm1_name"]]
    inc_df = piv.join(meta).dropna(subset=["pop"])
    inc_df["dki"] = inc_df.adm1_name.str.contains("Jakarta", case=False, na=False)
    tot_pop = float(inc_df["pop"].sum())
    incidence = {"dki_pop_share": round(float(inc_df.loc[inc_df.dki, "pop"].sum() / tot_pop), 4)}
    for layer, base in (("rail", "no_rail"), ("transit", "walk")):
        if base not in inc_df.columns or "all" not in inc_df.columns:
            continue
        d = inc_df["all"] - inc_df[base]
        gain = d * inc_df["pop"]
        tot = float(gain.sum())
        srt = inc_df.assign(g=gain, d=d).sort_values("d")
        cum = srt["pop"].cumsum() / tot_pop
        incidence[layer] = {
            "mean_delta": round(float(tot / tot_pop), 6),
            "dki_share_of_gain": round(float(gain[inc_df.dki].sum() / tot), 4),
            "top_decile_share_of_gain": round(float(srt.loc[cum > 0.9, "g"].sum() / tot), 4),
            "dki_mean_delta": round(float(np.average(d[inc_df.dki], weights=inc_df.loc[inc_df.dki, "pop"])), 6),
            "bodetabek_mean_delta": round(float(np.average(d[~inc_df.dki], weights=inc_df.loc[~inc_df.dki, "pop"])), 6)}
    out["incidence"] = incidence

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
