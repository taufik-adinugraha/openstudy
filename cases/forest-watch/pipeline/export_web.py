"""Stage 6 - export.  NaN-safe view-models for the Astro app, each comfortably under 3 MB.

  data/stats.json        gates, vintages, licences, citations, the KLHK divergence panel
  data/adm1.json         simplified province outlines + the 2001-2024 loss series
  data/ignition.json     the hero: weekly first-detection cells on a 0.05-degree grid,
                         stored as parallel integer arrays so 3 years of national alerts
                         cost ~1 MB instead of ~20
  data/clusters.json     the explorer's cluster points (capped, largest first)
  data/ledger.json       province x week counts and hectares
  data/linkage.json      linkage-class hectare shares per province per quarter
  data/mills.json        mills with 12-month alert pressure
"""

from __future__ import annotations

import json
import math
import sys

import geopandas as gpd
import numpy as np
import pandas as pd

import config
from alerts import log

GRID = 0.1                     # degrees, the ignition raster cell (~11 km; keeps the hero < 2 MB)
IGNITION_YEARS = 3
MAX_CLUSTERS = 9_000           # largest-first; the page states the cap and the cut-off size
MAX_MILLS = 1_200


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not math.isfinite(float(o)) else round(float(o), 5)
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return str(o)[:10]
    if o is pd.NaT or (isinstance(o, float) and math.isnan(o)):
        return None
    return o


def write(name: str, obj) -> None:
    config.WEB_DATA.mkdir(parents=True, exist_ok=True)
    p = config.WEB_DATA / name
    p.write_text(json.dumps(clean(obj), separators=(",", ":")))
    log(f"  {name}: {p.stat().st_size/1024:.0f} kB")


