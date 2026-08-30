"""Stage 2 · aux — boundaries, the analysis cell grid, and the rice reference mask.

BOUNDARIES
----------
geoBoundaries gbOpen ADM1 (ODbL 1.0) and ADM2 (CC BY 3.0 IGO) for Indonesia; kecamatan (ADM3)
from HDX COD-AB, because **gbOpen publishes no Indonesian ADM3 — the API 404s.**  GADM is
rejected outright: non-commercial licence, and this is a commercial demo.

THE ANALYSIS UNIT, DECIDED HERE
-------------------------------
Three units, and they are not interchangeable:

  cell           where phenology is detected — ``config.CELL_M`` metres, in the kabupaten's own
                 UTM zone so that a cell is a square of known area rather than a rectangle whose
                 shape depends on latitude
  kecamatan      the map unit — fine enough that the harvest wave reads as a wave
  kabupaten      the REPORTING and VALIDATION unit, because that is the level at which BPS
                 publishes KSA.  Reporting at kecamatan would imply a precision the benchmark
                 cannot test and gate G-I1 would become unfalsifiable.

THE RICE MASK — A PRIOR, AND DELIBERATELY NOT A FILTER ON THE SAR READ
----------------------------------------------------------------------
The spec has the mask restricting where the SAR is even read.  This build does not do that, and
the change is deliberate: gate G-I4 has to *measure* agreement with Open-SEA-Rice-10, and a
detector that is only ever run inside the mask can only ever agree with it.  Detection therefore
runs on every cell of the six kabupaten and the mask is carried per cell as a covariate — used
for confidence weighting, for the G-I4 confusion matrix, and for the mask-sensitivity interval
in stage 7.  It costs roughly twice the cells; the new route makes that affordable (the SAR is
window-read from remote COG overviews, so the cost of a cell is bytes, not gigabytes).

OUTPUT: data/adm.parquet (ADM1/2/3 with BPS codes, WKB geometry), data/cells.parquet (cell index:
id, kabupaten, kecamatan, UTM x/y, lon/lat, ha, rice-mask class), data/rice_mask_meta.json.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import config
import util
from util import log

ADM = config.DATA_DIR / "adm.parquet"
CELLS = config.DATA_DIR / "cells.parquet"
MASK_META = config.DATA_DIR / "rice_mask_meta.json"


# ── boundaries ────────────────────────────────────────────────────────────────────────
def _norm(name: str) -> str:
    s = str(name).upper()
    for junk in ("KABUPATEN ", "KAB. ", "KAB ", "KOTA ", "CITY OF ", "MUNICIPALITY OF "):
        s = s.replace(junk, "")
    return " ".join(s.replace(".", " ").replace("-", " ").split())


def boundaries():
    """geoBoundaries ADM1/ADM2 + COD-AB ADM3, clipped to Java's rice-bowl provinces."""
    import geopandas as gpd
    import pandas as pd

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = config.RAW / "adm"
    raw.mkdir(parents=True, exist_ok=True)

    frames = []
    for level, url in (("ADM1", config.GB_ADM1_URL), ("ADM2", config.GB_ADM2_URL)):
        dest = raw / f"gb_{level}.geojson"
        util.require(util.fetch(url, dest, min_bytes=10_000) is not None,
                     f"geoBoundaries {level} download failed — {url}")
        g = gpd.read_file(dest)
        g = g.rename(columns={"shapeName": "name", "shapeID": "gb_id"})
        # geoBoundaries names Indonesian provinces in English; BPS names them in Indonesian.
        g["name"] = g["name"].map(lambda s: config.PROVINCE_ALIASES.get(s, s)) \
            if level == "ADM1" else g["name"]
        g["level"] = level
        frames.append(g[["name", "gb_id", "level", "geometry"]])
        log(f"boundaries: {level} {len(g)} units")

    adm1 = frames[0]
    adm2 = frames[1]

    # Which ADM2 belongs to which ADM1 — geoBoundaries does not carry the parent code, so it is
    # resolved by representative-point containment rather than by a name join (two provinces
    # each have a "Bogor"-shaped ambiguity once kota/kabupaten prefixes are stripped).
    rp = adm2.copy()
    rp["geometry"] = rp.representative_point()
    joined = gpd.sjoin(rp, adm1[["name", "geometry"]].rename(columns={"name": "province"}),
                       how="left", predicate="within")
    adm2 = adm2.assign(province=joined["province"].to_numpy())
    adm2["province"] = adm2["province"].fillna("")

    keep1 = adm1[adm1["name"].isin(config.SCOPE_PROVINCES)].copy()
    keep2 = adm2[adm2["province"].isin(config.SCOPE_PROVINCES)].copy()
    util.require(len(keep1) == 3, f"expected 3 scope provinces, matched {len(keep1)}: "
                                  f"{sorted(adm1['name'].unique())[:40]}")
    log(f"boundaries: scope provinces {len(keep1)}, kabupaten in scope {len(keep2)}")

    # BPS codes for the six deep units, asserted by name so a boundary-vintage change is loud.
    keep2["norm"] = keep2["name"].map(_norm)
    keep2["bps"] = pd.NA
    for kab, meta in config.SCOPE_DEEP.items():
        hit = keep2.index[keep2["norm"] == _norm(kab)]
        util.require(len(hit) == 1, f"deep kabupaten {kab!r} matched {len(hit)} ADM2 polygons")
        keep2.loc[hit, "bps"] = meta["bps"]
    keep2["deep"] = keep2["norm"].isin([_norm(k) for k in config.SCOPE_DEEP])

    adm3 = _kecamatan(keep2[keep2["deep"]])

    out = pd.concat([
        keep1.assign(province=keep1["name"], bps=pd.NA, deep=False, norm=keep1["name"].map(_norm)),
        keep2, adm3,
    ], ignore_index=True)
    out = gpd.GeoDataFrame(out, geometry="geometry", crs="EPSG:4326")
    out["wkb"] = out.geometry.apply(lambda g: g.wkb)
    pd.DataFrame(out.drop(columns="geometry")).to_parquet(ADM, index=False)
    log(f"boundaries -> {ADM} ({len(out)} rows: "
        f"{out['level'].value_counts().to_dict()})")
    return out


