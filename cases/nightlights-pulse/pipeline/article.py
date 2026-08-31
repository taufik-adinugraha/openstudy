"""Build the review article's data layer from the case's own published calibration.

Every number the article prints comes from here, so the prose can never drift from
the pipeline. The article pins a data vintage the way a paper pins its sample.
"""
import json
import statistics as st
import sys


def ols(x, y):
    n = len(x); mx = sum(x) / n; my = sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    syy = sum((b - my) ** 2 for b in y)
    b1 = sxy / sxx
    r = sxy / (sxx * syy) ** 0.5
    return {"slope": b1, "intercept": my - b1 * mx, "r": r, "r2": r * r, "n": n}


def main() -> int:
    d = json.load(open(sys.argv[1]))
    out = {"vintage": d["latestMonth"], "generated": d["generated"], "bps": d["bps"]}

    # ── 1. levels, split the way Gibson et al. (2021) split Indonesia ──────────
    sc = d["scatter"]; years = sc["years"]; pts = sc["points"]
    levels = []
    for i, yr in enumerate(years):
        g = {"all": [], "kota": [], "kabupaten": []}
        for p in pts:
            if p["x"][i] is None or p["y"][i] is None:
                continue
            rec = (p["x"][i], p["y"][i])
            g["all"].append(rec)
            g["kota" if p["name"].startswith("Kota") else "kabupaten"].append(rec)
        row = {"year": yr}
        for k, rows in g.items():
            f = ols([r[0] for r in rows], [r[1] for r in rows])
            row[k] = {"slope": round(f["slope"], 4), "r2": round(f["r2"], 4), "n": f["n"]}
        levels.append(row)
    out["levels"] = levels

    # the scatter itself, latest year, for the figure
    li = len(years) - 1
    out["scatterLatest"] = {"year": years[li], "points": [
        {"x": round(p["x"][li], 3), "y": round(p["y"][li], 3),
         "kota": p["name"].startswith("Kota"), "flare": p.get("flare", False),
         "name": p["name"]}
        for p in pts if p["x"][li] is not None and p["y"][li] is not None]}

    # ── 2. the elasticity ladder: signal dying as cross-section is removed ─────
    pan, gr = d["panel"], d["growth"]
    last = levels[-1]["all"]
    out["ladder"] = [
        {"k": "Cross-section, one year", "spec": "log PDRB ~ log lights, 514 regencies",
         "b": last["slope"], "lo": None, "hi": None, "r2": last["r2"], "removes": "nothing"},
        {"k": "Within regency (well-observed)", "spec": "regency FE, ≥6 observed months",
         "b": pan["annual_fe_regency_wellobs"]["beta"], "lo": pan["annual_fe_regency_wellobs"]["lo"],
         "hi": pan["annual_fe_regency_wellobs"]["hi"], "r2": pan["annual_fe_regency_wellobs"]["within_r2"],
         "removes": "all differences between places"},
        {"k": "Within regency (all)", "spec": "regency FE, annual",
         "b": pan["annual_fe_regency"]["beta"], "lo": pan["annual_fe_regency"]["lo"],
         "hi": pan["annual_fe_regency"]["hi"], "r2": pan["annual_fe_regency"]["within_r2"],
         "removes": "all differences between places"},
        {"k": "Within regency and year", "spec": "regency + year FE, annual",
         "b": pan["annual_fe_regency_year"]["beta"], "lo": pan["annual_fe_regency_year"]["lo"],
         "hi": pan["annual_fe_regency_year"]["hi"], "r2": pan["annual_fe_regency_year"]["within_r2"],
         "removes": "places and national shocks"},
        {"k": "Growth, annual", "spec": "Δlog PDRB ~ Δlog lights",
         "b": gr["annual"]["beta"], "lo": gr["annual"]["lo"], "hi": gr["annual"]["hi"],
         "r2": gr["annual"]["r2"], "removes": "all levels"},
        {"k": "Growth, quarterly YoY", "spec": "Δlog PDRB ~ Δlog lights",
         "b": gr["quarterly_yoy"]["beta"], "lo": gr["quarterly_yoy"]["lo"], "hi": gr["quarterly_yoy"]["hi"],
         "r2": gr["quarterly_yoy"]["r2"], "removes": "all levels"},
    ]

    # ── 3. the coverage artifact ──────────────────────────────────────────────
    nc = d["nowcast"]; ser = nc["series"]
    ba = {r["year"]: r["g"] for r in nc["bps_annual"]}
    cov_rows = []
    for y in range(2019, 2027):
        ms = [s for s in ser if s["m"].startswith(str(y))]
        if not ms:
            continue
        cov_rows.append({"year": y, "lights": st.mean(s["gl"] for s in ms),
                         "cov": st.mean(s["cov"] for s in ms), "bps": ba.get(y)})
    out["coverage"] = {"rows": cov_rows,
                       "corr": ols([r["cov"] for r in cov_rows], [r["lights"] for r in cov_rows])["r"]}
    pairs = [(r["lights"], r["bps"]) for r in cov_rows if r["bps"] is not None]
    out["national"] = ols([p[0] for p in pairs], [p[1] for p in pairs])

    # ── 4. the nowcast is a constant ──────────────────────────────────────────
    h = nc["headline"]; q = gr["quarterly_yoy"]
    contrib = q["beta"] * h["lights_growth"]
    out["nowcast"] = {"a": q["a"], "beta": q["beta"], "lo": q["lo"], "hi": q["hi"],
                      "lightsGrowth": h["lights_growth"], "contribution": contrib,
                      "published": h["g"], "band": [h["lo"], h["hi"]],
                      "constantShare": 1 - abs(contrib) / h["g"],
                      "bandVsContribution": (h["hi"] - h["lo"]) / abs(contrib),
                      "monthsBeyondBps": h["months_beyond_bps"]}
    mv = nc["risers"] + nc["fallers"]
    out["movers"] = {"lightsRange": [min(m["lights_growth"] for m in mv), max(m["lights_growth"] for m in mv)],
                     "mappedRange": [min(m["g"] for m in mv), max(m["g"] for m in mv)],
                     "top": sorted(mv, key=lambda m: -m["lights_growth"])[:5],
                     "bottom": sorted(mv, key=lambda m: m["lights_growth"])[:5]}

    out["oos"] = d["oos"]
    out["gates"] = d["gates"]
    out["deseason"] = d["deseason"]
    out["flares"] = {k: d["flares"][k] for k in
                     ("source", "doi", "buffer_km", "n_sites", "national_share",
                      "national_share_5km", "national_share_15km", "n_regencies_flagged")}
    out["flares"]["top"] = d["flares"]["regencies"][:8]
    out["weakFit"] = d["weakFit"]["list"][:8]
    out["weakFitN"] = d["weakFit"]["n_over_2sigma"]

    json.dump(out, open(sys.argv[2], "w"), indent=1)
    print(f"[article] levels {len(levels)} yrs · ladder {len(out['ladder'])} rungs · "
          f"coverage r={out['coverage']['corr']:+.3f} · nowcast {out['nowcast']['constantShare']*100:.1f}% constant")
    print(f"[article] -> {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
