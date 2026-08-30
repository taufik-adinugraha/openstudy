"""Stage 5 - validate.  Gates G-H1..G-H4 -> data/stats.json.

The comparator is the GFW Data API's own on-demand raster analysis: POST a GeoJSON geometry
plus SQL to /dataset/{ds}/{version}/query/json and GFW aggregates the same tiles server-side.
Because we pass *our* COD-AB province geometry and *their* version string, the comparison
isolates our raster plumbing (windowed reads, cos-latitude pixel area, province rasterising,
seam stitching) from any difference in source data or boundaries.

  G-H1  tree-cover loss ha, per province x year 2015-2024, ours vs the API      +/- 5 %  hard
  G-H2  RADD alerts, trailing 12 months, per focus province, ours vs the API    +/- 10 % hard
  G-H3  >= 60 % of high-confidence RADD clusters >= 5 ha carry a GLAD-L alert within +/- 60 d
  G-H4  palm+mill-linked share of alert hectares in Riau >= 25 % (literature floor)

Lab rule: if the data contradicts the story the story is rewritten. Gates are diagnosed, never
tuned. A gate that cannot be evaluated is recorded as "pending" with the reason, never as a
pass.  Also assembles the KLHK-vs-GFW definitional divergence panel.
"""

from __future__ import annotations

import json
import sys
from datetime import date

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

import config
from alerts import RAW_GRID, log

TOL_YEARS = range(2015, 2025)


def _nan_safe(o):
    if isinstance(o, dict):
        return {k: _nan_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_nan_safe(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else round(float(o), 6)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, date)):
        return str(o)[:10]
    return o


def api_query(dataset: str, version: str, sql: str, geometry=None, timeout=300):
    body = {"sql": sql}
    if geometry is not None:
        body["geometry"] = geometry
    try:
        r = requests.post(config.GFW_QUERY_URL.format(dataset=dataset, version=version),
                          headers=config.GFW_HEADERS, json=body, timeout=timeout)
    except requests.RequestException as exc:
        return None, f"{type(exc).__name__}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:160]}"
    return r.json().get("data", []), None


def gate_h1(prov: gpd.GeoDataFrame, ours: pd.DataFrame) -> dict:
    spec = config.RASTERS["tcl30"]
    sql = ("SELECT umd_tree_cover_loss__year, SUM(area__ha) FROM data "
           "WHERE umd_tree_cover_density_2000__threshold >= 30 "
           "GROUP BY umd_tree_cover_loss__year")
    rows, fails = [], {}
    for _, p in prov.iterrows():
        geom = p.geometry.simplify(0.002).buffer(0)
        data, err = api_query(spec["dataset"], spec["version"], sql,
                              json.loads(gpd.GeoSeries([geom], crs=4326).to_json())
                              ["features"][0]["geometry"])
        if err:
            fails[p.province] = err
            continue
        if not data:
            continue
        # The API names the columns after the dataset, and the exact spelling moves between
        # versions, so bind by shape (the year-ish key and the hectare-ish key) not by literal.
        k0 = next((k for k in data[0] if "year" in k), None)
        k1 = next((k for k in data[0] if "ha" in k or "area" in k), None)
        if k0 is None or k1 is None:
            fails[p.province] = f"unexpected response columns: {list(data[0])}"
            continue
        ref = {int(d[k0]): float(d[k1]) for d in data if d.get(k0) is not None}
        mine = dict(zip(ours.loc[ours.province == p.province, "year"],
                        ours.loc[ours.province == p.province, "loss_ha"]))
        for y in TOL_YEARS:
            a, b = mine.get(y, 0.0), ref.get(y, 0.0)
            if b <= 0:
                continue
            rows.append({"province": p.province, "year": y, "ours_ha": a, "gfw_ha": b,
                         "pct_diff": 100 * (a - b) / b})
        log(f"  G-H1 {p.province}: {len(ref)} reference years")
    if not rows:
        return {"status": "pending", "reason": "no reference years returned", "errors": fails}
    df = pd.DataFrame(rows)
    worst = df.loc[df.pct_diff.abs().idxmax()]
    return {"status": "pass" if df.pct_diff.abs().max() <= config.GATE_LOSS_TOL_PCT else "fail",
            "tolerance_pct": config.GATE_LOSS_TOL_PCT, "n_comparisons": len(df),
            "max_abs_pct_diff": float(df.pct_diff.abs().max()),
            "median_abs_pct_diff": float(df.pct_diff.abs().median()),
            "within_tolerance_share": float((df.pct_diff.abs()
                                             <= config.GATE_LOSS_TOL_PCT).mean()),
            "worst": _nan_safe(worst.to_dict()),
            "comparator": f"{spec['dataset']} {spec['version']} raster analysis over COD-AB "
                          "province geometry",
            "errors": fails,
            "rows": _nan_safe(df.to_dict("records"))}