def _kecamatan(deep_kab):
    """COD-AB ADM3 for the six deep kabupaten only.

    The COD-AB SHP zip is 474 MiB and the GDB 209 MiB; Case H found the GDB exposes no
    ``adm1``-named layer under the current pyogrio, so the SHP zip is the route.  It is
    downloaded once, the ADM3 layer is read straight out of the zip with pyogrio's ``/vsizip/``
    support, the six kabupaten are cut out, and the archive is deleted — nothing 474 MiB stays
    on a disk with a 10 GB floor.
    """
    import geopandas as gpd
    import pandas as pd

    dest = config.RAW / "adm" / "codab_shp.zip"
    if util.fetch(config.COD_AB_SHP_URL, dest, min_bytes=10_000_000, timeout=1800) is None:
        log("kecamatan: COD-AB unavailable — the harvest-wave map falls back to kabupaten")
        return gpd.GeoDataFrame(columns=["name", "gb_id", "level", "geometry", "province",
                                         "bps", "deep", "norm"], geometry="geometry",
                                crs="EPSG:4326")
    try:
        import pyogrio
        names = [n for n, _ in pyogrio.list_layers(f"/vsizip/{dest}")]
        log(f"kecamatan: layers {names}")
        # The layers are named ``idn_admin3``, not ``idn_adm3`` — matching on "adm3" finds
        # nothing and looks exactly like Case H's missing-layer problem, which it is not.
        lyr = next((n for n in names if "admin3" in n.lower() or "adm3" in n.lower()), None)
        util.require(lyr is not None, f"no ADM3 layer in COD-AB: {names}")
        g = gpd.read_file(f"/vsizip/{dest}", layer=lyr)
    except Exception as exc:                                   # noqa: BLE001
        log(f"kecamatan: unreadable ({type(exc).__name__} {exc}) — falling back to kabupaten")
        return gpd.GeoDataFrame(columns=["name", "gb_id", "level", "geometry", "province",
                                         "bps", "deep", "norm"], geometry="geometry",
                                crs="EPSG:4326")

    cols = {c.lower(): c for c in g.columns}
    ncol = cols.get("adm3_en") or cols.get("adm3_pcode") or list(g.columns)[0]
    pcol = cols.get("adm3_pcode") or ncol
    g = g.to_crs(4326)
    kab = deep_kab.to_crs(4326)
    rp = g.copy()
    rp["geometry"] = rp.representative_point()
    hit = gpd.sjoin(rp, kab[["name", "bps", "province", "geometry"]]
                    .rename(columns={"name": "kab", "bps": "kab_bps"}),
                    how="inner", predicate="within")
    g = g.loc[hit.index].copy()
    g["name"] = hit[ncol].to_numpy()
    g["gb_id"] = hit[pcol].to_numpy()
    g["level"] = "ADM3"
    g["province"] = hit["province"].to_numpy()
    g["bps"] = hit["kab_bps"].to_numpy()
    g["deep"] = True
    g["norm"] = g["name"].map(_norm)
    log(f"kecamatan: {len(g)} within the six deep kabupaten")
    dest.unlink(missing_ok=True)          # 474 MiB does not stay on a 10 GB-floor disk
    return gpd.GeoDataFrame(g[["name", "gb_id", "level", "geometry", "province", "bps",
                               "deep", "norm"]], geometry="geometry", crs="EPSG:4326")


