"""Stage 1 · ingest — every raw input for Jabodetabek (bbox in config.py).

  GTFS          TransJakarta official feed (2.5 MB; 240 routes incl. 98 Mikrotrans, full
                frequencies.txt; licence UNSTATED → attribution + written confirmation pending).
  OSM           Geofabrik java-latest.osm.pbf (~900 MB) → osmium extract to the Jabodetabek
                bbox → tags-filter to a routing subset (highway/railway/public_transport) so
                R5 builds inside a 2.5 G heap; health POIs and rail stations exported first.
                The 900 MB parent is deleted immediately after the clip (disk floor).
  population    GHSL GHS-POP E2025 100 m tile R10_C29 (one tile covers all Jabodetabek).
                SPEC DEVIATION: WorldPop's 169 MB national raster is skipped — the GHSL tile
                is 22 MB and covers the region exactly. Logged in the README.
  jobs proxy    GHSL GHS-BUILT-S NRES E2020 100 m (non-residential built-up surface).
  facilities    HDX healthsites (ODbL) filtered to the bbox; OSM amenity=hospital|clinic|doctors
                from the clip as the cross-source.
  polygons      HDX COD-AB IDN ADM4 (CC BY-IGO) filtered to the 14 Jabodetabek kabupaten/kota.

Outputs: data/raw/<source>/, data/adm4.parquet, data/raw/manifest.json.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import config
import util
from util import log

RAW = config.RAW
OSM_DIR = RAW / "osm"
OSM_PARENT = OSM_DIR / "java-latest.osm.pbf"
OSM_ROUTING = OSM_DIR / "jabodetabek_routing.osm.pbf"
OSM_HEALTH = OSM_DIR / "health.geojsonseq"
OSM_RAILSTOPS = OSM_DIR / "railstops.geojsonseq"
GHS_POP_TIF = RAW / "ghsl" / "ghs_pop_e2025_r10_c29.tif"
GHS_NRES_TIF = RAW / "ghsl" / "ghs_nres_e2020_r10_c29.tif"
HEALTHSITES = RAW / "health" / "healthsites_idn.geojson"
ADM4 = config.DATA_DIR / "adm4.parquet"


def gtfs() -> None:
    util.fetch(config.TRANSJAKARTA_GTFS_URL, config.GTFS_DIR / "transjakarta.zip", key="gtfs_transjakarta")
    try:
        util.fetch(config.BOGOR_ANGKOT_GTFS_URL, config.GTFS_DIR / "bogor_angkot.zip", key="gtfs_bogor")
    except Exception as e:                       # community feed, best effort
        log("bogor angkot feed unavailable — skipping:", type(e).__name__, e)


def osm() -> None:
    if OSM_ROUTING.exists() and OSM_HEALTH.exists() and OSM_RAILSTOPS.exists():
        log("cached OSM derivatives")
        return
    clip = config.OSM_CLIP
    if not clip.exists():
        util.fetch(config.OSM_JAVA_PBF_URL, OSM_PARENT, key="osm_java_pbf", min_bytes=500_000_000)
        util.guard_disk()
        w, s, e, n = config.BBOX
        # -s simple: the default complete_ways strategy needs >3 GB on the 900 MB Java file
        # (it OOM-killed the unit). simple peaks at 2.1 GB and truncates only ways that leave
        # the bbox — irrelevant for a metro clip. Logged in the README.
        util.run(["osmium", "extract", "-b", f"{w},{s},{e},{n}", "-s", "simple",
                  "-o", clip, "--overwrite", OSM_PARENT])
    if OSM_PARENT.exists():
        log("deleting the 900 MB parent PBF (disk floor)")
        OSM_PARENT.unlink()
    util.manifest_put("osm_clip", url=config.OSM_JAVA_PBF_URL, path=str(clip.relative_to(config.CASE_DIR)),
                      bytes=clip.stat().st_size, bbox=config.BBOX, licence="ODbL 1.0")
    if not OSM_HEALTH.exists():
        tmp = OSM_DIR / "health.osm.pbf"
        util.run(["osmium", "tags-filter", "-o", tmp, "--overwrite", clip,
                  "nwr/amenity=hospital,clinic,doctors"])
        util.run(["osmium", "export", tmp, "-f", "geojsonseq", "-o", OSM_HEALTH, "--overwrite",
                  "--geometry-types=point,polygon", "-a", "type"])
        tmp.unlink()
    if not OSM_RAILSTOPS.exists():
        tmp = OSM_DIR / "railstops.osm.pbf"
        util.run(["osmium", "tags-filter", "-o", tmp, "--overwrite", clip,
                  "n/railway=station,halt,stop", "n/public_transport=station,stop_position"])
        util.run(["osmium", "export", tmp, "-f", "geojsonseq", "-o", OSM_RAILSTOPS, "--overwrite",
                  "--geometry-types=point", "-a", "type"])
        tmp.unlink()
    if not OSM_ROUTING.exists():
        # R5 only needs the street/footpath graph; dropping everything else keeps the graph
        # build inside the 2.5 G heap the resource rules allow.
        util.run(["osmium", "tags-filter", "-o", OSM_ROUTING, "--overwrite", clip,
                  "w/highway", "w/public_transport", "w/railway", "w/route",
                  "n/highway", "n/public_transport", "n/railway",
                  "r/type=restriction"])
    log("OSM routing clip", f"{OSM_ROUTING.stat().st_size/1e6:.0f} MB")


def ghsl() -> None:
    for url_suffix, tif in ((config.GHSL_POP_TILE, GHS_POP_TIF), (config.GHSL_NRES_TILE, GHS_NRES_TIF)):
        if tif.exists():
            log("cached", tif.name)
            continue
        z = RAW / "ghsl" / Path(url_suffix).name
        util.fetch(config.GHSL_FTP + url_suffix, z, key=tif.stem, min_bytes=500_000)
        with zipfile.ZipFile(z) as zf:
            member = next(n for n in zf.namelist() if n.lower().endswith(".tif"))
            tif.write_bytes(zf.read(member))
        z.unlink()
        util.manifest_put(tif.stem, licence="CC BY 4.0", source="JRC GHSL R2023A",
                          bytes=tif.stat().st_size)
        log("GHSL", tif.name, f"{tif.stat().st_size/1e6:.1f} MB")


def healthsites() -> None:
    if HEALTHSITES.exists():
        log("cached healthsites")
        return
    import requests
    r = requests.get("https://data.humdata.org/api/3/action/package_show",
                     params={"id": "indonesia-healthsites"}, headers=config.BROWSER_UA, timeout=90)
    r.raise_for_status()
    res = r.json()["result"]["resources"]
    cand = [x for x in res if "geojson" in (x.get("format", "") + x.get("name", "")).lower()]
    cand.sort(key=lambda x: ("shape" in x.get("name", "").lower(), -int(x.get("size") or 0)))
    if not cand:
        raise RuntimeError("no geojson resource on HDX indonesia-healthsites")
    util.fetch(cand[0]["download_url"], HEALTHSITES, key="healthsites",
               min_bytes=100_000)
    util.manifest_put("healthsites", licence="ODbL", source=config.HEALTHSITES_HDX,
                      resource=cand[0].get("name"), last_modified=cand[0].get("last_modified"),
                      bytes=HEALTHSITES.stat().st_size)


def codab() -> None:
    if ADM4.exists():
        log("cached adm4")
        return
    import geopandas as gpd
    import pyogrio

    z = RAW / "codab" / "idn_admin_boundaries.gdb.zip"
    util.fetch(config.COD_AB_GDB_URL, z, key="codab_adm4", min_bytes=10_000_000)
    with zipfile.ZipFile(z) as zf:
        gdb = sorted({n.split("/")[0] for n in zf.namelist() if ".gdb" in n})[0]
        zf.extractall(z.parent)
    path = z.parent / gdb
    layers = [l[0] for l in pyogrio.list_layers(path)]
    log("gdb layers:", layers)
    lay = next(l for l in layers if "adm4" in l.lower() or "admin4" in l.lower())
    g = gpd.read_file(path, layer=lay, engine="pyogrio")
    g.columns = [c.upper() if c != "geometry" else c for c in g.columns]
    log("adm4 columns:", list(g.columns))

    def col(*prefixes):
        for p in prefixes:
            for c in g.columns:
                if c.startswith(p):
                    return c
        raise KeyError(prefixes)

    pcol = col("ADM2_P")
    keep = {"ID" + c for c in config.JABODETABEK_BPS}
    g = g[g[pcol].isin(keep)].copy()
    g = g.rename(columns={col("ADM4_P"): "adm4_pcode", col("ADM4_EN", "ADM4_NAME", "ADM4"): "adm4_name",
                          col("ADM3_EN", "ADM3_NAME"): "adm3_name",
                          col("ADM2_EN", "ADM2_NAME"): "adm2_name",
                          col("ADM1_EN", "ADM1_NAME"): "adm1_name", pcol: "adm2_pcode"})
    g = g.to_crs(4326)
    g["geometry"] = g.geometry.make_valid()
    g = g[["adm4_pcode", "adm4_name", "adm3_name", "adm2_name", "adm1_name", "adm2_pcode", "geometry"]]
    g = g.dissolve(by="adm4_pcode", aggfunc="first").reset_index()
    ADM4.parent.mkdir(parents=True, exist_ok=True)
    g.to_parquet(ADM4)
    util.manifest_put("codab_adm4", licence="CC BY-IGO (HDX COD-AB / BPS)", layer=lay,
                      units=int(len(g)), url=config.COD_AB_GDB_URL)
    log("ADM4 kelurahan in Jabodetabek:", len(g), "| kabupaten/kota:", g.adm2_name.nunique())
    # the gdb is 1 GB unzipped — drop it once the parquet exists
    import shutil
    shutil.rmtree(path, ignore_errors=True)
    z.unlink(missing_ok=True)


def main() -> None:
    util.guard_disk()
    RAW.mkdir(parents=True, exist_ok=True)
    gtfs()
    ghsl()
    healthsites()
    codab()
    osm()
    log("ingest done. manifest:", util.MANIFEST)
    print(json.dumps({k: v.get("bytes") for k, v in
                      json.loads(util.MANIFEST.read_text()).items()}, indent=2))


if __name__ == "__main__":
    main()