def raw_grid(prov: gpd.GeoDataFrame, min_wk: int, names: tuple[str, ...]) -> pd.DataFrame:
    """Unfiltered alert hectares per province per week, for weeks at or after ``min_wk``.

    This, not the >= 0.5 ha event table, is the like-for-like comparator for G-H2: GFW's
    aggregation counts every alert pixel, and the event floor is a presentation choice we make
    downstream.  Comparing the filtered table against an unfiltered reference would guarantee a
    failure that says nothing about our plumbing.

    Streamed one tile at a time and filtered on week *before* the spatial join — loading all 13
    tiles of the 0.05-degree x weekly grid at once is ~20 M rows and OOM-kills a 3 GB unit.
    """
    target = prov.loc[prov.province.isin(names), ["province", "geometry"]]
    out = []
    for f in sorted(config.ALERTS_DIR.glob("raw_*.parquet")):
        g = pd.read_parquet(f)
        g = g.loc[g.wk >= min_wk]
        if g.empty:
            continue
        lon = config.BBOX_IDN[0] + (g.gx + 0.5) * RAW_GRID
        lat = config.BBOX_IDN[3] - (g.gy + 0.5) * RAW_GRID
        pts = gpd.GeoDataFrame(g[["ha", "px"]], geometry=gpd.points_from_xy(lon, lat), crs=4326)
        j = gpd.sjoin(pts, target, how="inner", predicate="within")
        if len(j):
            out.append(pd.DataFrame(j[["province", "ha", "px"]])
                       .groupby("province", as_index=False).sum())
        del g, pts, j
    if not out:
        return pd.DataFrame(columns=["province", "ha", "px"])
    return pd.concat(out, ignore_index=True).groupby("province", as_index=False).sum()


