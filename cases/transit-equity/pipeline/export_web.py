"""Stage 8 · export — NaN-safe view-models for the Astro app (json.dumps(allow_nan=False)).

  web/public/data/adm4.geojson    1,511 kelurahan, simplified, with their access values baked in
  web/public/data/access.json     per kelurahan × scenario × cutoff metrics + names + centroid
  web/public/data/cells.json      the destination lattice (lat/lon, jobs proxy, population)
  web/public/data/isochrones.json per kelurahan: which cells are reachable, and in how long
  web/public/data/network.json    TransJakarta corridor / Mikrotrans / rail geometries by mode
  web/public/data/equity.json     Lorenz, Gini, Palma, scenario deltas, winners and losers
  web/public/data/stats.json      gates, inputs, vintages, the hand-encoded rail table
"""

from __future__ import annotations

import json
import shutil
import zipfile

import numpy as np
import pandas as pd

import config
import ingest
import matrix
import points
import util
from util import log

OUT = config.WEB_DATA
SCEN_ORDER = ["all", "no_rail", "walk"]


def _write(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    p.write_text(json.dumps(obj, allow_nan=False, separators=(",", ":")))
    log(f"  {name}: {p.stat().st_size/1e6:.2f} MB")


def _clean(x, nd=4):
    if x is None:
        return None
    x = float(x)
    return None if not np.isfinite(x) else round(x, nd)


def geojson(acc: pd.DataFrame) -> list[str]:
    import geopandas as gpd
    g = gpd.read_parquet(ingest.ADM4)
    g = g.to_crs(4326)
    simp = g.geometry.simplify(0.0006).buffer(0)
    ok = simp.notna() & ~simp.is_empty & simp.geom_type.isin(["Polygon", "MultiPolygon"])
    g["geometry"] = simp.where(ok, g.geometry)          # keep the original if simplification degenerates
    g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    order = list(g["adm4_pcode"])
    feats = []
    for _, r in g.iterrows():
        geom = r.geometry.__geo_interface__
        def rnd(c):
            return [[[round(x, 5), round(y, 5)] for x, y in ring] for ring in c]
        if geom["type"] == "Polygon":
            geom = {"type": "Polygon", "coordinates": rnd(geom["coordinates"])}
        else:
            geom = {"type": "MultiPolygon", "coordinates": [rnd(p) for p in geom["coordinates"]]}
        feats.append({"type": "Feature", "geometry": geom,
                      "properties": {"id": r["adm4_pcode"], "n": r["adm4_name"],
                                     "k": r["adm3_name"], "r": r["adm2_name"]}})
    _write("adm4.geojson", {"type": "FeatureCollection", "features": feats})
    return order


def access_json(acc: pd.DataFrame, order: list[str]) -> None:
    idx = {p: i for i, p in enumerate(order)}
    meta = acc.drop_duplicates("id").set_index("id")
    rows = {}
    for pid in order:
        if pid not in meta.index:
            continue
        m = meta.loc[pid]
        rows[pid] = {"n": m["adm4_name"], "k": m["adm3_name"], "r": m["adm2_name"],
                     "dki": bool("jakarta" in str(m["adm1_name"]).lower()),
                     "pop": _clean(m["pop"], 0), "lat": _clean(m["lat"], 5), "lon": _clean(m["lon"], 5)}
    for s in SCEN_ORDER:
        a = acc[acc.scenario == s]
        if a.empty:
            continue
        for c in config.CUTOFFS_MIN:
            sub = a[a.cutoff == c].set_index("id")
            for pid in rows:
                if pid in sub.index:
                    r = sub.loc[pid]
                    # Six decimals, not four: the population-weighted median of jobs_share is
                    # 0.0021, so rounding at 1e-4 published 217 kelurahan — 3.3 million people
                    # — as exactly zero when they are not, and the map painted them as though
                    # they reached nothing at all.
                    rows[pid].setdefault(s, {})[str(c)] = [
                        _clean(r["jobs_share"], 6), int(r["hospitals"]), _clean(r["pop_share"], 6),
                        int(r["clinics"])]
        ex = a.drop_duplicates("id").set_index("id")
        for pid in rows:
            if pid in ex.index:
                rows[pid].setdefault(s + "_x", {})["nh"] = _clean(ex.loc[pid, "nearest_hosp_min"], 1)
                rows[pid][s + "_x"]["g"] = _clean(ex.loc[pid, "gravity_share"], 6)
    _write("access.json", {"order": order, "index": idx, "cutoffs": list(config.CUTOFFS_MIN),
                           "scenarios": [s for s in SCEN_ORDER if (acc.scenario == s).any()],
                           "rows": rows,
                           "window": {"date": config.DEPARTURE_DATE,
                                      "from": config.DEPARTURE_WINDOW[0],
                                      "to": config.DEPARTURE_WINDOW[1]}})


def cells_json() -> pd.DataFrame:
    d = points.build_destinations()
    df = pd.DataFrame(d.drop(columns="geometry"))
    _write("cells.json", {"cell_m": config.DEST_GRID_M,
                          "lat": [round(float(v), 4) for v in df["lat"]],
                          "lon": [round(float(v), 4) for v in df["lon"]],
                          "jobs": [int(round(float(v))) for v in df["nres_m2"]],
                          "pop": [int(round(float(v))) for v in df["pop"]],
                          "hosp": [int(v) for v in df["hospital"]],
                          "clinic": [int(v) for v in df["clinic"]]})
    return df


def isochrones(order: list[str]) -> None:
    out = {}
    for s in ("all", "no_rail"):
        try:
            m = matrix.load(s)
        except FileNotFoundError:
            continue
        m = m[m.tt <= max(config.CUTOFFS_MIN)]
        for pid, grp in m.groupby("from_id"):
            grp = grp.sort_values("tt")
            out.setdefault(str(pid), {})[s] = {"i": [int(v) for v in grp.to_id],
                                               "t": [int(v) for v in grp.tt]}
    _write("isochrones.json", out)


def hero_json(acc: pd.DataFrame) -> None:
    """A handful of featured origins with their isochrones, so the hero animates on first paint
    without waiting for the full isochrone file."""
    a = acc[(acc.scenario == "all") & (acc.cutoff == 60)]
    feat: list[str] = []
    for needle in ("Menteng", "Bekasi", "Bogor", "Tangerang"):
        sel = a[a.adm3_name.str.contains(needle, case=False, na=False)]
        if len(sel):
            feat.append(str(sel.nlargest(1, "pop").iloc[0]["id"]))
    feat += [str(x) for x in a.nlargest(3, "jobs_share")["id"]]
    feat += [str(x) for x in a[a["pop"] > 20000].nsmallest(2, "jobs_share")["id"]]
    feat = list(dict.fromkeys(feat))
    m = matrix.load("all")
    m = m[m.from_id.isin(feat) & (m.tt <= max(config.CUTOFFS_MIN))]
    origins = {}
    for pid, grp in m.groupby("from_id"):
        grp = grp.sort_values("tt")
        origins[str(pid)] = {"i": [int(v) for v in grp.to_id], "t": [int(v) for v in grp.tt]}
    default = max(origins, key=lambda k: len(origins[k]["i"])) if origins else None
    _write("hero.json", {"default": default, "featured": feat, "origins": origins})


def network_json() -> None:
    from shapely.geometry import LineString
    lines = []
    z = config.GTFS_DIR / "transjakarta.zip"
    if z.exists():
        with zipfile.ZipFile(z) as zf:
            shapes = pd.read_csv(zf.open("shapes.txt"))
            trips = pd.read_csv(zf.open("trips.txt"))
            routes = pd.read_csv(zf.open("routes.txt"))
        first = trips.dropna(subset=["shape_id"]).drop_duplicates("route_id")
        rmeta = routes.set_index("route_id")
        for _, t in first.iterrows():
            s = shapes[shapes.shape_id == t.shape_id].sort_values("shape_pt_sequence")
            if len(s) < 2:
                continue
            ls = LineString(zip(s.shape_pt_lon, s.shape_pt_lat)).simplify(0.0008)
            rid = str(t.route_id)
            name = str(rmeta.loc[t.route_id, "route_long_name"]) if t.route_id in rmeta.index else rid
            mode = "mikrotrans" if rid.upper().startswith("JAK") else "brt"
            lines.append({"m": mode, "id": rid, "n": name[:60],
                          "c": [[round(x, 5), round(y, 5)] for x, y in ls.coords]})
    if config.RAIL_GTFS.exists():
        with zipfile.ZipFile(config.RAIL_GTFS) as zf:
            stops = pd.read_csv(zf.open("stops.txt"))
            st = pd.read_csv(zf.open("stop_times.txt"))
            trips = pd.read_csv(zf.open("trips.txt"))
        smap = stops.set_index("stop_id")
        for rid, grp in trips[trips.direction_id == 0].groupby("route_id"):
            tid = grp.trip_id.iloc[0]
            seq = st[st.trip_id == tid].sort_values("stop_sequence")
            pts = [[round(float(smap.loc[s, "stop_lon"]), 5), round(float(smap.loc[s, "stop_lat"]), 5)]
                   for s in seq.stop_id if s in smap.index]
            key = str(rid).replace("rail_", "")
            mode = "mrt" if key.startswith("mrt") else "lrt" if key.startswith("lrt") else "krl"
            lines.append({"m": mode, "id": key, "n": key.replace("_", " ").title(), "c": pts,
                          "stations": [{"n": str(smap.loc[s, "stop_name"]),
                                        "c": [round(float(smap.loc[s, "stop_lon"]), 5),
                                              round(float(smap.loc[s, "stop_lat"]), 5)]}
                                       for s in seq.stop_id if s in smap.index],
                          "handencoded": True})
    _write("network.json", {"lines": lines,
                            "modes": {"brt": "#E4562E", "mikrotrans": "#8C6A5D", "krl": "#3E8EDE",
                                      "mrt": "#57B26A", "lrt": "#E0A63F"}})


def main() -> None:
    util.guard_disk()
    acc = pd.read_parquet(config.ACCESS_ADM4)
    OUT.mkdir(parents=True, exist_ok=True)
    order = geojson(acc)
    access_json(acc, order)
    cells_json()
    hero_json(acc)
    isochrones(order)
    network_json()
    for src, name in ((config.EQUITY_JSON, "equity.json"), (config.STATS_JSON, "stats.json")):
        if src.exists():
            shutil.copyfile(src, OUT / name)
            log(f"  {name}: {(OUT / name).stat().st_size/1e6:.2f} MB")
    log("export →", OUT)


if __name__ == "__main__":
    main()
