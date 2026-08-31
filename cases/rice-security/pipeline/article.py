"""Build the review article's data layer from the case's own published output.

Two inputs, both produced by this pipeline: the published ``web/public/data/summary.json``
and ``data/audit.json`` (what the review recomputed from the same parquets).  Every number the
article prints comes from here, so the prose cannot drift from the pipeline, and the article
pins a data vintage the way a paper pins its sample.

Usage:  python pipeline/article.py web/public/data/summary.json data/audit.json \
            web/src/data/article.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def gate(stats, gid):
    return next(g for g in stats["gates"] if g["id"] == gid)


def main() -> int:
    stats = json.load(open(sys.argv[1]))
    aud = json.load(open(sys.argv[2]))
    out_path = Path(sys.argv[3])
    A, B, C, D, E, F, G, H, I = (aud[k] for k in (
        "A_decomposition", "B_by_crop_class", "C_seasonal", "D_revisit", "E_thinning",
        "F_calibration", "G_timing", "H_benchmark", "I_prf"))

    g1, g2 = gate(stats, "G-I1"), gate(stats, "G-I2")
    g3, g4, g5 = gate(stats, "G-I3"), gate(stats, "G-I4"), gate(stats, "G-I5")
    kabs = stats["kabupaten"]

    o = {
        "vintage": stats["generated_utc"][:10],
        "generated": stats["generated_utc"],
        "sar_window": stats["scope"]["sar_window"],
        "seasons": stats["scope"]["seasons"],
        "cells": stats["scope"]["cells"],
        "cell_m": stats["scope"]["cell_m"],
        "kabupaten_ha": stats["scope"]["kabupaten_ha"],
        "provinces": stats["scope"]["provinces"],
        "cycles": sum(k["cycles"] for k in kabs),
        "acquisitions": sum(k["acquisitions"] for k in kabs),
        "full_years": aud["full_years"],
    }

    # ── the five published checks, by descriptive name — no internal codes on a reader's page ──
    o["checks"] = [
        {"n": 1, "name": "Reconciliation with the official survey", "pass": g1["pass"],
         "detail": (f"uncalibrated R² {g1['variants']['uncalibrated']['kabupaten_r2']}, "
                    f"MAPE {g1['variants']['uncalibrated']['kabupaten_mape']}%; "
                    f"calibrated R² {g1['variants']['calibrated']['kabupaten_r2']} on the annual "
                    f"panel")},
        {"n": 2, "name": "Harvest timing", "pass": g2["pass"],
         "detail": (f"median |error| {g2['median_abs_error_weeks']} weeks against a "
                    f"{g2['threshold']['median_abs_error_weeks']}-week threshold")},
        {"n": 3, "name": "Cropping intensity", "pass": g3["pass"],
         "detail": "irrigated units " + ", ".join(
             f"{k} {v['cycles_per_year']}" for k, v in g3["units"].items())},
        {"n": 4, "name": "Agreement with an independent rice map", "pass": g4["pass"],
         "detail": (f"{g4['agreement_on_prior']:.1%} of the published map reproduced against a "
                    f"{g4['threshold']['agreement']:.0%} threshold")},
        {"n": 5, "name": "Held-out season", "pass": g5["pass"],
         "detail": (f"uncalibrated {g5['uncalibrated']['diff_pct']}% on the sum, "
                    f"MAPE {g5['uncalibrated']['mape']}%")},
    ]
    o["checks_passed"] = stats["gates_passed"]
    o["g1"] = {v: {k: g1["variants"][v][k] for k in
                   ("aggregate_by_year", "worst_abs_diff_pct", "kabupaten_r2", "kabupaten_mape")}
               for v in ("uncalibrated", "calibrated")}
    o["g2"] = {"median_abs_error_weeks": g2["median_abs_error_weeks"],
               "threshold_weeks": g2["threshold"]["median_abs_error_weeks"],
               "unit_bias_weeks": g2["unit_bias_weeks"],
               "worst_unit_bias_weeks": g2["worst_unit_bias_weeks"]}
    o["g3"] = {"band": g3["threshold"]["irrigated"],
               "units": {k: v["cycles_per_year"] for k, v in g3["units"].items()}}
    o["g4"] = {k: g4[k] for k in ("agreement_on_prior", "agreement_on_ours", "prior_rice_cells",
                                  "detected_cells", "prior_only_cells", "ours_only_cells")}
    o["g4"]["threshold"] = g4["threshold"]["agreement"]

    # ── finding 1 · the benchmark, and whether it moves ───────────────────────────────────
    o["benchmark"] = {
        "break_year": H["break_year"], "break_pct": H["break_pct"],
        "pre_ksa_ha": H["pre_ksa_ha"], "first_ksa_ha": H["first_ksa_ha"],
        "record_opens": H["record_opens"], "break_inside_window": H["break_inside_window"],
        "five_unit_series": H["five_kabupaten_ksa_by_year"],
        "five_unit_range_pct": H["five_kabupaten_range_pct"],
        "five_unit_cv_pct": H["five_kabupaten_cv_pct"],
        "national_ksa_mha": H["national_ksa_mha"],
        "national_2025_jump_pct": H["national_2025_jump_pct"],
        "map_implied_ha": H["map_implied_harvest_ha"],
        "map_vs_ksa_pct": H["map_vs_ksa_pct"], "map_vs_ksa_year": H["map_vs_ksa_year"],
    }

    # ── finding 2 · the shortfall, factored ───────────────────────────────────────────────
    o["decomposition"] = {
        "aggregate": [{k: (float(v) if isinstance(v, str) and v.replace(".", "").isdigit() else v)
                       for k, v in r.items()} for r in A["aggregate"]],
        "by_kabupaten": [r for r in A["by_kabupaten_year"] if r["ksa_ha"] is not None],
    }
    latest = o["decomposition"]["aggregate"][-1]
    first = o["decomposition"]["aggregate"][0]
    o["headline"] = {
        "ratio_first": first["ratio_ksa"], "ratio_last": latest["ratio_ksa"],
        "recall_extent_first": first["recall_extent"], "recall_extent_last": latest["recall_extent"],
        "recall_ci_first": first["recall_ci"], "recall_ci_last": latest["recall_ci"],
        "share_extent": round(sum(r["share_extent"] for r in o["decomposition"]["aggregate"])
                              / len(o["decomposition"]["aggregate"]), 4),
        "map_ci": latest["map_ci"], "det_ci": latest["det_ci"],
    }

    # ── finding 3 · by the independent map's own crop count ───────────────────────────────
    o["crop_class"] = {"summary": {str(k): v for k, v in B["summary"].items()},
                       "rows": B["by_class_year"]}

    # ── finding 4 · seasonality ───────────────────────────────────────────────────────────
    o["seasonal"] = {"by_month": C["by_month"],
                     "by_month_lag_corrected": C["by_month_lag_corrected"],
                     "lag_months_removed": C["lag_months_removed"],
                     "lobes": C["lobes"], "lobes_lag_corrected": C["lobes_lag_corrected"]}

    # ── finding 5 · revisit, observed and controlled ──────────────────────────────────────
    kab_now = {}
    for r in D["rows"]:
        kab_now.setdefault(r["kabupaten"], {})[r["year"]] = r
    overlay = []
    for k, ys in kab_now.items():
        for y, r in sorted(ys.items()):
            overlay.append({"kabupaten": k, "year": y, "gap": r["median_gap_days"],
                            "recall": r["recall_extent"], "acq": r["n_acquisitions"],
                            "ci": r["ci"]})
    vhr = {p["kabupaten"]: p["vh_range_db"] for p in F["per_kabupaten"]}
    o["revisit"] = {
        "rows": D["rows"], "by_year": D["by_year"],
        "corr_gap_vs_recall": D["corr_gap_vs_extent_recall"],
        "overlay": overlay,
        "vh_range_db": vhr,
        "s1b_failure": D["s1b_failure"], "s1b_test_possible": D["s1b_test_possible"],
        "within_unit": [{"kabupaten": k,
                         "gap_first": ys[min(ys)]["median_gap_days"],
                         "gap_last": ys[max(ys)]["median_gap_days"],
                         "recall_first": ys[min(ys)]["recall_extent"],
                         "recall_last": ys[max(ys)]["recall_extent"],
                         "acq_first": ys[min(ys)]["n_acquisitions"],
                         "acq_last": ys[max(ys)]["n_acquisitions"],
                         "gain": round(ys[max(ys)]["recall_extent"]
                                       / max(ys[min(ys)]["recall_extent"], 1e-9), 2)}
                        for k, ys in sorted(kab_now.items())],
    }
    o["thinning"] = {"kabupaten": E["kabupaten"], "ladder": E["ladder"],
                     "prior_rice_cells": E["prior_rice_cells"],
                     "published_cycles": next(k["cycles"] for k in kabs
                                              if k["kabupaten"] == E["kabupaten"]),
                     "baseline_vs_published": round(
                         E["ladder"][0]["cycles"] / next(k["cycles"] for k in kabs
                                                         if k["kabupaten"] == E["kabupaten"]), 3)}

    # ── finding 6 · what the calibration is ───────────────────────────────────────────────
    o["calibration"] = {
        "coefficients": F["coefficients"], "n_fit_rows": F["n_fit_rows"],
        "r2_monthly_calibrated": F["r2_monthly_calibrated"],
        "r2_monthly_uncalibrated": F["r2_monthly_uncalibrated"],
        "r2_monthly_holdout": F["r2_monthly_holdout"],
        "r2_annual_calibrated": g1["variants"]["calibrated"]["kabupaten_r2"],
        "mean_sat_share": F["mean_sat_share"],
        "per_kabupaten": F["per_kabupaten"],
        "sign_flip_vh_range_db": F["sign_flip_vh_range_db"],
        "negative_units": [p["kabupaten"] for p in F["per_kabupaten"]
                           if p["effective_slope_on_detected"] < 0],
    }

    # ── finding 7 · timing ────────────────────────────────────────────────────────────────
    o["timing"] = {k: G[k] for k in ("median_abs_argmax_error_months",
                                     "median_abs_xcorr_lag_months", "median_xcorr_at_lag",
                                     "median_xcorr_at_zero", "n")}
    o["timing"]["rows"] = G["rows"]

    # ── precision and recall against the independent map ──────────────────────────────────
    o["prf"] = I

    # ── the detector, and how sharp its edges are ─────────────────────────────────────────
    t = stats["detector_thresholds"]
    o["thresholds"] = {k: t[k] for k in ("flood_drop_db", "flood_baseline_pctl", "flood_db",
                                         "rise_db", "rise_window_days", "heading_window_days",
                                         "head_to_harvest_days", "min_cycle_days", "max_gap_days")}
    o["sensitivity"] = stats["threshold_sensitivity_ha"]
    o["kabupaten"] = [{k: v[k] for k in ("kabupaten", "province", "system", "cells", "cycles",
                                         "median_vh_rise_db", "median_vv_min_db",
                                         "median_cycle_days", "share_cells_with_any_cycle",
                                         "acquisitions")} for v in kabs]
    o["mask"] = {k: stats["mask"][k] for k in ("product", "doi", "year", "res_m", "cells_rice",
                                               "share_rice", "class_counts")}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(o, indent=1))
    print(f"[article] checks {o['checks_passed']}/5 · shortfall {o['headline']['ratio_last']:.3f} "
          f"of the benchmark · {o['headline']['share_extent']:.0%} of the deficit is extent")
    print(f"[article] -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