# ── the analysis cell grid ────────────────────────────────────────────────────────────
def build_cells():
    """The analysis cell index: id, kabupaten, kecamatan, UTM x/y, lon/lat, hectares.

    Area is geodetic.  A UTM square of 100 m x 100 m is not 1.0000 ha on the ellipsoid — the
    point scale factor runs 0.9996 at the central meridian to about 1.0004 at the zone edge, so
    a naive 1 ha per cell biases the eastern kabupaten against the western ones by roughly a
    tenth of a percent.  Small, systematic, and free to fix: every kabupaten's cells are scaled
    so they sum to the polygon's true geodesic area.
    """
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    from pyproj import Geod
    from shapely import points, wkb

    adm = pd.read_parquet(ADM)
    adm["geometry"] = adm["wkb"].apply(wkb.loads)
    adm = gpd.GeoDataFrame(adm, geometry="geometry", crs="EPSG:4326")
    kabs = adm[(adm.level == "ADM2") & adm.deep]
    kecs = adm[adm.level == "ADM3"]
    geod = Geod(ellps="WGS84")

    rows = []
    for _, kab in kabs.iterrows():
        cen = kab.geometry.centroid
        epsg = util.utm_epsg(cen.x, cen.y)
        gk = gpd.GeoSeries([kab.geometry], crs=4326).to_crs(epsg).iloc[0]
        x0, y0, x1, y1 = gk.bounds
        s = config.CELL_M
        x0, y0 = np.floor(x0 / s) * s, np.floor(y0 / s) * s
        xs = np.arange(x0 + s / 2, x1 + s, s)
        ys = np.arange(y0 + s / 2, y1 + s, s)
        gx, gy = np.meshgrid(xs, ys)
        pts = gpd.GeoSeries(points(np.c_[gx.ravel(), gy.ravel()]), crs=epsg)
        inside = pts.within(gk).to_numpy()
        pts = pts[inside]
        ll = pts.to_crs(4326)
        n = len(pts)
        util.require(n > 1000, f"{kab['name']}: only {n} cells — grid construction failed")

        # geodesic correction (see docstring)
        true_ha = abs(geod.geometry_area_perimeter(kab.geometry)[0]) / 10_000.0
        planar_ha = gk.area / 10_000.0
        cell_ha = (s * s / 10_000.0) * (true_ha / planar_ha)

        df = pd.DataFrame({
            "kabupaten": kab["name"], "kab_bps": int(kab["bps"]), "province": kab["province"],
            "epsg": epsg,
            "x": pts.x.to_numpy().astype("float64"), "y": pts.y.to_numpy().astype("float64"),
            "lon": ll.x.to_numpy().astype("float32"), "lat": ll.y.to_numpy().astype("float32"),
            "ha": np.float32(cell_ha),
        })
        # kecamatan by containment; cells in no kecamatan keep the kabupaten as their map unit
        sub = kecs[kecs["bps"] == kab["bps"]]
        if len(sub):
            j = gpd.sjoin(gpd.GeoDataFrame(df[["lon", "lat"]], geometry=ll.to_numpy(), crs=4326),
                          sub[["name", "gb_id", "geometry"]].rename(
                              columns={"name": "kecamatan", "gb_id": "kec_id"}),
                          how="left", predicate="within")
            j = j[~j.index.duplicated(keep="first")].reindex(df.index)
            df["kecamatan"] = j["kecamatan"].fillna(kab["name"]).to_numpy()
            df["kec_id"] = j["kec_id"].fillna(str(kab["gb_id"])).to_numpy()
        else:
            df["kecamatan"] = kab["name"]
            df["kec_id"] = str(kab["gb_id"])
        df["cell"] = [f"{kab['bps']}-{i}" for i in range(n)]
        rows.append(df)
        log(f"cells: {kab['name']:11s} EPSG:{epsg} n={n:7d} cell={cell_ha:.5f} ha "
            f"total={n * cell_ha / 1000:.1f} kha (polygon {true_ha / 1000:.1f} kha) "
            f"kec={df['kecamatan'].nunique()}")

    out = pd.concat(rows, ignore_index=True)
    out.to_parquet(CELLS, index=False)
    log(f"cells -> {CELLS} ({len(out):,} cells, {out['ha'].sum() / 1000:.1f} kha)")
    return out


