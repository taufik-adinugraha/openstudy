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


def disaggregation(latest: int) -> dict:
    """The shipped procedure, run one administrative level up, where truth exists.

    Chapter 4 distributes an official regency rate among its kecamatan and cannot be
    checked, because BPS publishes nothing below the regency. The identical rule CAN be
    checked one level higher: take each province's official population-weighted rate as
    given, let the held-out model distribute it among that province's regencies, and score
    the result against the official regency rates. The null is the honest alternative any
    agency already has — give every regency its province's rate.
    """
    if not config.CV_PREDICTIONS.exists():
        return {}
    cv = pd.read_parquet(config.CV_PREDICTIONS)
    d = cv[(cv["year"] == latest) & np.isfinite(cv["pred_lopo"]) &
           np.isfinite(cv["p0_pct"]) & (cv["pop"] > 0)].copy()
    if len(d) < 50:
        return {}
    g = d.groupby("prov_code")
    off = g.apply(lambda s: np.average(s["p0_pct"], weights=s["pop"]), include_groups=False)
    pred = g.apply(lambda s: np.average(s["pred_lopo"], weights=s["pop"]), include_groups=False)
    d["flat"] = d["prov_code"].map(off)
    d["bench"] = (d["pred_lopo"] * d["prov_code"].map(off / pred)).clip(0, 100)

    def sc(p):
        e = d["p0_pct"] - p
        return {"r2": round(1 - float((e ** 2).sum()) /
                            float(((d["p0_pct"] - d["p0_pct"].mean()) ** 2).sum()), 4),
                "mae": round(float(e.abs().mean()), 4),
                "rmse": round(float(np.sqrt((e ** 2).mean())), 4)}

    return {
        "level": "province → regency",
        "n": int(len(d)),
        "flat_parent_rate": sc(d["flat"]),
        "benchmarked_model": sc(d["bench"]),
        "win_rate": round(float(((d["bench"] - d["p0_pct"]).abs()
                                 < (d["flat"] - d["p0_pct"]).abs()).mean()), 4),
        "mae_reduction_pp": round(float((d["flat"] - d["p0_pct"]).abs().mean()
                                        - (d["bench"] - d["p0_pct"]).abs().mean()), 4),
        "note": ("The kecamatan layer cannot be validated directly. This runs the identical "
                 "benchmarking rule one level up, where the official regency rates supply "
                 "the truth, against the null of giving every regency its province's rate."),
    }


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
    dec = ms.get("decomposition", {})
    g1 = gate("G-F1", f"Out-of-sample skill — leave-one-province-out, {latest} cross-section",
              all(c["ok"] for c in g1c) if all(c["ok"] is not None for c in g1c) else None,
              f"{ms['folds']['lopo']} province folds over {skill['lopo']['n']} regencies. "
              f"A random k-fold on the same rows reaches R² {skill['random']['r2']} — the "
              f"inflation a non-spatial fold buys, and the reason this number is the one "
              f"published." +
              (f" Where the error lives: {dec['offset_share_of_sse']:.0%} of the squared error "
               f"is a single constant offset for a whole province. Remove those offsets and the "
               f"model still orders regencies inside their province at ρ "
               f"{dec['within_province_spearman']:.2f}, though it explains only R² "
               f"{dec['within_province_r2']:.3f} of the within-province variation — read both."
               if dec.get("offset_share_of_sse") is not None else ""), g1c, hard=True)

    oj, kt = skill["lopo_offjava"], skill["lopo_kota"]
    g2c = [chk("Java R²", skill["lopo_java"]["r2"], config.GATE_R2_OFFJAVA_MIN, ">="),
           chk("off-Java R²", oj["r2"], config.GATE_R2_OFFJAVA_MIN, ">="),
           chk("kota (urban) R²", kt["r2"], config.GATE_R2_OFFJAVA_MIN, ">="),
           chk("kabupaten (rural) R²", skill["lopo_kabupaten"]["r2"],
               config.GATE_R2_OFFJAVA_MIN, ">=")]
    indicative = bool(oj["r2"] is not None and oj["r2"] < config.GATE_R2_OFFJAVA_MIN)
    kota_ind = bool(kt["r2"] is not None and kt["r2"] < config.GATE_R2_OFFJAVA_MIN)
    flagged = [n for n, f in (("off-Java", indicative), ("kota", kota_ind)) if f]
    g2 = gate("G-F2", "Java / off-Java and urban / rural skill disclosed separately",
              None if oj["r2"] is None else True,
              ("A disclosure gate: it is satisfied by measuring and publishing both sides, "
               "and a red value below is a finding, not a gate failure. " +
               (f"{' and '.join(flagged)} skill sits under {config.GATE_R2_OFFJAVA_MIN}, so "
                f"every {' and '.join(flagged)} estimate is labelled indicative. "
                if flagged else "Both splits clear the indicative-labelling threshold. ") +
               (f"Urban R² of {kt['r2']} is below zero — for kota the model does worse than "
                f"predicting the national mean, and city estimates should be read as "
                f"indicative only." if kt["r2"] is not None and kt["r2"] < 0 else "")), g2c)
    g2["status"] = "disclosed" if g2["status"] == "pass" else g2["status"]

    g3c = [chk(f"Spearman ρ {y}", skill[f"temporal_{y}"]["spearman"],
               config.GATE_TEMPORAL_SPEARMAN, ">=")
           for y in config.TEMPORAL_HOLDOUT_YEARS]
    g3c += [chk(f"ρ {y} · province ALSO held out", skill.get(f"temporal_strict_{y}", {}).get("spearman"),
                config.GATE_TEMPORAL_SPEARMAN, ">=") for y in config.TEMPORAL_HOLDOUT_YEARS]
    g3 = gate("G-F3", "Temporal hold-out — train ≤ 2023, predict 2024 and 2025",
              all(c["ok"] for c in g3c[:2]) if all(c["ok"] is not None for c in g3c[:2]) else None,
              "Fitted on 2016–2023 only, then asked for the two releases it has never seen. "
              "Read the first two rows with care: the same regencies appear in training in "
              "earlier years and the roof and land-cover layers are single-vintage, so what "
              "they mostly measure is how persistent a regency's rate is. The last two rows "
              "are the strict version — the province is held out as well as the year — and "
              "that is the number to quote to a client.", g3c)

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

    # input coverage, measured rather than assumed: a kecamatan with no building footprints
    # or no population is predicted from the remaining families and must be disclosed.
    cover = {}
    if config.FEATURES_ADM3.exists():
        f3 = pd.read_parquet(config.FEATURES_ADM3, columns=["pcode", "year", "bld_count", "pop",
                                                            "lc_pixels", "lights_mean"])
        row = f3[f3["year"] == latest]
        cover = {
            "adm3": int(len(row)),
            "no_buildings": int((row["bld_count"].fillna(0) <= 0).sum()),
            "no_population": int((row["pop"].fillna(0) <= 0).sum()),
            "no_landcover": int((row["lc_pixels"].fillna(0) <= 0).sum()),
            "no_lights": int(row["lights_mean"].isna().sum()),
        }
    stats = {
        "case": "poverty-map",
        "vintage": str(date.today()),
        "latest_year": latest,
        "gates": gates,
        "ships": not hard_fail,
        "hard_failures": hard_fail,
        "offjava_indicative": indicative,
        "kota_indicative": kota_ind,
        "skill": skill,
        "skill_panel_lopo": ms["skill_panel_lopo"],
        "decomposition": ms.get("decomposition", {}),
        "disaggregation": disaggregation(latest),
        "folds": ms["folds"],
        "coverage": {
            "adm2_units": fm["adm2_units"], "adm3_units": fm["adm3_units"],
            "years": fm["years"], "n_features": ms["n_features"],
            "reconciliation": recon, "inputs": cover,
        },
        "caveats": [
            "The out-of-sample skill check fails on this run and is published as measured. The "
            "thresholds were taken from studies that did not hold space out — an index correlated "
            "with the official rates on the same units — while this number holds an entire "
            "province out. On these same rows a random fold reports R² 0.65, which is where those "
            "studies sit. A monetary headcount is also a genuinely harder target than the asset "
            "indices most satellite-poverty papers predict: it is a threshold crossing of a "
            "distribution, and a satellite reads a place's central tendency, not its lower tail."
            if hard_fail else
            "The headline skill number is leave-one-province-out; the random k-fold figure is "
            "published only to show how much a non-spatial fold flatters this kind of model.",
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
    if cover.get("no_buildings"):
        stats["caveats"].append(
            f"{cover['no_buildings']} of {cover['adm3']} kecamatan have no Open Buildings "
            "footprints above the 0.70 confidence cut — mostly very small urban units and "
            "remote islands. They still receive an estimate, driven by the lights, population "
            "and land-cover families, and it is correspondingly weaker.")
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