def gate_h2(prov: gpd.GeoDataFrame, linked: pd.DataFrame) -> dict:
    spec = config.RASTERS["radd"]
    last = pd.to_datetime(linked.last_date).max()
    start_ts = last - pd.Timedelta(days=365)
    start = start_ts.date().isoformat()
    start_day = (start_ts - pd.Timestamp(config.ALERT_EPOCH)).days
    sql = ("SELECT count(*) AS n, SUM(area__ha) AS ha FROM data "
           f"WHERE wur_radd_alerts__date >= '{start}'")
    grid = raw_grid(prov, start_day // 7, config.FOCUS_PROVINCES)
    rows, fails = [], {}
    for name in config.FOCUS_PROVINCES:
        sel = prov.loc[prov.province == name]
        if sel.empty:
            fails[name] = "province not found in the boundary file"
            continue
        geom = sel.geometry.iloc[0].simplify(0.002).buffer(0)
        data, err = api_query(spec["dataset"], spec["version"], sql,
                              json.loads(gpd.GeoSeries([geom], crs=4326).to_json())
                              ["features"][0]["geometry"])
        if err:
            fails[name] = err
            continue
        ref_ha = float(data[0].get("ha") or 0.0)
        ours_ha = float(grid.loc[grid.province == name, "ha"].sum())
        ev = linked.loc[(linked.province == name)
                        & (pd.to_datetime(linked.first_date) >= start_ts)]
        rows.append({"province": name, "ours_ha": ours_ha, "gfw_ha": ref_ha,
                     "events_ha": float(ev.ha.sum()), "events": int(len(ev)),
                     "gfw_pixels": int(data[0].get("count") or 0),
                     "pct_diff": 100 * (ours_ha - ref_ha) / ref_ha if ref_ha else float("nan")})
        log(f"  G-H2 {name}: ours {ours_ha:,.0f} ha vs GFW {ref_ha:,.0f} ha "
            f"({100 * (ours_ha - ref_ha) / ref_ha:+.1f} %); "
            f"events >= 0.5 ha keep {ev.ha.sum() / ours_ha:.0%}" if ref_ha and ours_ha else "")
    if not rows:
        return {"status": "pending", "reason": "no reference aggregation returned",
                "errors": fails}
    df = pd.DataFrame(rows)
    keep = float(df.events_ha.sum() / df.ours_ha.sum()) if df.ours_ha.sum() else float("nan")
    return {"status": "pass" if df.pct_diff.abs().max() <= config.GATE_ALERT_TOL_PCT else "fail",
            "tolerance_pct": config.GATE_ALERT_TOL_PCT, "window_start": start,
            "window_end": str(last)[:10],
            "max_abs_pct_diff": float(df.pct_diff.abs().max()),
            "event_floor_keeps_share_of_ha": keep,
            "note": "compared on unfiltered alert hectares, because GFW's aggregation counts "
                    "every alert pixel. The >= 0.5 ha event floor that the rest of the page uses "
                    f"keeps {keep:.0%} of those hectares — stated rather than netted out",
            "comparator": f"{spec['dataset']} {spec['version']} raster analysis over the same "
                          "province geometry",
            "errors": fails, "rows": _nan_safe(df.to_dict("records"))}


def gate_h3(linked: pd.DataFrame) -> dict:
    big = linked.loc[(linked.ha >= 5.0) & (linked.hi_share >= 0.5)]
    if not len(big):
        return {"status": "pending", "reason": "no high-confidence clusters >= 5 ha"}
    share = float(big.glad_agree.mean())
    return {"status": "pass" if share >= config.GATE_GLAD_AGREEMENT else "fail",
            "threshold": config.GATE_GLAD_AGREEMENT, "agreement_share": share,
            "n_clusters": int(len(big)), "window_days": config.GLAD_AGREEMENT_DAYS,
            "note": "GLAD-L is optical and 30 m; under persistent cloud it legitimately misses "
                    "what radar sees, so a shortfall is evidence about sensors, not a bug"}


def gate_h4(linked: pd.DataFrame) -> dict:
    riau = linked.loc[linked.province == "Riau"]
    if not len(riau):
        return {"status": "pending", "reason": "no Riau clusters"}
    ha = riau.ha.sum()
    linked_ha = riau.loc[riau.linked].ha.sum()
    share = float(linked_ha / ha) if ha else float("nan")
    return {"status": "pass" if share >= config.GATE_LINK_MIN_SHARE else "fail",
            "floor": config.GATE_LINK_MIN_SHARE, "riau_linked_share": share,
            "riau_alert_ha": float(ha),
            "by_class": _nan_safe((riau.groupby("link_class").ha.sum() / ha).to_dict()),
            "note": "literature floor: Gaveau et al. 2022 put ~32 % of 2001-19 Indonesian "
                    "forest loss as direct conversion to oil palm"}


def divergence(ours: pd.DataFrame) -> dict:
    nat = ours.groupby("year").loss_ha.sum()
    return {"gfw_tree_cover_loss_ha": {int(y): float(nat.get(y, np.nan)) for y in (2023, 2024)},
            "gfw_published_ha": config.GFW_IDN_TCL_HA,
            "klhk_ha": config.KLHK_DEFORESTATION_HA,
            "auriga_2024_ha": config.AURIGA_2024_HA,
            "explanation":
                "These numbers disagree because they measure different things, and all of them "
                "are correct. GFW's 'tree cover loss' is gross removal of >=5 m tree canopy at "
                ">=30 % density, from satellite, and counts an oil-palm estate being replanted "
                "and a pulpwood block being harvested exactly as it counts primary forest being "
                "cleared. KLHK's 'deforestation' is a change in legal-and-biophysical forest "
                "cover between two annual land-cover maps, excludes planted forest and harvest "
                "inside production concessions, and is reported net of reforestation. Neither "
                "is a corrected version of the other; a number is only meaningful with its "
                "definition attached."}


def main(argv: list[str]) -> None:
    prov = gpd.read_parquet(config.BOUNDARIES)
    linked = pd.read_parquet(config.LINKED)
    ours = (pd.read_parquet(config.LOSS_TABLE) if config.LOSS_TABLE.exists()
            else pd.DataFrame(columns=["province", "year", "loss_ha"]))
    manifest = json.loads(config.MANIFEST.read_text()) if config.MANIFEST.exists() else {}

    stats: dict = {
        "generated": date.today().isoformat(),
        "vintages": {k: f"{v['dataset']} {v['version']}" for k, v in config.RASTERS.items()}
        | {"mills": f"{config.MILLS['dataset']} {config.MILLS['version']}"},
        # boundary licence comes from the manifest: ingest falls back to geoBoundaries when the
        # COD-AB geodatabase does not expose a readable ADM1 layer, and the page must say which.
        "licences": {k: v for k, v in config.LICENCES.items() if k != "hdx_cod_ab_idn"}
        | {(manifest.get("layers", {}).get("adm1", {}).get("source") or "administrative boundaries"):
           manifest.get("layers", {}).get("adm1", {}).get("licence",
                                                          config.LICENCES["hdx_cod_ab_idn"])},
        "rejected_sources": config.REJECTED,
        "citations": {k: v["cite"] for k, v in config.RASTERS.items()}
        | {"mills": config.MILLS["cite"]},
        "clusters": {
            "n": int(len(linked)),
            "ha": float(linked.ha.sum()),
            "first_date": str(pd.to_datetime(linked.first_date).min())[:10],
            "last_date": str(pd.to_datetime(linked.last_date).max())[:10],
            "min_cluster_ha": config.MIN_CLUSTER_HA,
        },
        "linkage": {
            "by_class_ha": _nan_safe(linked.groupby("link_class").ha.sum().to_dict()),
            "by_class_share": _nan_safe(
                (linked.groupby("link_class").ha.sum() / linked.ha.sum()).to_dict()),
            "linked_share": float(linked.loc[linked.linked].ha.sum() / linked.ha.sum()),
            "peat_share": float(linked.loc[linked.on_peat].ha.sum() / linked.ha.sum()),
            "primary_share": float(linked.loc[linked.in_primary].ha.sum() / linked.ha.sum()),
        },
        # Measured, not assumed: RADD's detection domain in Indonesia IS the UMD 2001 primary
        # humid-tropical-forest mask.  Across every tile, 100 % of alert pixels fall inside a
        # mask that covers only 6-28 % of the land, and provinces with no primary forest left
        # (Java, Nusa Tenggara) produce no alerts at all.  Two consequences the page states
        # rather than hides: the in-primary flag carries no information, and the palm-internal
        # share is structurally suppressed, because an estate that was already plantation in
        # 2001 is outside the domain and can never raise a RADD alert.
        "radd_domain": {
            "primary_share_of_alert_ha": float(
                linked.loc[linked.in_primary].ha.sum() / linked.ha.sum()),
            "structural": bool(
                linked.loc[linked.in_primary].ha.sum() / linked.ha.sum() > 0.99),
            "note": "RADD alerts are only issued inside the 2001 primary humid tropical forest "
                    "mask, so the in-primary flag is a property of the sensor's baseline, not a "
                    "finding about this year's clearing. It also means PALM-INTERNAL under-counts "
                    "replanting: an estate that was already plantation in 2001 lies outside RADD's "
                    "domain entirely. The palm-linked share published here is therefore a floor.",
        },
        "divergence": divergence(ours),
    }
    log("G-H3 / G-H4 (local)")
    stats["gates"] = {"G-H3": gate_h3(linked), "G-H4": gate_h4(linked)}
    if "--local-only" not in argv:
        log("G-H2 (GFW API)")
        stats["gates"]["G-H2"] = gate_h2(prov, linked)
        log("G-H1 (GFW API, this is the slow one)")
        stats["gates"]["G-H1"] = (gate_h1(prov, ours) if len(ours) else
                                  {"status": "pending", "reason": "loss table not built"})
    stats["manifest"] = {k: {kk: vv for kk, vv in v.items() if kk != "tiles"}
                         for k, v in manifest.get("layers", {}).items()}
    config.STATS_JSON.write_text(json.dumps(_nan_safe(stats), indent=1))
    for name, g in stats["gates"].items():
        log(f"{name}: {g.get('status', '?').upper()}")
    log("wrote", config.STATS_JSON)


if __name__ == "__main__":
    main(sys.argv[1:])
