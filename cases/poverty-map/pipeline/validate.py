"""Stage 5 · validate — gates G-F1…G-F4 → data/stats.json.

Lab rule: a failing gate is published red, with its number. Nothing here re-tunes anything;
this module only measures and records. G-F1 and G-F4 are hard gates (the case does not ship
on a red G-F1); G-F2 is a disclosure gate — it fails only if the two sides were not measured,
and it sets the "indicative" flag on off-Java estimates when off-Java R² < 0.35.
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

import config


def gate(gid: str, title: str, ok: bool | None, detail: str, checks: list[dict],
         hard: bool = False) -> dict:
    return {"id": gid, "title": title, "hard": hard,
            "status": "pending" if ok is None else ("pass" if ok else "fail"),
            "detail": detail, "checks": checks}


def chk(label: str, value, threshold, cmp: str, unit: str = "") -> dict:
    if value is None or not np.isfinite(value):
        return {"label": label, "value": None, "threshold": threshold, "cmp": cmp,
                "unit": unit, "ok": None}
    ok = value >= threshold if cmp == ">=" else value <= threshold
    return {"label": label, "value": round(float(value), 4), "threshold": threshold,
            "cmp": cmp, "unit": unit, "ok": bool(ok)}


def run() -> dict:
    ms_path = config.DATA_DIR / "model_stats.json"
    if not ms_path.exists():
        raise SystemExit("[validate] model_stats.json missing — run model.py first")
    ms = json.loads(ms_path.read_text())
    fm = json.loads((config.DATA_DIR / "features_meta.json").read_text())
    skill = ms["skill"]
    latest = ms["latest_year"]

    g1c = [chk("R² (leave-one-province-out)", skill["lopo"]["r2"], config.GATE_R2_MIN, ">="),
           chk("Spearman ρ", skill["lopo"]["spearman"], config.GATE_SPEARMAN_MIN, ">="),
           chk("RMSE", skill["lopo"]["rmse"], config.GATE_RMSE_MAX_PP, "<=", "pp")]
    g1 = gate("G-F1", f"Out-of-sample skill — leave-one-province-out, {latest} cross-section",
              all(c["ok"] for c in g1c) if all(c["ok"] is not None for c in g1c) else None,
              f"{ms['folds']['lopo']} province folds over {skill['lopo']['n']} regencies; "
              f"random k-fold on the same rows reaches R² {skill['random']['r2']} — the "
              f"inflation a non-spatial fold buys.", g1c, hard=True)

    oj = skill["lopo_offjava"]
    g2c = [chk("Java R²", skill["lopo_java"]["r2"], config.GATE_R2_OFFJAVA_MIN, ">="),
           chk("off-Java R²", oj["r2"], config.GATE_R2_OFFJAVA_MIN, ">="),
           chk("kota R²", skill["lopo_kota"]["r2"], config.GATE_R2_OFFJAVA_MIN, ">="),
           chk("kabupaten R²", skill["lopo_kabupaten"]["r2"], config.GATE_R2_OFFJAVA_MIN, ">=")]
    indicative = bool(oj["r2"] is not None and oj["r2"] < config.GATE_R2_OFFJAVA_MIN)
    g2 = gate("G-F2", "Java / off-Java and urban / rural skill disclosed separately",
              None if oj["r2"] is None else True,
              ("Both sides measured and published. " +
               (f"Off-Java R² {oj['r2']} is below {config.GATE_R2_OFFJAVA_MIN}, so every "
                "off-Java estimate is labelled indicative and its interval widened."
                if indicative else
                "Off-Java skill clears the indicative-labelling threshold.")), g2c)

    g3c = [chk(f"Spearman ρ {y}", skill[f"temporal_{y}"]["spearman"],
               config.GATE_TEMPORAL_SPEARMAN, ">=")
           for y in config.TEMPORAL_HOLDOUT_YEARS]
    g3 = gate("G-F3", "Temporal hold-out — train ≤ 2023, predict 2024 and 2025",
              all(c["ok"] for c in g3c) if all(c["ok"] is not None for c in g3c) else None,
              "Fitted on 2016–2023 only, then asked for the two releases it has never seen. "
              "The features that vary annually are lights and population; roofs and land "
              "cover are single-vintage, so this is a demanding test of the spatial signal.",
              g3c)

    # ---- G-F4: recompute the benchmark identity from the shipped estimates
    worst = None
    n_units = 0
    if config.ESTIMATES_ADM3.exists():
        est = pd.read_parquet(config.ESTIMATES_ADM3)
        e = est[np.isfinite(est["p0_est"]) & np.isfinite(est["official_p0"]) &
                np.isfinite(est["pop"]) & (est["pop"] > 0)]
        if len(e):
            e = e.assign(w=e["pop"], wx=e["pop"] * e["p0_est"])
            agg = e.groupby(["bps_code", "year"]).agg(
                w=("w", "sum"), wx=("wx", "sum"), official=("official_p0", "first"))
            agg["recovered"] = agg["wx"] / agg["w"]
            agg["err"] = (agg["recovered"] - agg["official"]).abs()
            worst = float(agg["err"].max())
            n_units = int(len(agg))
    g4c = [chk("max |recovered − official|", worst, config.GATE_BENCHMARK_TOL_PP, "<=", "pp")]
    g4 = gate("G-F4", "Benchmark integrity — kecamatan estimates reproduce every official "
                      "regency rate",
              None if worst is None else worst <= config.GATE_BENCHMARK_TOL_PP,
              f"Population-weighted mean of the kecamatan estimates recomputed from the "
              f"shipped file for all {n_units:,} regency-years." if n_units else
              "Pending — estimates_adm3.parquet not built yet.", g4c, hard=True)

    gates = [g1, g2, g3, g4]
    hard_fail = [g["id"] for g in gates if g["hard"] and g["status"] == "fail"]
    recon = fm["recon"]
    stats = {
        "case": "poverty-map",
        "vintage": str(date.today()),
        "latest_year": latest,
        "gates": gates,
        "ships": not hard_fail,
        "hard_failures": hard_fail,
        "offjava_indicative": indicative,
        "skill": skill,
        "skill_panel_lopo": ms["skill_panel_lopo"],
        "folds": ms["folds"],
        "coverage": {
            "adm2_units": fm["adm2_units"], "adm3_units": fm["adm3_units"],
            "years": fm["years"], "n_features": ms["n_features"],
            "reconciliation": recon,
        },
        "caveats": [
            "Kecamatan values are model estimates that distribute the official regency rate; "
            "they are not survey measurements and BPS publishes nothing below the regency.",
            "Open Buildings is a single 2023 vintage and ESA WorldCover a single 2021 vintage; "
            "only night lights and population vary year to year, so the model is primarily a "
            "spatial disaggregator, not a time-series.",
            "Susenas and DHS microdata are restricted, so the target is the published regency "
            "poverty rate rather than household consumption.",
            f"{recon['non_census']} COD-AB polygons (lakes, a reservoir, a forest block) carry "
            "no BPS code by construction and receive no estimate.",
        ],
    }
    if recon["unresolved"]:
        stats["caveats"].append(
            f"{len(recon['unresolved'])} administrative codes could not be reconciled between "
            "the 2020 COD-AB boundaries and the current BPS series; they are listed on the page "
            "and excluded from training.")
    config.STATS_JSON.write_text(json.dumps(stats, indent=1))
    for g in gates:
        marks = " · ".join(
            f"{c['label']} {c['value']}{c['unit']} {c['cmp']} {c['threshold']}"
            for c in g["checks"])
        print(f"[validate] {g['id']} {g['status'].upper():7s} {marks}", flush=True)
    print(f"[validate] ships={stats['ships']} -> {config.STATS_JSON.name}", flush=True)
    return stats


def main() -> None:
    run()


if __name__ == "__main__":
    main()
