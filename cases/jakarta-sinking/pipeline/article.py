"""Build the review article's data layer from the case's own published outputs.

Reads only what the case publishes (web/public/data/exposure.json,
web/src/data/summary.json, data/derived/stats.json) plus the extra tests in
data/derived/review.json, and writes web/src/data/article.json. Every number the
article prints comes from here, so the prose cannot drift from the pipeline.

Run: uv run python pipeline/article.py
"""

from __future__ import annotations

import json
import sys

import numpy as np

import config

DERIVED = config.DATA_DIR / "derived"
WEB = config.CASE_DIR / "web"

# The one set of published numbers in this file: Ohenhen et al. (2026), Science Advances,
# doi:10.1126/sciadv.aec0172, supplementary table S2 — the depositors' own per-municipality
# statistics for the very field this case regrids. cm/yr, negative = subsidence.
OHENHEN_S2 = {
    "Jakarta Barat":   {"mean": -1.067, "median": -0.894, "sd": 0.840, "min": -4.775, "share_neg": 94.8, "share_lt1": 45.6},
    "Jakarta Pusat":   {"mean": 0.012, "median": 0.020, "sd": 0.324, "min": -1.283, "share_neg": 47.3, "share_lt1": 0.56},
    "Jakarta Selatan": {"mean": -0.413, "median": -0.380, "sd": 0.417, "min": -2.746, "share_neg": 84.4, "share_lt1": 7.3},
    "Jakarta Timur":   {"mean": -0.164, "median": -0.132, "sd": 0.314, "min": -2.624, "share_neg": 72.8, "share_lt1": 2.0},
    "Jakarta Utara":   {"mean": -0.707, "median": -0.411, "sd": 1.064, "min": -5.643, "share_neg": 72.7, "share_lt1": 28.1},
}


def rank(a):
    """Average ranks, so ties do not manufacture order."""
    a = np.asarray(a, dtype="float64")
    order = np.argsort(a, kind="mergesort")
    r = np.empty(len(a), dtype="float64")
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and a[order[j + 1]] == a[order[i]]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = rank(a) - np.mean(rank(a)), rank(b) - np.mean(rank(b))
    return float((ra * rb).sum() / np.sqrt((ra ** 2).sum() * (rb ** 2).sum()))


def interp_height(curve, target):
    """Height on the cell-mean exposure curve carrying `target` people."""
    h, y = curve["heights"], curve["mean_2025"]
    for i in range(1, len(h)):
        if y[i] >= target:
            f = (target - y[i - 1]) / max(y[i] - y[i - 1], 1)
            return round(h[i - 1] + f * (h[i] - h[i - 1]), 2)
    return h[-1]