# ── the rice prior ────────────────────────────────────────────────────────────────────
def _zenodo_files(doi: str) -> list[dict]:
    """Resolve a Zenodo record's files and its DEPOSIT licence.

    Zenodo answers 504 under load often enough that a single attempt fails a whole run for no
    reason; five tries with a backoff is the difference between a flaky stage and a stable one.
    """
    import time

    import requests

    rec = doi.rsplit(".", 1)[-1]
    for attempt in range(5):
        try:
            r = requests.get(f"https://zenodo.org/api/records/{rec}",
                             headers=util.browser_ua(), timeout=180)
            r.raise_for_status()
            break
        except Exception as exc:                               # noqa: BLE001
            log(f"zenodo {rec}: {type(exc).__name__} {exc} (attempt {attempt + 1}/5)")
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    j = r.json()
    return [{"key": f["key"], "size": f.get("size"),
             "url": f["links"].get("self") or f["links"].get("download")}
            for f in j.get("files", [])], j.get("metadata", {}).get("license", {})


def rice_mask():
    """Open-SEA-Rice-10 onto the cell grid — a prior and a benchmark, never a label.

    Classes 1/2/3 are single/double/triple crop, so the same raster is the extent prior for the
    confidence weighting AND the independent cropping-intensity benchmark for gate G-I3.  The
    licence is read from the Zenodo DEPOSIT, not the paper: the article renders CC BY-NC-ND
    while the deposit is CC BY 4.0, and the deposit is the instrument that grants rights.
    """
    import numpy as np
    import pandas as pd
    import rasterio
    from rasterio.warp import transform as warp_transform

    cells = pd.read_parquet(CELLS)
    spec = config.RICE_MASK[config.RICE_MASK_PRIMARY]
    files, licence = _zenodo_files(spec["doi"])
    log(f"rice mask: Zenodo {spec['doi']} licence={licence} files="
        f"{[(f['key'], round((f['size'] or 0) / 1e6, 1)) for f in files]}")
    want = next((f for f in files if f["key"] == spec["file"]), None) or \
        next((f for f in files if f["key"].lower().endswith(".zip")), None)
    util.require(want is not None, f"no usable file on Zenodo record for {spec['doi']}")

    dest = config.RAW / "mask" / want["key"]
    if util.fetch(want["url"], dest, headers=util.browser_ua(), min_bytes=1_000_000,
                  timeout=1800) is None:
        log("rice mask: download failed — G-I4 will be reported as not evaluated")
        MASK_META.write_text(json.dumps({"available": False, "doi": spec["doi"]}, indent=1))
        return None

    tifs = []
    if dest.suffix.lower() == ".zip":
        with zipfile.ZipFile(dest) as z:
            names = [n for n in z.namelist() if n.lower().endswith((".tif", ".tiff"))]
            log(f"rice mask: {len(names)} rasters in the archive")
            outdir = config.RAW / "mask" / "x"
            outdir.mkdir(parents=True, exist_ok=True)
            for n in names:
                p = outdir / Path(n).name
                if not p.exists():
                    p.write_bytes(z.read(n))
                tifs.append(p)
    else:
        tifs = [dest]

    cls = np.zeros(len(cells), dtype="uint8")
    lon = cells["lon"].to_numpy("float64")
    lat = cells["lat"].to_numpy("float64")
    hit_total = 0
    for p in tifs:
        try:
            with rasterio.open(p) as ds:
                w, s, e, n = ds.bounds if ds.crs.to_epsg() == 4326 else (None,) * 4
                if ds.crs.to_epsg() == 4326:
                    sel = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
                    if not sel.any():
                        continue
                    xs, ys = lon[sel], lat[sel]
                else:
                    xs_, ys_ = warp_transform("EPSG:4326", ds.crs, lon.tolist(), lat.tolist())
                    xs, ys = np.asarray(xs_), np.asarray(ys_)
                    sel = ((xs >= ds.bounds.left) & (xs <= ds.bounds.right) &
                           (ys >= ds.bounds.bottom) & (ys <= ds.bounds.top))
                    if not sel.any():
                        continue
                    xs, ys = xs[sel], ys[sel]
                vals = np.fromiter((v[0] for v in ds.sample(np.c_[xs, ys], 1)),
                                   dtype="float32", count=int(sel.sum()))
                vals = np.nan_to_num(vals, nan=0).astype("uint8")
                idx = np.flatnonzero(sel)
                cls[idx] = np.maximum(cls[idx], vals)
                hit_total += int((vals > 0).sum())
                log(f"rice mask: {p.name} matched {int(sel.sum()):,} cells, "
                    f"{int((vals > 0).sum()):,} rice")
        except Exception as exc:                               # noqa: BLE001
            log(f"rice mask: {p.name} unreadable ({type(exc).__name__} {exc})")
    cells["mask_class"] = cls
    cells.to_parquet(CELLS, index=False)
    share = float((cls > 0).mean())
    MASK_META.write_text(json.dumps({
        "available": True, "product": "Open-SEA-Rice-10", "doi": spec["doi"],
        "licence_from_deposit": licence, "licence_expected": spec["licence"],
        "year": spec["year"], "res_m": spec["res_m"],
        "cells_total": int(len(cells)), "cells_rice": int((cls > 0).sum()),
        "share_rice": round(share, 4),
        "class_counts": {int(k): int(v) for k, v in
                         zip(*np.unique(cls, return_counts=True))},
        "note": spec["note"],
    }, indent=1))
    log(f"rice mask -> {share:.1%} of the six kabupaten flagged rice by the prior")
    for p in (config.RAW / "mask" / "x").glob("*.tif*"):
        p.unlink(missing_ok=True)
    dest.unlink(missing_ok=True)
    return cells


def main() -> None:
    steps = sys.argv[1:] or ["boundaries", "cells", "mask"]
    util.guard_disk()
    if "boundaries" in steps:
        boundaries()
    if "cells" in steps:
        build_cells()
    if "mask" in steps:
        rice_mask()


if __name__ == "__main__":
    main()
