"""Origins and destinations for the travel-time matrix (shared by matrix / access / export).

origins       ~1,500 Jabodetabek kelurahan (COD-AB ADM4), population-weighted centroids from
              the GHS-POP 100 m surface, snapped back inside the polygon when the weighted
              centroid falls outside it.
destinations  a 1 km lattice over the region (SPEC DEVIATION: the spec says 500 m ≈ 12,000
              cells; 1 km ≈ 5,000 cells keeps the matrix inside the 2.5 G heap we are allowed
              — logged in the README), each cell carrying the jobs proxy (GHS-BUILT-S NRES
              non-residential floorspace, m²), population, and the count of hospitals and
              clinics/puskesmas inside it. One destination set answers every access question.

Outputs: data/origins.parquet, data/destinations.parquet.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config
import ingest
import util
from util import log

ORIGINS = config.DATA_DIR / "origins.parquet"
DESTINATIONS = config.DATA_DIR / "destinations.parquet"
MOLLWEIDE = "ESRI:54009"


def _adm4():
    import geopandas as gpd
    return gpd.read_parquet(ingest.ADM4)


def build_origins():
    import geopandas as gpd
    from shapely.geometry import Point

    if ORIGINS.exists():
        return gpd.read_parquet(ORIGINS)
    g = _adm4()
    pop, cx, cy = util.zonal(ingest.GHS_POP_TIF, g, want_centroid=True)
    nres, _, _ = util.zonal(ingest.GHS_NRES_TIF, g)
    pts = gpd.GeoSeries([Point(x, y) for x, y in zip(cx, cy)], crs=MOLLWEIDE).to_crs(4326)
    poly = g.geometry.reset_index(drop=True)
    inside = pts.within(poly)
    rep = poly.representative_point()
    geom = [p if ok and np.isfinite(p.x) else r for p, ok, r in zip(pts, inside, rep)]
    out = gpd.GeoDataFrame({
        "id": g["adm4_pcode"].values,
        "adm4_name": g["adm4_name"].values, "adm3_name": g["adm3_name"].values,
        "adm2_name": g["adm2_name"].values, "adm1_name": g["adm1_name"].values,
        "pop": np.round(pop, 1), "nres_m2": np.round(nres, 1),
    }, geometry=geom, crs=4326)
    out["lat"] = out.geometry.y
    out["lon"] = out.geometry.x
    out.to_parquet(ORIGINS)
    log(f"origins: {len(out)} kelurahan, pop {out['pop'].sum()/1e6:.2f} M, "
        f"{int(inside.sum())} weighted centroids inside their own polygon")
    return out


def _facilities(region):
    """Health facilities inside the region, classified hospital vs clinic/puskesmas."""
    import geopandas as gpd
    src = "HDX healthsites (ODbL)"
    try:
        f = gpd.read_file(ingest.HEALTHSITES, bbox=config.BBOX, engine="pyogrio")
    except Exception as e:
        log("healthsites unreadable, falling back to OSM clip:", e)
        f = gpd.read_file(ingest.OSM_HEALTH, engine="pyogrio")
        src = "OSM amenity=hospital|clinic|doctors (ODbL)"
    f = f[f.geometry.notna()].copy()
    f["geometry"] = f.to_crs(MOLLWEIDE).geometry.centroid.to_crs(4326)   # ways are polygons
    f = f.set_crs(4326, allow_override=True)

    def col(*needles, exclude=()):
        """The healthsites export ships HXL-tagged headers ('#loc+amenity', '#loc +name')."""
        for n in needles:
            for c in f.columns:
                lc = c.lower()
                if n in lc and not any(x in lc for x in exclude):
                    return f[c].astype("string").fillna("").str.lower()
        return pd.Series("", index=f.index, dtype="string")

    amen = col("amenity", exclude=("health_amenity",))
    care = col("healthcare")
    name = col("name", exclude=("housenumber", "amenity_type"))
    is_hosp = (amen == "hospital") | (care == "hospital") | \
        name.str.contains(r"rumah sakit|^rs |hospital", regex=True, na=False)
    is_clinic = amen.isin(["clinic", "doctors"]) | care.isin(["clinic", "centre", "doctor"]) | \
        name.str.contains(r"puskesmas|klinik", regex=True, na=False)
    f = f[is_hosp | is_clinic].copy()
    f["kind"] = np.where(is_hosp[is_hosp | is_clinic], "hospital", "clinic")
    f["name"] = name[is_hosp | is_clinic]
    f = gpd.sjoin(f[["kind", "name", "geometry"]], region[["geometry"]], predicate="within", how="inner")
    log(f"facilities: {len(f)} in region ({int((f.kind=='hospital').sum())} hospitals) from {src}")
    return f.drop(columns=[c for c in f.columns if c.startswith("index_")]), src


def build_destinations():
    import geopandas as gpd
    from shapely.geometry import Point

    if DESTINATIONS.exists():
        return gpd.read_parquet(DESTINATIONS)
    g = _adm4()
    cell = config.DEST_GRID_M
    gx, gy, nres, nx_, ny_ = util.grid_sums(ingest.GHS_NRES_TIF, g, cell)
    gx2, gy2, pop, px_, py_ = util.grid_sums(ingest.GHS_POP_TIF, g, cell)
    d = pd.DataFrame({"x": gx, "y": gy, "nres_m2": nres, "nx": nx_, "ny": ny_}).merge(
        pd.DataFrame({"x": gx2, "y": gy2, "pop": pop, "px": px_, "py": py_}),
        on=["x", "y"], how="outer").fillna(0.0)
    # route to where people (or, failing that, workplaces) actually are inside the cell
    d["rx"] = np.where(d["pop"] > 0, d["px"], np.where(d["nres_m2"] > 0, d["nx"], d["x"]))
    d["ry"] = np.where(d["pop"] > 0, d["py"], np.where(d["nres_m2"] > 0, d["ny"], d["y"]))
    facs, fac_src = _facilities(g)
    fp = facs.to_crs(MOLLWEIDE)
    fx = (np.floor(fp.geometry.x / cell) + 0.5) * cell
    fy = (np.floor(fp.geometry.y / cell) + 0.5) * cell
    fac = pd.DataFrame({"x": fx.values, "y": fy.values, "kind": facs["kind"].values})
    cnt = fac.pivot_table(index=["x", "y"], columns="kind", aggfunc="size", fill_value=0).reset_index()
    for k in ("hospital", "clinic"):
        if k not in cnt.columns:
            cnt[k] = 0
    d = d.merge(cnt[["x", "y", "hospital", "clinic"]], on=["x", "y"], how="outer").fillna(0.0)

    total_nres, total_pop = d["nres_m2"].sum(), d["pop"].sum()
    keep = (d["pop"] >= 50) | (d["nres_m2"] >= 2500) | (d["hospital"] > 0) | (d["clinic"] > 0)
    d = d[keep].copy()
    gdf = gpd.GeoDataFrame(d, geometry=[Point(x, y) for x, y in zip(d.rx, d.ry)], crs=MOLLWEIDE).to_crs(4326)
    gdf = gpd.sjoin(gdf, g[["adm4_pcode", "geometry"]], predicate="within", how="left")
    gdf = gdf[gdf["adm4_pcode"].notna()].copy()          # drop sea / outside-region cells
    gdf = gdf.drop(columns=[c for c in gdf.columns if c.startswith("index_")])
    gdf["id"] = np.arange(len(gdf), dtype="int32")
    gdf["lat"] = gdf.geometry.y
    gdf["lon"] = gdf.geometry.x
    gdf.to_parquet(DESTINATIONS)
    log(f"destinations: {len(gdf)} cells of {cell} m — "
        f"{gdf['nres_m2'].sum()/total_nres:.1%} of the region's jobs proxy, "
        f"{gdf['pop'].sum()/total_pop:.1%} of its population, "
        f"{int(gdf['hospital'].sum())} hospitals, {int(gdf['clinic'].sum())} clinics")
    util.manifest_put("destinations", cell_m=cell, cells=int(len(gdf)),
                      jobs_proxy_covered=float(gdf["nres_m2"].sum() / max(total_nres, 1)),
                      pop_covered=float(gdf["pop"].sum() / max(total_pop, 1)),
                      facility_source=fac_src)
    return gdf


def main() -> None:
    build_origins()
    build_destinations()


if __name__ == "__main__":
    main()
