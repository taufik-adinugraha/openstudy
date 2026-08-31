"""Build the review article's data layer from the case's own published output.

Every number the article prints comes from here — the case's published ``stats.json`` and the
adversarial re-scoring in ``pipeline/review.py`` — so the prose can never drift from the
pipeline.  The article pins a data vintage the way a paper pins its sample.

    uv run python pipeline/article.py data/stats.json data/review.json \\
        web/src/data/article.json
"""

from __future__ import annotations

import json
import sys


def _r(x, d=6):
    return None if x is None else round(float(x), d)


def main() -> int:
    stats = json.load(open(sys.argv[1]))
    rev = json.load(open(sys.argv[2]))
    out: dict = {}

    G = stats["gates"]
    rm = stats["risk_meta"]
    tm = stats["transport_meta"]
    fa = stats["fires_audit"]
    pm = stats["panel_meta"]

    out["vintage"] = stats["generated"][:10]
    out["generated"] = stats["generated"]
    out["review_generated"] = rev.get("generated")
    out["leads"] = [int(k) for k in sorted(rm["leads"], key=int)]

    # ── 1 · what the case published, exactly as published ─────────────────────────────
    out["published"] = {
        "gates_passed": stats["gates_passed"], "gates_total": stats["gates_total"],
        "hard_passed": stats["hard_gates_passed"], "hard_total": stats["hard_gates_total"],
        "era5_years": rm["era5_years"], "folds": rm["folds"], "anchors": rm["anchors"],
        "panel_rows": pm["rows"], "n_cells": rev["rescore"]["n_cells"],
        "per_lead": {str(L): {
            "auc": _r(rm["leads"][str(L)]["forecast"]["auc"]),
            "auc_reanalysis": _r(rm["leads"][str(L)]["reanalysis"]["auc"]),
            "foresight_gap": _r(rm["leads"][str(L)]["foresight_gap_auc"]),
            "brier": _r(rm["leads"][str(L)]["forecast"]["brier"]),
            "base_rate": _r(rm["leads"][str(L)]["forecast"]["base_rate"]),
            "auc_climatology": _r(rm["leads"][str(L)]["forecast"]["auc_climatology"]),
            "auc_persistence": _r(rm["leads"][str(L)]["forecast"]["auc_persistence"]),
            "bss_climatology": _r(rm["leads"][str(L)]["forecast"]["bss_vs_climatology"]),
            "bss_persistence": _r(rm["leads"][str(L)]["forecast"]["bss_vs_persistence"]),
            "n": rm["leads"][str(L)]["forecast"]["n"],
            "positives": rm["leads"][str(L)]["forecast"]["positives"],
            "fwi_auc": _r(rm["fwi"]["per_lead"][str(L)].get("auc_fwi")),
            "fwi_bss": _r(rm["fwi"]["per_lead"][str(L)].get("bss_vs_fwi")),
            "fwi_n": rm["fwi"]["per_lead"][str(L)].get("n"),
            "fwi_model_auc": _r(rm["fwi"]["per_lead"][str(L)].get("auc_model")),
        } for L in out["leads"]},
        "shap_share": {k: _r(v, 4) for k, v in rm["shap_families"]["share"].items()},
        "shap_top": [{"f": k, "v": _r(v, 5)}
                     for k, v in list(rm["shap_families"]["per_feature"].items())[:10]],
        "anchor_scores": {k: {"auc": _r(v["auc"]), "n": v["n"], "positives": v["positives"]}
                          for k, v in rm["anchor_scores"].items()},
    }

    # ── 2 · re-scoring under the honest metric for a rare event ───────────────────────
    R = rev["rescore"]
    out["rescore"] = {"sample_rows": R["sample_rows"], "folds": R["folds"],
                      "n_cells": R["n_cells"], "per_lead": {}}
    for L in out["leads"]:
        s = R["per_lead"][str(L)]
        row = {"n": s["n"], "positives": s["positives"], "base_rate": _r(s["base_rate"]),
               "scores": {}}
        for k, v in s["scores"].items():
            row["scores"][k] = {
                "auc": _r(v["auc"]), "ap": _r(v["ap"]), "ap_lift": _r(v["ap_lift"], 3),
                "p1": _r(v["at_1pct"]["precision"]), "r1": _r(v["at_1pct"]["recall"]),
                "p5": _r(v["at_5pct"]["precision"]), "r5": _r(v["at_5pct"]["recall"]),
                "k1": v["at_1pct"]["k"],
            }
        row["decomposition"] = {k: {"day": _r(v["within_day_auc"]),
                                    "cell": _r(v["within_cell_auc"])}
                                for k, v in s["decomposition"].items()}
        c = s["cell_rate_only"]
        row["cell_rate_only"] = {"auc": _r(c["auc"]), "ap": _r(c["ap"]),
                                 "p1": _r(c["at_1pct"]["precision"])}
        row["model_vs_cellrate_spearman"] = _r(s["model_vs_cellrate_spearman"], 3)
        out["rescore"]["per_lead"][str(L)] = row

    # the metric reversal: AUC and average precision rank the two baselines opposite ways
    one = out["rescore"]["per_lead"]["1"]["scores"]
    out["metric_reversal"] = {
        "auc_prefers": "climatology" if one["climatology"]["auc"] > one["persistence"]["auc"]
        else "persistence",
        "ap_prefers": "climatology" if one["climatology"]["ap"] > one["persistence"]["ap"]
        else "persistence",
        "climatology": {"auc": one["climatology"]["auc"], "ap": one["climatology"]["ap"],
                        "p1": one["climatology"]["p1"]},
        "persistence": {"auc": one["persistence"]["auc"], "ap": one["persistence"]["ap"],
                        "p1": one["persistence"]["p1"]},
        "reversed": ((one["climatology"]["auc"] > one["persistence"]["auc"])
                     != (one["climatology"]["ap"] > one["persistence"]["ap"])),
    }

    # ── 3 · the spatially blocked refit ───────────────────────────────────────────────
    if "spatial" in rev:
        S = rev["spatial"]
        out["spatial"] = {
            "block_deg": S["block_deg"], "n_blocks": S["n_blocks"],
            "n_spatial_folds": S["n_spatial_folds"], "n_cells": S["n_cells"],
            "seasons": S["seasons"], "per_lead": {}}
        for L, v in S["per_lead"].items():
            pub = out["published"]["per_lead"].get(L, {})
            out["spatial"]["per_lead"][L] = {
                "n": v["n"], "auc": _r(v["auc"]), "ap": _r(v["ap"]),
                "base_rate": _r(v["base_rate"]),
                "p1": _r(v["at_1pct"]["precision"]), "r1": _r(v["at_1pct"]["recall"]),
                "published_auc": pub.get("auc"),
                "delta_auc": _r((v["auc"] or 0) - (pub.get("auc") or 0)),
                "delta_ap": _r((v["ap"] or 0)
                               - (out["rescore"]["per_lead"][L]["scores"]["model"]["ap"] or 0)),
                "per_block": {k: {"n": b["n"], "auc": _r(b["auc"]), "ap": _r(b["ap"]),
                                  "base_rate": _r(b["base_rate"])}
                              for k, b in v["per_block"].items()},
                "per_season": {k: {"n": b["n"], "auc": _r(b["auc"]), "ap": _r(b["ap"])}
                               for k, b in v["per_season"].items()},
            }

    # ── 4 · the FWI comparison, audited ───────────────────────────────────────────────
    F = rev["fwi"]
    out["fwi"] = {
        "years_on_disk": F["years_on_disk"],
        "rows_by_year": F["rows_by_year"],
        "oof_folds": F["oof_folds"],
        "fold_years_with_fwi": F["fold_years_with_fwi"],
        "fold_years_without_fwi": F["fold_years_without_fwi"],
        "anchor_years_with_fwi": F["anchor_years_with_fwi"],
        "anchor_years_without_fwi": F["anchor_years_without_fwi"],
        "ingest_status": stats["indices_meta"]["fwi"],
        "per_lead": {},
    }
    for L in out["leads"]:
        r = F["per_lead"][str(L)]
        rec = {"oof_rows": r["oof_rows"], "rows_with_fwi": r["rows_with_fwi"],
               "join_share": _r(r["join_share"]),
               "per_fold": {k: {"oof_rows": v["oof_rows"],
                                "rows_with_fwi": v["rows_with_fwi"],
                                "share": _r(v["share"])} for k, v in r["per_fold"].items()}}
        if "like_for_like" in r:
            ll = r["like_for_like"]
            rec["like_for_like"] = {k: (_r(v) if isinstance(v, (int, float)) else v)
                                    for k, v in ll.items() if k != "model_at_1pct"}
            rec["like_for_like"]["model_p1"] = _r(ll["model_at_1pct"]["precision"])
            rec["like_for_like"]["model_r1"] = _r(ll["model_at_1pct"]["recall"])
            rec["components"] = {k: {"auc": _r(v["auc"]), "ap": _r(v["ap"]),
                                     "p1": _r(v["at_1pct"]["precision"])}
                                 for k, v in r["components"].items()}
            rec["by_fold"] = {k: {kk: (_r(vv) if isinstance(vv, float) else vv)
                                  for kk, vv in v.items()} for k, v in r["by_fold"].items()}
        out["fwi"]["per_lead"][str(L)] = rec

    # the single sentence the headline turns on: what seasons was it actually scored on?
    p1 = out["fwi"]["per_lead"]["1"]
    scored_on = [k for k, v in p1["per_fold"].items() if v["rows_with_fwi"] > 0]
    out["fwi"]["seasons_with_data"] = scored_on
    out["fwi"]["published_n"] = rm["fwi"]["per_lead"]["1"]["n"]

    # THE STATE OF THE CEMS RECORD IN THE BUILD THIS REVIEW EXAMINED, before the queue-driver
    # fix in util.run_store_jobs.  Transcribed from that build's own data/stats.json and
    # data/cads_jobs.json, and recorded here because the post-fix artefacts can no longer show
    # it — the article's account of what was wrong has to be checkable against something.
    out["fwi"]["reviewed_build"] = {
        "years_on_disk": [2012, 2013, 2016, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026],
        "fold_years_with_fwi": [2016, 2018],
        "fold_years_without_fwi": [2017],
        "anchor_years_without_fwi": [2015],
        "years_recovered": [2014, 2015, 2017],
        "published_n": 133386,
        "auc_fwi": {"1": 0.806189, "3": 0.774075, "7": 0.738132},
        "bss_vs_fwi": {"1": 0.131496, "3": 0.104357, "7": 0.055004},
        "ingest_status": {"status": "queued", "reason": "EWDS jobs submitted; rerun to drain"},
        "note": ("the three seasons were recorded as rejected, not queued; the poller re-stamped "
                 "each rejected job's cooling-off timestamp on every pass, so the submit loop's "
                 "180 s retry window never elapsed and the request was never resubmitted"),
    }

    # ── 5 · the trajectory, replayed ──────────────────────────────────────────────────
    T = rev["trajectory"]
    out["traj"] = {
        "receptor_days": T["receptor_days"], "parcel_rows": T["parcel_rows"],
        "years": T["years"],
        "ensemble": [{"h": e["hours"], "surv": _r(e["surviving_share"]),
                      "med": _r(e["spread_km_median"], 2), "p90": _r(e["spread_km_p90"], 2)}
                     for e in T["ensemble"]],
        "truncation": {k: (_r(v) if isinstance(v, float) else v)
                       for k, v in T["truncation"].items()},
        "attribution": {k: (_r(v) if isinstance(v, float) else v)
                        for k, v in T["attribution"].items() if k != "singapore"},
        "singapore": {
            "episode_days": T["attribution"]["singapore"]["episode_days"],
            "distinct_top_provinces": T["attribution"]["singapore"]["distinct_top_provinces"],
            "median_top_share": _r(T["attribution"]["singapore"]["median_top_share"]),
            "median_agreement": _r(T["attribution"]["singapore"]["median_agreement"]),
            "top_share_over_half": _r(T["attribution"]["singapore"]["top_share_over_half"]),
            "provinces": [{"p": v["province"], "days": v["days"],
                           "share": _r(v["share_of_days"])}
                          for v in T["attribution"]["singapore"]["top_provinces"]],
        },
        "bearing": {"rows": T["bearing"]["rows"], "within_30": _r(T["bearing"]["within_30"]),
                    "median": _r(T["bearing"]["median_diff"], 2),
                    "p90": _r(T["bearing"]["p90_diff"], 2),
                    "per_receptor": {k: {"n": v["n"], "within_30": _r(v["within_30"]),
                                         "median": _r(v["median_diff"], 2),
                                         "p90": _r(v["p90_diff"], 2)}
                                     for k, v in T["bearing"]["per_receptor"].items()}},
        "gfas_height_share": _r(tm["gfas_height_share"]),
        "plume_rise_fallback_share": _r(tm["plume_rise_fallback_share"]),
        "escaped_domain_share": _r(tm["escaped_domain_share"]),
        "years_integrated": tm["years_integrated"],
        "hours": tm["hours"], "scheme": tm["scheme"],
        "receptor_km": tm["receptor_km"],
        "cams_comparison": tm["cams_comparison"],
        # the CAMS block changed shape between the two ingest runs, so read both forms
        "cams_forecast": _cams_forecast(stats.get("cams_meta", {})),
        "gfas_injection_years": _gfas_years(stats.get("cams_meta", {})),
    }
    # the two halves of the case run on almost disjoint years
    ry = set(rm["era5_years"])
    ty = set(tm["years_integrated"])
    out["traj"]["shared_years_with_risk"] = sorted(ry & ty)
    out["traj"]["risk_only_years"] = sorted(ry - ty)
    out["traj"]["transport_only_years"] = sorted(ty - ry)

    # ── 6 · the anchors, and what the failed replay check actually shows ──────────────
    g5 = G["G-J5"]
    mod = g5["seasonal_series_modelled"]
    obs = g5["seasonal_series_observed"]
    yrs = sorted(mod, key=lambda y: int(y))
    mr = _rank([mod[y] for y in yrs])
    orr = _rank([obs[y] for y in yrs])
    n = len(yrs)
    dsq = sum((a - b) ** 2 for a, b in zip(mr, orr))
    out["anchors"] = {
        "seasons": [{"year": int(y), "modelled": _r(mod[y]), "observed": obs[y],
                     "rank_modelled": mr[i], "rank_observed": orr[i]}
                    for i, y in enumerate(yrs)],
        "spearman": _r(1 - 6 * dsq / (n * (n * n - 1)), 4),
        "exact_order": mr == orr,
        "observed_full": {k: v for k, v in obs.items()},
        "auc_2015": _r(rm["anchor_scores"]["2015"]["auc"]),
        "auc_2019": _r(rm["anchor_scores"]["2019"]["auc"]),
        "gate_pass": g5["pass"],
        "seasons_admitted": g5["seasons_admitted_by_threshold"],
        "seasons_in_record": g5["seasons_in_record"],
        "structurally_unsatisfiable": g5["structurally_unsatisfiable"],
        # the top of the distribution is compressed even though the ordering is exact
        "gap_2019_2012_modelled": _r((mod["2019"] - mod["2012"]) / mod["2019"], 4),
        "gap_2019_2012_observed": _r((obs["2019"] - obs["2012"]) / obs["2019"], 4),
    }

    # ── 7 · the filter, and the receptors ─────────────────────────────────────────────
    out["fires"] = {
        "rows_raw": fa["rows_raw"], "rows_kept": fa["rows_kept"],
        "removed_share": _r(fa["removed_share"]), "nrt_rows": fa["nrt_rows"],
        "nrt_removed_share": _r(fa["nrt_removed_share"]),
        "type_present_share": _r(fa["type_present_share"]),
        "composition": fa["removed_composition"],
        "by_year": {k: v for k, v in fa["detections_by_year"].items()},
        "retained_inside_mask": G["G-J1"]["retained_inside_mask"],
        "per_product": G["G-J1"]["per_product"],
    }
    out["receptors"] = {k: {"tier": v["tier"], "rho": _r(v["rho"]), "n": v["n"],
                            "kind": v["kind"], "pass": v["pass"]}
                        for k, v in G["G-J4"]["per_receptor"].items()}
    out["gates"] = {k: {"pass": v["pass"], "hard": v["hard"], "reason": v.get("reason")}
                    for k, v in G.items()}

    json.dump(out, open(sys.argv[3], "w"), indent=1)
    sp = out.get("spatial", {}).get("per_lead", {}).get("1", {})
    print(f"[article] AUC 1d {out['published']['per_lead']['1']['auc']:.4f} -> "
          f"AP {out['rescore']['per_lead']['1']['scores']['model']['ap']:.4f} · "
          f"spatially blocked AUC {sp.get('auc')} · "
          f"FWI seasons {out['fwi']['fold_years_with_fwi']} of {out['fwi']['oof_folds']} · "
          f"Singapore top province {out['traj']['singapore']['provinces'][0]['p']}")
    print(f"[article] -> {sys.argv[3]}")
    return 0


def _cams_forecast(cm: dict) -> dict:
    """What the CAMS chemistry forecast ingest actually delivered, whichever shape it wrote."""
    con = cm.get("consolidated") or {}
    if "cams_forecast" in con:                       # the shape the reviewed build published
        return {"status": "not consolidated", "parts": con["cams_forecast"].get("parts", 0),
                "years": []}
    f = cm.get("forecast") or {}
    return {"status": f.get("status", "unknown"), "parts": f.get("done", 0),
            "years": (cm.get("coverage_notes") or {}).get("forecast_years_pulled", [])}


def _gfas_years(cm: dict) -> list:
    con = cm.get("consolidated") or {}
    if "gfas" in con and "injection_height_years" in con["gfas"]:
        return con["gfas"]["injection_height_years"]
    return []


def _rank(v):
    """Descending rank, 1 = largest.  Ties get the average rank."""
    order = sorted(range(len(v)), key=lambda i: -v[i])
    out = [0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


if __name__ == "__main__":
    sys.exit(main())