def main() -> int:
    X = json.loads((WEB / "public" / "data" / "exposure.json").read_text())
    S = json.loads((WEB / "src" / "data" / "summary.json").read_text())
    R = json.loads((DERIVED / "review.json").read_text())
    ST = json.loads((DERIVED / "stats.json").read_text())

    K = X["kel"]
    main_k = {i: k for i, k in K.items() if not k["island"]}
    ranked = {i: k for i, k in main_k.items() if (k["cov"] or 0) >= config.GATE_COVERAGE_MIN}
    ids = list(ranked)
    pop_city = X["city"]["pop"]

    # ── the velocity field, as kelurahan see it ───────────────────────────────
    vmed = np.array([ranked[i]["v_med"] for i in ids])
    vp10 = np.array([ranked[i]["v_p10"] for i in ids])
    velocity = {
        "n": len(ids),
        "med_q": {q: round(float(np.percentile(vmed, p)), 2) for q, p in
                  (("p5", 5), ("p25", 25), ("p50", 50), ("p75", 75), ("p95", 95))},
        "n_uplift": int((vmed > 0).sum()),
        "n_slower_than_half": int((vmed > -0.5).sum()),
        "n_med_fast2": int((vmed < -2).sum()),
        "n_p10_fast2": int((vp10 < -2).sum()),
        "pop_fast2": S["pop_fast2"], "pop_fast1": S["pop_fast1"],
        "share_fast2": round(S["pop_fast2"] / pop_city, 4),
        "share_fast1": round(S["pop_fast1"] / pop_city, 4),
        "area_fast2_share": S["area_fast2_share"], "area_fast1_share": S["area_fast1_share"],
        # every kelurahan, for the distribution figure
        "med_all": [round(float(v), 2) for v in np.sort(vmed)],
    }

    # ── what the 2050 exposure ranking is actually made of ────────────────────
    e50 = [ranked[i]["pop1"][-1] for i in ids]
    drivers = [
        ("low ground (share below +1 m)", spearman(e50, [ranked[i]["low1"] or 0 for i in ids])),
        ("population × low-ground share", spearman(e50, [ranked[i]["pop"] * (ranked[i]["low1"] or 0) for i in ids])),
        ("the same map today (2025)", spearman(e50, [ranked[i]["pop1"][0] for i in ids])),
        ("the same map with the radar off", R["F"]["rho_2050_vs_no_subsidence"]),
        ("resident population", spearman(e50, [ranked[i]["pop"] for i in ids])),
        ("measured sinking rate (−p10)", spearman(e50, [-(ranked[i]["v_p10"] or 0) for i in ids])),
        ("flood events recorded 2021–24", spearman(e50, [ranked[i]["fl_ev"] or 0 for i in ids])),
    ]
    ranking = {"drivers": [{"k": k, "rho": round(v, 3)} for k, v in drivers],
               "top20_shared_no_radar": R["F"]["top20_shared_no_subsidence"],
               "city_2050_with": R["F"]["city_2050_with"], "city_2050_without": R["F"]["city_2050_without"]}

    # ── where the fast ground actually is ─────────────────────────────────────
    gj = json.loads((WEB / "public" / "data" / "kelurahan.geojson").read_text())
    cen = {}
    for f in gj["features"]:
        pts: list = []
        def walk(c):
            if isinstance(c[0], (int, float)):
                pts.append(c)
            else:
                for x in c:
                    walk(x)
        walk(f["geometry"]["coordinates"])
        cen[f["properties"]["id"]] = (round(sum(p[0] for p in pts) / len(pts), 4),
                                      round(sum(p[1] for p in pts) / len(pts), 4))
    COAST_LAT = -6.14                       # the northern belt: everything seaward of this line
    scatter = [{"id": i, "name": ranked[i]["name"], "kota": ranked[i]["kota"],
                "lon": cen.get(i, (None, None))[0], "lat": cen.get(i, (None, None))[1],
                "v_med": ranked[i]["v_med"], "v_p10": ranked[i]["v_p10"],
                "ground": ranked[i]["ground_p10"], "low1": ranked[i]["low1"],
                "pop": round(ranked[i]["pop"]), "e50": ranked[i]["pop1"][-1],
                "coastal": bool(cen.get(i, (0, -99))[1] > COAST_LAT)} for i in ids]
    top20 = sorted(scatter, key=lambda r: r["v_med"])[:20]
    kota_counts: dict = {}
    for r in top20:
        kota_counts[r["kota"]] = kota_counts.get(r["kota"], 0) + 1
    geography = {
        "coast_lat": COAST_LAT, "n_top20_coastal": sum(1 for r in top20 if r["coastal"]),
        "top20_by_kota": kota_counts,
        "rho_rate_vs_lowground": round(spearman([-(r["v_p10"]) for r in scatter], [r["low1"] for r in scatter]), 3),
        "rho_rate_vs_ground": round(spearman([-(r["v_p10"]) for r in scatter], [r["ground"] for r in scatter]), 3),
        "fastest_inland": [r for r in top20 if not r["coastal"]][:5],
        "n_coastal": sum(1 for r in scatter if r["coastal"]),
    }

    # ── the city series, and the step hiding in it ────────────────────────────
    def step(key):
        s = X["city"][key]
        d = [s[i + 1] - s[i] for i in range(len(s) - 1)]
        return {"series": s, "first_step": d[0], "median_rest": float(np.median(d[1:])),
                "total": s[-1] - s[0], "first_share": round(d[0] / (s[-1] - s[0]), 3),
                "share_2025": round(s[0] / pop_city, 4), "share_2050": round(s[-1] / pop_city, 4)}
    series = {"years": X["years"], "below1m": step("pop1"), "below0m": step("pop0"),
              "built1m": X["city"]["built1"], "built0m": X["city"]["built0"]}

    # ── the clock, read strictly ──────────────────────────────────────────────
    cl = [(i, ranked[i]["clk_med_0m"]) for i in ids]
    horizon = X["years"][-1] - X["base_year"]
    clock = {
        "horizon": horizon, "base_year": X["base_year"],
        "already_below": sum(1 for _, c in cl if c == 0),
        "no_clock": sum(1 for _, c in cl if c is None),
        "within_horizon": sum(1 for _, c in cl if c is not None and 0 < c <= horizon),
        "beyond_century": sum(1 for _, c in cl if c is not None and c > 100),
        "soonest": S["soonest_0m"][:6],
        "assumption": S["assumption"],
    }

    # ── the extra tests ───────────────────────────────────────────────────────
    curve = R["curve"]
    est = R["C"]["estimators"]
    estimator = {
        **R["C"],
        "equivalent_mean_height_m": interp_height(curve, est["min"]["1m"]["2025"]),
        "curve": curve,
        "pop_city": pop_city,
    }
    epoch = {**R["D"], "clock_year_equivalent": None}
    # which year of the published clock the uncorrected DEM epoch is worth
    s1 = X["city"]["pop1"]
    tgt = epoch["counts"]["1m"]["2025"]
    for j, v in enumerate(s1):
        if v >= tgt:
            epoch["clock_year_equivalent"] = X["years"][j]
            break

    out = {
        "vintage": S["generated"], "built_on": S["built_on"], "window": S["window"], "source": S["source"],
        "points": S["points"], "unique": S["unique"],
        "city": {"pop": pop_city, "ghs_pop": X["city"]["ghs_pop"], "official": S["official_pop"],
                 "built_km2": X["city"]["built"], "area_km2": S["area_km2"],
                 "n_kel": S["n_kel"], "n_mainland": S["n_mainland"], "n_ranked": S["n_ranked"]},
        "velocity": velocity,
        "hotspots": S["hotspots"],
        "fastest": S["fastest_p10"][:8],
        "gnss": {"published": S["gnss"], **R["B"]},
        "decel": R["A"], "gnssSeries": R["series"],
        "ranking": ranking, "series": series, "clock": clock,
        "geography": geography, "scatter": scatter,
        "estimator": estimator, "epoch": epoch, "datum": R["E"], "decay": R["G"],
        "replication": [{**r, "lit": OHENHEN_S2.get(r["kota"])} for r in R["H"]],
        "replication_max_err": round(max(abs(r["mean"] - OHENHEN_S2[r["kota"]]["mean"])
                                         for r in R["H"] if r["kota"] in OHENHEN_S2), 3),
        "replication_source": "Ohenhen et al. (2026) Science Advances, doi:10.1126/sciadv.aec0172, table S2",
        "area": R["area"],
        "gates": {k: {"pass": S["gates"][k]["pass"], "label": S["gates"][k]["label"]} for k in S["gates"]},
        "flood": ST["gates"]["flood_plausibility"],
        "coverage": {"min": round(min(ranked[i]["cov"] for i in ids), 3),
                     "median": round(float(np.median([ranked[i]["cov"] for i in ids])), 3),
                     "gapfill_note": X["field"]["note"]},
    }
    (WEB / "src" / "data" / "article.json").write_text(json.dumps(_clean(out), indent=1, allow_nan=False))
    print(f"[article] velocity median {velocity['med_q']['p50']:+.2f} cm/yr · {velocity['n_uplift']} kelurahan rising · "
          f"ranking rho(no radar) {ranking['drivers'][3]['rho']} · estimator span "
          f"{est['mean']['1m']['2025']:,}–{est['min']['1m']['2025']:,}")
    print(f"[article] -> {WEB / 'src' / 'data' / 'article.json'}")
    return 0


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


if __name__ == "__main__":
    sys.exit(main())