def main(argv: list[str]) -> None:
    linked = pd.read_parquet(config.LINKED)
    linked["first_date"] = pd.to_datetime(linked.first_date)
    linked["last_date"] = pd.to_datetime(linked.last_date)
    stats = json.loads(config.STATS_JSON.read_text()) if config.STATS_JSON.exists() else {}

    write("stats.json", stats)

    # --- provinces + loss series -------------------------------------------------------
    prov = gpd.read_parquet(config.BOUNDARIES)[["province", "geometry"]].copy()
    prov["geometry"] = prov.geometry.simplify(0.01).buffer(0)
    loss = (pd.read_parquet(config.LOSS_TABLE) if config.LOSS_TABLE.exists()
            else pd.DataFrame(columns=["province", "year", "loss_ha"]))
    series = {p: dict(zip(g.year.astype(int), g.loss_ha.round(0)))
              for p, g in loss.groupby("province")}
    alert_by_prov = linked.groupby("province").agg(alert_ha=("ha", "sum"),
                                                   clusters=("ha", "size"))
    lk = linked.groupby("province").apply(
        lambda g: float(g.loc[g.linked].ha.sum() / g.ha.sum()) if g.ha.sum() else 0.0,
        include_groups=False)
    gj = json.loads(prov.to_json())
    for f in gj["features"]:
        n = f["properties"]["province"]
        f["properties"] = {
            "province": n,
            "loss": series.get(n, {}),
            "alert_ha": float(alert_by_prov.alert_ha.get(n, 0.0)),
            "clusters": int(alert_by_prov.clusters.get(n, 0)),
            "linked_share": float(lk.get(n, 0.0)),
        }
    write("adm1.json", clean(gj))

    # --- hero ignition ------------------------------------------------------------------
    last = linked.first_date.max()
    ign = linked.loc[linked.first_date >= last - pd.Timedelta(days=365 * IGNITION_YEARS)].copy()
    ign["w"] = ((ign.first_date - ign.first_date.min()).dt.days // 7).astype(int)
    ign["gx"] = np.round((ign.lon - config.BBOX_IDN[0]) / GRID).astype(int)
    ign["gy"] = np.round((config.BBOX_IDN[3] - ign.lat) / GRID).astype(int)
    cells = ign.groupby(["w", "gx", "gy"]).agg(ha=("ha", "sum"),
                                               lk=("linked", "mean")).reset_index()
    week0 = ign.first_date.min().normalize()
    write("ignition.json", {
        "origin": [config.BBOX_IDN[0], config.BBOX_IDN[3]], "grid": GRID,
        "week0": str(week0)[:10], "n_weeks": int(cells.w.max()) + 1,
        "w": cells.w.tolist(), "x": cells.gx.tolist(), "y": cells.gy.tolist(),
        "ha": [round(float(v), 1) for v in cells.ha], "lk": [round(float(v), 2) for v in cells.lk],
        "total_ha": float(ign.ha.sum()), "total_clusters": int(len(ign)),
    })

    # --- explorer clusters ----------------------------------------------------------------
    cols = ["cluster_id", "lon", "lat", "ha", "first_date", "last_date", "hi_share",
            "link_class", "on_peat", "in_primary", "province", "mill_dist_km",
            "nearest_mill", "nearest_mill_group", "nearest_mill_rspo", "glad_agree",
            "palm_share"]
    cl = linked.nlargest(MAX_CLUSTERS, "ha")[cols].copy()
    cl["first_date"] = cl.first_date.dt.strftime("%Y-%m-%d")
    cl["last_date"] = cl.last_date.dt.strftime("%Y-%m-%d")
    for c in ("lon", "lat"):
        cl[c] = cl[c].round(4)
    cl["ha"] = cl.ha.round(2)
    cl["mill_dist_km"] = cl.mill_dist_km.round(1)
    for c in ("hi_share", "palm_share"):
        cl[c] = cl[c].round(3)
    write("clusters.json", {"n_total": int(len(linked)), "n_shown": int(len(cl)),
                            "min_ha_shown": float(cl.ha.min()),
                            "rows": clean(cl.to_dict("records"))})

    # --- weekly ledger ---------------------------------------------------------------------
    led = (linked.assign(week=linked.first_date.dt.to_period("W-SUN").dt.start_time)
           .groupby(["province", "week"])
           .agg(ha=("ha", "sum"), n=("ha", "size"),
                linked_ha=("ha", lambda s: 0.0)).reset_index())
    lk_ha = (linked.loc[linked.linked]
             .assign(week=linked.loc[linked.linked].first_date.dt.to_period("W-SUN")
                     .dt.start_time)
             .groupby(["province", "week"]).ha.sum().rename("lha").reset_index())
    led = led.drop(columns="linked_ha").merge(lk_ha, on=["province", "week"], how="left")
    led["lha"] = led.lha.fillna(0.0)
    led["week"] = led.week.dt.strftime("%Y-%m-%d")
    write("ledger.json", {"rows": clean(led.round(1).to_dict("records"))})

    # --- linkage shares per province per quarter --------------------------------------------
    q = (linked.groupby(["province", "quarter", "link_class"]).ha.sum()
         .unstack(fill_value=0.0).reset_index())
    flags = (linked.groupby(["province", "quarter"])
             .apply(lambda g: pd.Series({
                 "peat_ha": float(g.loc[g.on_peat].ha.sum()),
                 "primary_ha": float(g.loc[g.in_primary].ha.sum())}),
                 include_groups=False).reset_index())
    q = q.merge(flags, on=["province", "quarter"], how="left")
    write("linkage.json", {"classes": ["PALM-INTERNAL", "PALM-EDGE", "MILL-CATCHMENT",
                                       "UNLINKED"],
                           "rows": clean(q.round(1).to_dict("records"))})

    # --- review view-model -------------------------------------------------------------------
    # The base rate, the lift and the unfiltered share, so the dashboard can print a denominator
    # next to every conditional share instead of quoting it bare.  Written by pipeline/baserate.py.
    br_path = config.DATA_DIR / "baserate.json"
    if br_path.exists():
        br = json.loads(br_path.read_text())
        cat, ctl = br.get("catchment", {}), br.get("controls", {})
        ef = ctl.get("event_floor", {})
        n50 = cat.get("national", {}).get("50", {})
        best = max(cat.get("national", {}).items(),
                   key=lambda kv: kv[1].get("lift_vs_domain") or 0, default=(None, {}))
        prov = cat.get("by_province", {})
        java = ("Banten", "West Java", "Central Java", "East Java", "Yogyakarta",
                "Jakarta Special Capital Region")
        nusa = ("West Nusa Tenggara", "East Nusa Tenggara", "Bali")
        byp = linked.groupby("province").ha.sum()
        write("review.json", {
            "generated": br.get("generated"),
            "radius_km": config.MILL_RADIUS_KM,
            "domain_base": n50.get("domain_base"),
            "land_base": n50.get("land_base"),
            "alert_share": n50.get("alert_ha_share"),
            "lift": n50.get("lift_vs_domain"),
            "best_radius_km": int(best[0]) if best[0] else None,
            "best_radius_lift": best[1].get("lift_vs_domain"),
            "best_radius_alert": best[1].get("alert_ha_share"),
            "best_radius_base": best[1].get("domain_base"),
            "unfiltered_share": ef.get("unfiltered_within_50km"),
            "event_floor_keeps_national": ef.get("event_floor_keeps"),
            "mills_per_hectare": ctl.get("identifiability", {})
                                    .get("mills_claiming_a_hectare", {})
                                    .get("mean_over_alert_ha_in_catchment"),
            "domain_ha": cat.get("domain_ha"),
            "land_ha": cat.get("land_ha"),
            "domain_share_of_land": cat.get("domain_share_of_land"),
            "palm_in_domain_share": ctl.get("palm_extent", {})
                                       .get("sdpt_palm_in_radd_domain_share"),
            "sdpt_palm_ha": ctl.get("palm_extent", {}).get("sdpt_ha", {}).get("oil_palm"),
            "riau_base": prov.get("Riau", {}).get("domain_base_50"),
            "riau_lift": prov.get("Riau", {}).get("lift_50"),
            "java_ha": float(byp.reindex(java).fillna(0).sum()),
            "nusa_ha": float(byp.reindex(nusa).fillna(0).sum()),
            "provinces_with_alerts": int((byp > 0).sum()),
            "plantation_loss_share": br.get("loss_split", {}).get("in_plantation_share", {}),
            "plantation_loss_grand_share": (
                sum(br.get("loss_split", {}).get("in_plantation_ha", {}).values())
                / sum(br.get("loss_split", {}).get("total_ha", {}).values())
                if br.get("loss_split", {}).get("total_ha") else None),
        })

    # --- mills -------------------------------------------------------------------------------
    if config.MILLS_SCORED.exists():
        m = pd.read_parquet(config.MILLS_SCORED)
        if "alert_ha_12m" in m.columns:
            m = m.nlargest(MAX_MILLS, "alert_ha_12m")
        keep = [c for c in ("uml_id", "mill_name", "parent_com", "group_name", "province",
                            "district", "rspo_statu", "latitude", "longitude",
                            "alert_ha_12m", "alert_clusters_12m", "peat_alert_ha_12m")
                if c in m.columns]
        m = m[keep].copy()
        for c in ("latitude", "longitude"):
            m[c] = m[c].round(4)
        for c in ("alert_ha_12m", "peat_alert_ha_12m"):
            if c in m:
                m[c] = m[c].round(1)
        write("mills.json", {"rows": clean(m.to_dict("records"))})
    log("export complete")


if __name__ == "__main__":
    main(sys.argv[1:])
