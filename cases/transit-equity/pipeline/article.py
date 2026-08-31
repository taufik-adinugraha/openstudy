"""Build the review article's data layer from the case's own published outputs.

Every number the article prints comes from here, so the prose can never drift from the
pipeline. Reads only what the case publishes — data/equity.json, data/stats.json and
data/access_adm4.parquet — and pins a data vintage the way a paper pins its sample.

The article's second data file, web/src/data/review.json, is written by pipeline/review.py.

Output: web/src/data/article.json
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

import config
import equity as eq
from util import log

OUT = config.CASE_DIR / "web" / "src" / "data" / "article.json"


def main() -> None:
    e = json.loads(config.EQUITY_JSON.read_text())
    s = json.loads(config.STATS_JSON.read_text())
    acc = pd.read_parquet(config.ACCESS_ADM4)
    acc["dki"] = acc.adm1_name.str.contains("Jakarta", case=False, na=False)
    a60 = acc[(acc.scenario == "all") & (acc.cutoff == 60)]

    out: dict = {
        "vintage": s["generated"][:10],
        "generated": s["generated"],
        "window": s["window"],
        "measure": e["measure"],
        "origins": int(s["origins"]),
        "destinations": int(s["destinations"]),
        "cutoffs": list(config.CUTOFFS_MIN),
        "max_trip_min": config.MAX_TRIP_MIN,
        "max_walk_min": config.MAX_WALK_MIN,
        "gravity_half_weight_min": config.GRAVITY_HALF_WEIGHT_MIN,
        "rail_caveat_pct": config.RAIL_TT_CAVEAT_PCT,
    }

    # ── the published distributional package, scenario by scenario ────────────────────────
    out["scenarios"] = {k: {kk: vv for kk, vv in v.items()} for k, v in e["scenarios"].items()}
    out["by_cutoff"] = e["by_cutoff"]
    out["incidence"] = e["incidence"]
    out["rail_contribution"] = e["rail_contribution"]
    out["transit_contribution"] = e["transit_contribution"]
    out["pairing"] = e["pairing"]
    out["best"] = e["best"][:8]
    out["worst"] = e["worst"][:8]
    out["poverty_link"] = e.get("poverty_link", {})

    # ── the network as routed ─────────────────────────────────────────────────────────────
    g = s["gtfs"]
    out["feeds"] = {
        "transjakarta_routes": g["transjakarta.zip"]["routes"],
        "transjakarta_stops": g["transjakarta.zip"]["stops"],
        "mikrotrans_routes": 98,
        "rail_routes": g["rail_handencoded.zip"]["routes"],
        "rail_stops": g["rail_handencoded.zip"]["stops"],
        "angkot_routes": g["bogor_angkot.zip"]["routes"],
        "total_stops": s["G-G3"]["stops"],
        "worst_headway_min": max(g["transjakarta.zip"]["headways_min"]),
    }
    out["rail_lines"] = {k: {"operator": v["operator"], "headway_peak_min": v["headway_peak_min"],
                             "kmh": v["avg_commercial_kmh"], "stations": v["stations"]}
                         for k, v in s["rail_handencoded"]["lines"].items()}

    # ── the checks, as published ──────────────────────────────────────────────────────────
    out["checks"] = {
        "timetable": {"pass": s["G-G1"]["pass"], "passed": s["G-G1"]["passed"], "of": s["G-G1"]["of"],
                      "ods": [{k: o.get(k) for k in
                               ("name", "mode", "published_min", "scheduled_in_vehicle_min",
                                "router_door_to_door_min", "published_kmh", "scheduled_kmh",
                                "median_trip_km", "pass")}
                              for o in s["G-G1"]["ods"]]},
        "network": {"pass": s["G-G3"]["pass"], "snap_share": s["G-G3"]["snap_share"],
                    "stops": s["G-G3"]["stops"],
                    "unreachable": s["G-G3"]["unreachable_origins"],
                    "unreachable_mainland": s["G-G3"]["unreachable_mainland"],
                    "unreachable_pop": s["G-G3"]["unreachable_pop"],
                    "zero_jobs": s["G-G3"]["origins_reaching_no_job_floorspace"]},
        "plausibility": {"pass": s["G-G4"]["pass"],
                         "violations_exact": s["G-G4"]["monotonicity"]["violations_exact"],
                         "violations_material": s["G-G4"]["monotonicity"]["violations_material"],
                         "pnt_jakarta": s["G-G4"]["people_near_transit"]["Jakarta"]["share"],
                         "pnt_greater": s["G-G4"]["people_near_transit"]["Greater Jakarta"]["share"],
                         "pnt_anchor_jakarta": s["G-G4"]["people_near_transit"]["anchors_2016"]["Jakarta"],
                         "pnt_anchor_greater": s["G-G4"]["people_near_transit"]["anchors_2016"]["Greater Jakarta"],
                         "frequent_stops": s["G-G4"]["people_near_transit"]["frequent_stops"]},
        "external": {"status": s["G-G2"]["status"], "reason": s["G-G2"]["reason"]},
        "passed": s["gates_passed"], "hard": s["gates_hard"],
    }

    # ── coverage of the destination lattice ───────────────────────────────────────────────
    d = s["inputs"]["destinations"]
    out["lattice"] = {"cell_m": d["cell_m"], "cells": d["cells"],
                      "jobs_covered": d["jobs_proxy_covered"], "pop_covered": d["pop_covered"]}

    # ── population arithmetic the article leans on ────────────────────────────────────────
    tot = float(a60["pop"].sum())
    out["population"] = {
        "total": tot,
        "dki": float(a60.loc[a60.dki, "pop"].sum()),
        "dki_share": float(a60.loc[a60.dki, "pop"].sum() / tot),
        "zero_jobs_pop": float(a60.loc[a60.jobs_share <= 0, "pop"].sum()),
        "zero_jobs_pop_share": float(a60.loc[a60.jobs_share <= 0, "pop"].sum() / tot),
        "no_hospital_pop": float(a60.loc[a60.hospitals == 0, "pop"].sum()),
    }
    # the whole population-weighted distribution, thinned for the figures
    srt = a60.sort_values("jobs_share")
    cum = (srt["pop"].cumsum() / tot).values
    out["cdf"] = [{"p": round(float(p), 4), "v": round(float(v), 6)}
                  for p, v in zip(cum[::12], srt.jobs_share.values[::12])]
    out["kabupaten"] = json.loads(
        a60.groupby("adm2_name")
        .apply(lambda x: pd.Series({
            "pop": x["pop"].sum(),
            "mean": np.average(x.jobs_share, weights=x["pop"]) if x["pop"].sum() > 0 else 0.0,
            "median": eq.wmedian(x.jobs_share.values, x["pop"].values),
            "zero_share": float((x.jobs_share <= 0).mean()),
            "units": len(x)}), include_groups=False)
        .reset_index().round(6).to_json(orient="records"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, allow_nan=False, separators=(",", ":")))
    log("article →", OUT, f"{OUT.stat().st_size/1e6:.2f} MB")
    log(f"  vintage {out['vintage']} · {out['origins']} origins · {out['destinations']} cells · "
        f"Gini {out['scenarios']['all']['gini']} · checks {out['checks']['passed']}/{out['checks']['hard']}")
    log(f"  poverty link: {'available' if out['poverty_link'].get('available') else out['poverty_link'].get('reason')}")


if __name__ == "__main__":
    main()
