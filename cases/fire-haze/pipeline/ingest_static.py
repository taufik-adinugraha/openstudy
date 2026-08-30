"""Stage 2 · static — the fuel, and the map you attribute onto.

Three things that never change day to day, reduced once to the 0.25 deg model grid:

PEAT DEPTH — the fuel variable that matters most.
A surface fire on mineral soil in Riau burns for days.  A fire in drained peat burns for months,
downward, and is what actually produces the haze that closes schools in Singapore, because
smouldering combustion at low temperature emits far more particulate per unit of carbon than
flaming combustion does.  Depth is therefore not a nice-to-have covariate; it is close to the
whole story of why Indonesian fire is a transboundary problem and Australian fire is not.
PEATGRIDS (Widyastuti et al. 2025, CATENA; Zenodo 10.5281/zenodo.12559239) is 1 km global peat
thickness in metres, CC BY 4.0 and peer-reviewed.  The honest finding recorded in the case README
is that NO open, commercially-licensed, national, high-resolution Indonesian peat-depth raster
exists — BIG's Satu Peta carries KLHK's 1:50,000 map with a real ``peat_thick`` field, and it is
"License not specified", so it is a view-time reference only and is never stored here.

LAND COVER — ESA WorldCover v200 (2021), CC BY 4.0, 10 m, anonymous S3, a genuine COG.
Read through GDAL's /vsicurl with an OVERVIEW, not downloaded: the 38 tiles covering the AOI are
0.76 GiB at full resolution and the model grid is 0.25 deg, so full resolution would be
downloaded, decoded and then thrown away.  Reading overview level 5 range-reads a few megabytes
per tile and still gives ~5,900 samples per model cell, which is far more than a class fraction
needs.  Do NOT diff v100 against v200: different algorithm versions, so the difference is method,
not land cover.

BOUNDARIES — geoBoundaries ADM1, which is the attribution unit.
This case publishes PROVINCE-level attribution (the editorial decision is argued in the README).
The province of a 0.25 deg cell is decided once here, by point-in-polygon on the cell CENTRE, and
that single lookup is then used by the fire table, the risk surface and the trajectory
attribution alike — so the three can never disagree about which province a cell is in.

OUTPUT
------
``data/cell_static.parquet``  cell, clat, clon, adm1_name, adm1_iso, country, peat_m,
                              peat_frac, lc_<class>_frac, is_land
``data/adm1.parquet``         the ADM1 geometries (simplified) for the map
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import config
import util
from util import log

RAW_STATIC = config.RAW / "static"
CELL_OUT = config.DATA_DIR / "cell_static.parquet"
ADM1_OUT = config.DATA_DIR / "adm1.parquet"

GB_API = "https://www.geoboundaries.org/api/current/gbOpen/{iso}/ADM1/"
GB_COUNTRIES = ("IDN", "MYS", "SGP", "BRN")

# WorldCover v200 classes (the map's own legend)
WC_CLASSES = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built",
              60: "bare", 70: "snow", 80: "water", 90: "wetland", 95: "mangrove",
              100: "moss"}
# The three that carry fire risk in this landscape, kept as named columns in the panel:
#   crop     — the clearing that is actually lit
#   wetland  — the drained peat swamp, where a fire becomes a haze episode
#   tree     — what is left to lose
WC_OVERVIEW = 5          # 2**5 = 32x decimation: ~320 m, ~5,900 samples per 0.25 deg cell


def cell_grid():
    """Every 0.25 deg cell centre in the AOI, as a DataFrame."""
    import numpy as np
    import pandas as pd
    w, s, e, n = config.AOI
    g = config.GRID_DEG
    lats = np.round(np.arange(np.ceil(s / g) * g, n + 1e-9, g), 3)
    lons = np.round(np.arange(np.ceil(w / g) * g, e + 1e-9, g), 3)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    df = pd.DataFrame({"clat": la.ravel().astype("float32"),
                       "clon": lo.ravel().astype("float32")})
    df["cell"] = util.cell_key(df["clat"], df["clon"])
    return df


# ── boundaries ────────────────────────────────────────────────────────────────────────
def fetch_adm1():
    """geoBoundaries ADM1 for the four countries the plume can reach.

    Reused from the sibling cases' pattern rather than re-derived: geoBoundaries gbOpen is
    CC BY 4.0 and its ADM1 for Indonesia already carries the post-2020 Papua split, so the
    name-based recode the poverty case needs at ADM2 is not needed at ADM1.
    """
    import geopandas as gpd
    import pandas as pd
    import requests
    if ADM1_OUT.exists():
        return gpd.read_parquet(ADM1_OUT)
    RAW_STATIC.mkdir(parents=True, exist_ok=True)
    frames = []
    for iso in GB_COUNTRIES:
        dest = RAW_STATIC / f"adm1_{iso}.geojson"
        if not dest.exists():
            meta = requests.get(GB_API.format(iso=iso), timeout=120,
                                headers=util.browser_ua()).json()
            url = meta.get("gjDownloadURL") or meta.get("simplifiedGeometryGeoJSON")
            if not url:
                log(f"  adm1 {iso}: no download URL in the geoBoundaries record — skipped")
                continue
            if util.fetch(url, dest, headers=util.browser_ua(), min_bytes=1000) is None:
                continue
        g = gpd.read_file(dest)
        g["country"] = iso
        frames.append(g[["shapeName", "shapeISO", "country", "geometry"]])
        log(f"  adm1 {iso}: {len(g)} units")
    util.require(bool(frames), "geoBoundaries ADM1 unavailable for every country")
    adm = pd.concat(frames, ignore_index=True)
    adm = gpd.GeoDataFrame(adm, geometry="geometry", crs="EPSG:4326")
    adm["geometry"] = adm.geometry.simplify(0.005).buffer(0)
    adm = adm.rename(columns={"shapeName": "adm1_name", "shapeISO": "adm1_iso"})
    adm.to_parquet(ADM1_OUT)
    return adm


def assign_province(cells, adm):
    """Point-in-polygon on the cell CENTRE, once, for the whole pipeline.

    A cell centre in the sea gets ``is_land = False`` and no province — it is kept in the grid so
    the trajectory engine has somewhere to put a parcel over water, but it never carries risk.
    """
    import geopandas as gpd
    pts = gpd.GeoDataFrame(cells.copy(),
                           geometry=gpd.points_from_xy(cells["clon"], cells["clat"]),
                           crs="EPSG:4326")
    j = gpd.sjoin(pts, adm[["adm1_name", "adm1_iso", "country", "geometry"]],
                  how="left", predicate="within")
    j = j.drop(columns=["geometry", "index_right"]).drop_duplicates("cell")
    j["is_land"] = j["adm1_name"].notna()
    log(f"  provinces: {int(j['is_land'].sum()):,} land cells of {len(j):,}; "
        f"{j['adm1_name'].nunique()} ADM1 units touched")
    return j


# ── peat ──────────────────────────────────────────────────────────────────────────────
def fetch_peatgrids() -> Path | None:
    """PEATGRIDS thickness raster from Zenodo (CC BY 4.0).

    Zenodo returns 403 to some egress IPs — an anti-bot rule, not a licence gate — so the request
    carries a browser UA and the failure is recorded rather than fatal.
    """
    import requests
    dest = RAW_STATIC / "global_peatThickness_v1.tif"
    if dest.exists():
        return dest
    RAW_STATIC.mkdir(parents=True, exist_ok=True)
    rec = requests.get(f"https://zenodo.org/api/records/{config.PEATGRIDS['doi'].split('.')[-1]}",
                       timeout=120, headers=util.browser_ua())
    if rec.status_code != 200:
        log(f"  peatgrids: Zenodo record {rec.status_code}")
        return None
    for f in rec.json().get("files", []):
        if "thickness" in f["key"].lower():
            return util.fetch(f["links"]["self"], dest, headers=util.browser_ua(),
                              min_bytes=1_000_000, timeout=900)
    log("  peatgrids: no thickness file in the record")
    return None


def peat_to_grid(cells):
    """Mean peat thickness (m) and peat fraction per 0.25 deg cell."""
    import numpy as np
    import pandas as pd
    import rasterio
    from rasterio.windows import from_bounds
    p = fetch_peatgrids()
    if p is None:
        cells["peat_m"] = np.nan
        cells["peat_frac"] = np.nan
        return cells
    w, s, e, n = config.AOI
    with rasterio.open(p) as src:
        win = from_bounds(w, s, e, n, src.transform)
        arr = src.read(1, window=win, masked=True).astype("float32")
        tr = src.window_transform(win)
        nod = src.nodata
    if nod is not None:
        arr = np.ma.masked_equal(arr, nod)
    arr = np.ma.masked_less(arr, 0)
    h, wd = arr.shape
    rows, colsx = np.mgrid[0:h, 0:wd]
    lon = tr.c + (colsx + 0.5) * tr.a
    lat = tr.f + (rows + 0.5) * tr.e
    clat, clon = util.snap_cell(lat, lon)
    key = util.cell_key(clat, clon)
    d = pd.DataFrame({"cell": key.ravel(),
                      "v": arr.filled(np.nan).ravel()})
    d = d[np.isfinite(d["v"])]
    g = d.groupby("cell").agg(peat_m=("v", "mean"),
                              peat_frac=("v", lambda x: float((x > 0.05).mean()))).reset_index()
    log(f"  peat: {len(g):,} cells with PEATGRIDS coverage; "
        f"max depth {g['peat_m'].max():.2f} m")
    return cells.merge(g, on="cell", how="left")


# ── land cover ────────────────────────────────────────────────────────────────────────
def worldcover_tiles() -> list[str]:
    """The 3-degree tile names covering the AOI.  7 of 45 are all-ocean and simply 404."""
    w, s, e, n = config.AOI
    out = []
    lat = int((s // 3) * 3)
    while lat < n:
        lon = int((w // 3) * 3)
        while lon < e:
            ns = f"N{lat:02d}" if lat >= 0 else f"S{abs(lat):02d}"
            ew = f"E{lon:03d}" if lon >= 0 else f"W{abs(lon):03d}"
            out.append(f"{ns}{ew}")
            lon += 3
        lat += 3
    return out


def landcover_to_grid(cells):
    """Class fractions per 0.25 deg cell, read from COG overviews over HTTP.

    Never downloads a tile.  ``/vsicurl`` range-reads only the overview blocks it needs, which is
    a few MB against 36 MB per tile at full resolution, and the aggregate is identical at this
    grid size.
    """
    import numpy as np
    import pandas as pd
    import rasterio
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
    os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "3")
    os.environ.setdefault("VSI_CACHE", "TRUE")

    counts: dict[tuple[int, int], int] = {}
    tiles = worldcover_tiles()
    ok = 0
    for tile in tiles:
        url = "/vsicurl/" + config.WORLDCOVER_URL.format(tile=tile)
        try:
            with rasterio.open(url) as src:
                ov = src.overviews(1)
                factor = ov[min(WC_OVERVIEW, len(ov)) - 1] if ov else 1
                oh, ow = src.height // factor, src.width // factor
                arr = src.read(1, out_shape=(oh, ow))
                tr = src.transform * src.transform.scale(src.width / ow, src.height / oh)
        except Exception as exc:                            # noqa: BLE001 — ocean tiles 404
            log(f"  worldcover {tile}: {type(exc).__name__} (ocean tile or transient)")
            continue
        ok += 1
        rows, colsx = np.mgrid[0:arr.shape[0], 0:arr.shape[1]]
        lon = tr.c + (colsx + 0.5) * tr.a
        lat = tr.f + (rows + 0.5) * tr.e
        clat, clon = util.snap_cell(lat, lon)
        key = util.cell_key(clat, clon).ravel()
        val = arr.ravel()
        keep = val > 0
        for k, v in zip(key[keep], val[keep]):
            counts[(int(k), int(v))] = counts.get((int(k), int(v)), 0) + 1
        log(f"  worldcover {tile}: overview 1/{factor} -> {arr.shape[0]}x{arr.shape[1]}")
    log(f"  worldcover: {ok}/{len(tiles)} tiles read (the rest are ocean and absent)")
    if not counts:
        return cells
    d = pd.DataFrame([(k[0], k[1], v) for k, v in counts.items()],
                     columns=["cell", "cls", "n"])
    tot = d.groupby("cell")["n"].sum().rename("tot")
    d = d.join(tot, on="cell")
    d["frac"] = d["n"] / d["tot"]
    d["name"] = d["cls"].map(WC_CLASSES).fillna("other")
    wide = (d.pivot_table(index="cell", columns="name", values="frac",
                          aggfunc="sum", fill_value=0.0)
              .add_prefix("lc_").reset_index())
    return cells.merge(wide, on="cell", how="left")


def main() -> None:
    import numpy as np
    import pandas as pd
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_STATIC.mkdir(parents=True, exist_ok=True)
    cells = cell_grid()
    log(f"model grid: {len(cells):,} cells at {config.GRID_DEG} deg over {config.AOI}")

    adm = fetch_adm1()
    cells = assign_province(cells, adm)
    cells = peat_to_grid(cells)
    cells = landcover_to_grid(cells)

    for c in cells.columns:
        if cells[c].dtype == np.float64:
            cells[c] = cells[c].astype("float32")
    cells.to_parquet(CELL_OUT, index=False, compression="zstd")
    lc_cols = [c for c in cells.columns if c.startswith("lc_")]
    log(f"cell_static: {len(cells):,} cells, {len(lc_cols)} land-cover classes "
        f"({', '.join(sorted(lc_cols))}) -> {CELL_OUT.name}")
    util.manifest_put("static", cells=int(len(cells)),
                      land_cells=int(cells["is_land"].sum()),
                      peat_cells=int((cells.get("peat_m", pd.Series(dtype=float)) > 0.05).sum()),
                      lc_classes=lc_cols)


if __name__ == "__main__":
    main()
