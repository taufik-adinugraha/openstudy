"""Stage 1 · ingest — streaming acquisition + per-admin-unit aggregation.

Nothing is retained at full resolution. Every source is pulled one partition / tile /
year at a time, reduced immediately to per-kecamatan (ADM3) aggregates, written to
data/interim/<source>/<key>.parquet, and the raw file is deleted before the next unit
is fetched. Retained footprint stays ~1-2 GB regardless of the ~20 GB that flows through.

Resumability: every completed unit is appended to data/ingest_ledger.jsonl; a rerun skips
what is already there. Idempotent — re-doing a unit overwrites exactly its own parquet.
Disk guard: before each unit, if free disk < MIN_FREE_GB the job logs and exits 0 so
systemd does not restart-loop; rerunning the same command resumes.

Stages (priority order, spec F2/F3):
  bps         BPS P0/P1/P2/poverty-line 2016-2025 per kabupaten/kota  (pipeline/bps.py)
  boundaries  HDX COD-AB ADM2/ADM3/ADM4 gdb + P-code xlsx -> parquet topology
  worldpop    WorldPop R2025A constrained 100 m, 2016-2025, zonal -> population
  lights      Black Marble annual composites REUSED from Flagship A (never re-downloaded)
  buildings   Google Open Buildings v3, S2 level-6 partitions, streamed -> roof stats
  worldcover  ESA WorldCover 2021 v200 3x3 deg tiles, windowed -> land-cover shares
  merge       fold the interim partitions into data/features_raw_adm3.parquet (+ ADM2)

Not yet implemented (see README "Decisions pending user verification"): GHSL BUILT-S/NRES/
SMOD (Mollweide tiles), OSM highway density, Sentinel-2 spectral indices.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import config

INTERIM = config.DATA_DIR / "interim"
LEDGER = config.DATA_DIR / "ingest_ledger.jsonl"
MIN_FREE_GB = 10.0
CHUNK = 1 << 20
GCS_LIST = "https://storage.googleapis.com/storage/v1/b/open-buildings-data/o"
OB_L6_PREFIX = "v3/polygons_s2_level_6_gzip_no_header/"
OB_COLS = ["latitude", "longitude", "area_in_meters", "confidence", "geometry", "full_plus_code"]
OB_RES_DEG = 0.0005                     # ~55 m admin-index raster for point assignment
OB_MARGIN_DEG = 0.05
ROOF_EDGES = np.array([20, 30, 40, 50, 60, 80, 100, 130, 170, 220, 300, 400, 600, 1000, 2000, 5000],
                      dtype="float64")
ROOF_BINS = len(ROOF_EDGES) + 1
WC_CLASSES = [10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100]
WC_BLOCK = 4096


# --------------------------------------------------------------------------- infrastructure
def free_gb(path: Path | None = None) -> float:
    p = path or config.DATA_DIR
    p.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(p).free / 1e9


def guard(label: str) -> None:
    """Clean, resumable stop when the shared box is running out of room."""
    fg = free_gb()
    if fg < MIN_FREE_GB:
        print(f"[guard] free disk {fg:.1f} GB < {MIN_FREE_GB} GB — stopping cleanly before {label}; "
              f"rerun the same command to resume", flush=True)
        sys.exit(0)


def _ledger_rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def done_keys(stage: str) -> set[str]:
    return {r["key"] for r in _ledger_rows() if r.get("stage") == stage and r.get("ok")}


def mark(stage: str, key: str, ok: bool = True, **info) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    rec = {"stage": stage, "key": key, "ok": ok, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "free_gb": round(free_gb(), 1), **info}
    with LEDGER.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


def download(url: str, dest: Path, tries: int = 4, label: str = "") -> Path:
    """Resumable single-connection GET (bandwidth is shared — never parallelise a file)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, tries + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = dict(config.BROWSER_UA)
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with requests.get(url, headers=headers, stream=True, timeout=180) as resp:
                if resp.status_code == 416:                       # already complete
                    break
                if have and resp.status_code != 206:              # server ignored Range
                    part.unlink(missing_ok=True)
                    have = 0
                resp.raise_for_status()
                mode = "ab" if have and resp.status_code == 206 else "wb"
                with part.open(mode) as fh:
                    for blk in resp.iter_content(CHUNK):
                        fh.write(blk)
            break
        except requests.RequestException as err:
            if attempt == tries:
                raise
            print(f"[dl] {label or dest.name} attempt {attempt} failed ({err}); retrying",
                  flush=True)
            time.sleep(5 * attempt)
    part.replace(dest)
    return dest


def head_ok(url: str) -> int:
    try:
        r = requests.head(url, headers=config.BROWSER_UA, timeout=60, allow_redirects=True)
        return int(r.headers.get("Content-Length", 0)) if r.status_code == 200 else 0
    except requests.RequestException:
        return 0


# --------------------------------------------------------------------------- boundaries
def load_adm3():
    """ADM3 (kecamatan) topology with its ADM2 parent — the aggregation target for every layer."""
    import geopandas as gpd

    if not config.BOUNDARIES_ADM3.exists():
        boundaries()
    gdf = gpd.read_parquet(config.BOUNDARIES_ADM3)
    if config.SCOPE == "java":
        gdf = gdf[gdf["prov_code"].isin(config.JAVA_PROVINCE_CODES)].reset_index(drop=True)
    return gdf


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def boundaries() -> None:
    """HDX COD-AB (BPS-derived, CC BY-IGO, P-coded) -> adm2/adm3 parquet + BPS reconciliation."""
    import geopandas as gpd
    import pyogrio

    dst = config.BOUNDARIES_ADM3.parent
    dst.mkdir(parents=True, exist_ok=True)
    zip_path = config.RAW / "codab" / "idn_admin_boundaries.gdb.zip"
    if not (config.BOUNDARIES_ADM2.exists() and config.BOUNDARIES_ADM3.exists()):
        guard("COD-AB gdb (219 MB)")
        if not zip_path.exists():
            print("[boundaries] downloading COD-AB gdb.zip (219 MB)", flush=True)
            download(config.COD_AB_GDB_URL, zip_path, label="codab gdb")
        layers = [str(name) for name, _ in pyogrio.list_layers(f"/vsizip/{zip_path}")]
        print(f"[boundaries] layers: {layers}", flush=True)
        for level, target in ((2, config.BOUNDARIES_ADM2), (3, config.BOUNDARIES_ADM3)):
            hits = [ly for ly in layers if ly.lower().endswith(f"admin{level}")] or \
                   [ly for ly in layers if f"adm{level}" in ly.lower()]
            if not hits:
                raise RuntimeError(f"[boundaries] no ADM{level} layer in {layers}")
            gdf = gpd.read_file(f"/vsizip/{zip_path}", layer=hits[0])
            gdf.columns = [c.lower() for c in gdf.columns]
            pc, nm = f"adm{level}_pcode", f"adm{level}_name"
            out = gpd.GeoDataFrame({
                "pcode": gdf[pc].astype(str),
                "name": gdf[nm].astype(str),
                "adm2_code": gdf[pc].map(_digits).str[:4],
                "prov_code": gdf[pc].map(_digits).str[:2],
                "geometry": gdf.geometry,
            }, crs=gdf.crs).to_crs(4326)
            out["bps_code"] = out["pcode"].map(_digits).str[: 4 if level >= 2 else 2]
            out = out[out.geometry.notna() & ~out.geometry.is_empty].reset_index(drop=True)
            out.to_parquet(target, index=False)
            print(f"[boundaries] ADM{level}: {len(out)} units -> {target.name}", flush=True)
        zip_path.unlink(missing_ok=True)                 # streamed: raw deleted after reduction

    adm2 = gpd.read_parquet(config.BOUNDARIES_ADM2)
    reconcile(adm2)
    mark("boundaries", "codab", adm2=len(adm2),
         adm3=len(gpd.read_parquet(config.BOUNDARIES_ADM3)))


def reconcile(adm2) -> None:
    """P-code <-> BPS code reconciliation; unmatched counts are logged, never silently dropped."""
    bps_path = config.DATA_DIR / "bps_poverty.parquet"
    if not bps_path.exists():
        print("[boundaries] BPS parquet absent — reconciliation deferred", flush=True)
        return
    bps = pd.read_parquet(bps_path)
    latest = bps[bps["year"] == bps["year"].max()]
    b_codes, g_codes = set(latest["bps_code"]), set(adm2["bps_code"])
    only_bps = sorted(b_codes - g_codes)
    only_geo = sorted(g_codes - b_codes)
    out = config.DATA_DIR / "adm2_reconciliation.csv"
    rows = [{"bps_code": c, "side": "bps_only",
             "name": latest.loc[latest["bps_code"] == c, "bps_name"].iloc[0]} for c in only_bps]
    rows += [{"bps_code": c, "side": "codab_only",
              "name": adm2.loc[adm2["bps_code"] == c, "name"].iloc[0]} for c in only_geo]
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"[boundaries] reconcile ADM2: COD-AB {len(g_codes)} vs BPS {len(b_codes)} — "
          f"matched {len(b_codes & g_codes)}, BPS-only {len(only_bps)}, COD-AB-only {len(only_geo)}"
          f" -> {out.name}", flush=True)


# --------------------------------------------------------------------------- zonal helpers
def admin_raster(gdf, bounds, res: float):
    """Rasterise ADM3 unit *positions* (1-based; 0 = nothing) so point assignment is O(1)."""
    from rasterio import features, transform as rtransform

    west, south, east, north = bounds
    width = max(int(np.ceil((east - west) / res)), 1)
    height = max(int(np.ceil((north - south) / res)), 1)
    tr = rtransform.from_origin(west, north, res, res)
    arr = features.rasterize(
        ((geom, i + 1) for i, geom in enumerate(gdf.geometry)),
        out_shape=(height, width), transform=tr, fill=0, dtype="int32", all_touched=False)
    return arr, tr


def zonal(tif: Path, gdf, ops: list[str]) -> pd.DataFrame:
    from exactextract import exact_extract

    res = exact_extract(str(tif), gdf, ops, output="pandas")
    out = pd.DataFrame({"pcode": gdf["pcode"].values})
    for op in ops:
        col = op if op in res.columns else f"band_1_{op}"
        out[op] = pd.to_numeric(res[col], errors="coerce").values
    return out


def _write(stage: str, key: str, df: pd.DataFrame) -> Path:
    path = INTERIM / stage / f"{key}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


# --------------------------------------------------------------------------- worldpop
def worldpop() -> None:
    gdf = load_adm3()
    have = done_keys("worldpop")
    for year in sorted(config.BPS_YEARS):
        key = str(year)
        if key in have:
            continue
        guard(f"worldpop {year}")
        url = config.WORLDPOP_URL.format(year=year)
        tif = config.RAW / "worldpop" / f"idn_pop_{year}.tif"
        t0 = time.time()
        try:
            download(url, tif, label=f"worldpop {year}")
            df = zonal(tif, gdf, ["sum", "count"]).rename(
                columns={"sum": "pop", "count": "pop_px"})
            df["year"] = year
            df["pop"] = df["pop"].fillna(0.0)
            _write("worldpop", key, df)
            mark("worldpop", key, rows=len(df), secs=round(time.time() - t0, 1),
                 pop_total=float(df["pop"].sum()))
            print(f"[worldpop] {year}: {len(df)} ADM3 rows, total pop "
                  f"{df['pop'].sum()/1e6:.1f} M, {time.time()-t0:.0f}s", flush=True)
        except Exception as err:
            mark("worldpop", key, ok=False, error=str(err)[:300])
            print(f"[worldpop] {year} FAILED: {err}", flush=True)
        finally:
            tif.unlink(missing_ok=True)                   # raw deleted after aggregation


# --------------------------------------------------------------------------- black marble
def lights() -> None:
    """Reuse Flagship A's annual composites on the dev server — never re-downloaded."""
    gdf = load_adm3()
    have = done_keys("lights")
    base = config.BLACK_MARBLE_ANNUAL_DIR
    if not base.exists():
        print(f"[lights] {base} absent — Flagship A composites not on this host; skipped",
              flush=True)
        return
    for ydir in sorted(base.glob("*-01")):
        year = ydir.name.split("-")[0]
        if year in have:
            continue
        tifs = sorted(ydir.glob("*A4_radiance.tif"))
        if not tifs:
            continue
        t0 = time.time()
        try:
            df = zonal(tifs[0], gdf, ["sum", "mean", "count"]).rename(
                columns={"sum": "lights_sol", "mean": "lights_mean", "count": "lights_px"})
            df["year"] = int(year)
            df["lights_product"] = tifs[0].stem.split("_")[0]
            _write("lights", year, df)
            mark("lights", year, rows=len(df), secs=round(time.time() - t0, 1),
                 product=df["lights_product"].iloc[0])
            print(f"[lights] {year} ({df['lights_product'].iloc[0]}): {len(df)} ADM3 rows, "
                  f"{time.time()-t0:.0f}s", flush=True)
        except Exception as err:
            mark("lights", year, ok=False, error=str(err)[:300])
            print(f"[lights] {year} FAILED: {err}", flush=True)


# --------------------------------------------------------------------------- open buildings
def ob_partitions() -> list[dict]:
    """Level-6 S2 partitions of Open Buildings v3 that intersect the scope bbox."""
    import s2sphere as s2

    cache = config.RAW / "ob" / f"partitions_{config.SCOPE}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    west, south, east, north = config.BBOX
    items, token = [], None
    while True:
        params = {"prefix": OB_L6_PREFIX, "maxResults": 1000, "fields": "items(name,size),nextPageToken"}
        if token:
            params["pageToken"] = token
        r = requests.get(GCS_LIST, params=params, headers=config.BROWSER_UA, timeout=120)
        r.raise_for_status()
        page = r.json()
        items += page.get("items", [])
        token = page.get("nextPageToken")
        if not token:
            break
    out = []
    for it in items:
        name = it["name"].rsplit("/", 1)[-1]
        if not name.endswith("_buildings.csv.gz"):
            continue
        tok = name.split("_")[0]
        try:
            cell = s2.Cell(s2.CellId.from_token(tok))
            lls = [s2.LatLng.from_point(cell.get_vertex(i)) for i in range(4)]
        except Exception:
            continue
        lats = [ll.lat().degrees for ll in lls]
        lons = [ll.lng().degrees for ll in lls]
        b = (min(lons), min(lats), max(lons), max(lats))
        if b[0] > east or b[2] < west or b[1] > north or b[3] < south:
            continue
        out.append({"token": tok, "size": int(it["size"]), "bounds": b,
                    "url": f"https://storage.googleapis.com/open-buildings-data/{it['name']}"})
    out.sort(key=lambda d: d["size"])              # smallest first: early wins, low disk risk
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, indent=1))
    print(f"[buildings] {len(out)} level-6 partitions intersect {config.SCOPE} bbox, "
          f"{sum(d['size'] for d in out)/1e9:.1f} GB gz total", flush=True)
    return out


def ob_reduce(path: Path, gdf, bounds) -> tuple[pd.DataFrame, int, int]:
    """Stream one partition; accumulate per-ADM3 roof count/area/size-histogram. Never holds
    the partition in memory — pyarrow batches, numpy bincount accumulators only."""
    import pyarrow as pa
    from pyarrow import csv as pacsv

    # S2 cell edges are geodesics, so the corner box can under-cover: pad before rasterising.
    west, south, east, north = (bounds[0] - OB_MARGIN_DEG, bounds[1] - OB_MARGIN_DEG,
                                bounds[2] + OB_MARGIN_DEG, bounds[3] + OB_MARGIN_DEG)
    sub = gdf.cx[west:east, south:north]
    if sub.empty:
        return pd.DataFrame(), 0, 0
    sub = sub.reset_index(drop=True)
    idx, tr = admin_raster(sub, (west, south, east, north), OB_RES_DEG)
    height, width = idx.shape
    n = len(sub)
    count = np.zeros(n, "int64")
    area_sum = np.zeros(n, "float64")
    area_sq = np.zeros(n, "float64")
    hist = np.zeros(n * ROOF_BINS, "int64")
    seen = dropped = 0

    ropts = pacsv.ReadOptions(column_names=OB_COLS, block_size=1 << 26)
    copts = pacsv.ConvertOptions(include_columns=OB_COLS[:4],
                                 column_types={"latitude": pa.float64(), "longitude": pa.float64(),
                                               "area_in_meters": pa.float64(),
                                               "confidence": pa.float64()})
    stream = pa.CompressedInputStream(pa.OSFile(str(path), "rb"), "gzip")
    with pacsv.open_csv(stream, read_options=ropts, convert_options=copts) as reader:
        for batch in reader:
            lat = batch.column("latitude").to_numpy(zero_copy_only=False)
            lon = batch.column("longitude").to_numpy(zero_copy_only=False)
            area = batch.column("area_in_meters").to_numpy(zero_copy_only=False)
            conf = batch.column("confidence").to_numpy(zero_copy_only=False)
            seen += lat.size
            keep = np.isfinite(lat) & np.isfinite(lon) & np.isfinite(area) & \
                (conf >= config.OB_MIN_CONFIDENCE)
            if not keep.any():
                continue
            lat, lon, area = lat[keep], lon[keep], area[keep]
            row = ((north - lat) / OB_RES_DEG).astype("int64")
            col = ((lon - west) / OB_RES_DEG).astype("int64")
            inside = (row >= 0) & (row < height) & (col >= 0) & (col < width)
            dropped += int((~inside).sum())
            if not inside.any():
                continue
            a = idx[row[inside], col[inside]]
            m = a > 0
            if not m.any():
                continue
            ai = (a[m] - 1).astype("int64")
            ar = area[inside][m]
            count += np.bincount(ai, minlength=n)
            area_sum += np.bincount(ai, weights=ar, minlength=n)
            area_sq += np.bincount(ai, weights=ar * ar, minlength=n)
            hb = np.digitize(ar, ROOF_EDGES)
            hist += np.bincount(ai * ROOF_BINS + hb, minlength=n * ROOF_BINS)

    df = pd.DataFrame({"pcode": sub["pcode"].values, "ob_count": count,
                       "ob_area_sum": area_sum, "ob_area_sq": area_sq})
    hb = hist.reshape(n, ROOF_BINS)
    for j in range(ROOF_BINS):
        df[f"ob_h{j:02d}"] = hb[:, j]
    df = df[df["ob_count"] > 0].reset_index(drop=True)
    return df, seen, dropped


def buildings() -> None:
    gdf = load_adm3()
    parts = ob_partitions()
    have = done_keys("buildings")
    todo = [p for p in parts if p["token"] not in have]
    print(f"[buildings] {len(todo)}/{len(parts)} partitions to do "
          f"({sum(p['size'] for p in todo)/1e9:.1f} GB gz remaining)", flush=True)
    for i, part in enumerate(todo, 1):
        tok = part["token"]
        guard(f"OB partition {tok} ({part['size']/1e6:.0f} MB)")
        raw = config.RAW / "ob" / f"{tok}_buildings.csv.gz"
        t0 = time.time()
        try:
            download(part["url"], raw, label=f"OB {tok}")
            dl = time.time() - t0
            df, seen, dropped = ob_reduce(raw, gdf, part["bounds"])
            if len(df):
                _write("buildings", tok, df)
            mark("buildings", tok, rows=len(df), bldgs=int(df["ob_count"].sum()) if len(df) else 0,
                 read=seen, outside=dropped, mb=round(part["size"] / 1e6, 1),
                 secs=round(time.time() - t0, 1), dl_secs=round(dl, 1))
            print(f"[buildings] ({i}/{len(todo)}) {tok} {part['size']/1e6:.0f} MB: "
                  f"{seen:,} rows read, {int(df['ob_count'].sum()) if len(df) else 0:,} kept in "
                  f"{len(df)} ADM3 units, dl {dl:.0f}s total {time.time()-t0:.0f}s, "
                  f"free {free_gb():.1f} GB", flush=True)
        except Exception as err:
            mark("buildings", tok, ok=False, error=str(err)[:300])
            print(f"[buildings] {tok} FAILED: {err}", flush=True)
            traceback.print_exc()
        finally:
            raw.unlink(missing_ok=True)                   # raw deleted before the next partition
            raw.with_suffix(raw.suffix + ".part").unlink(missing_ok=True)


# --------------------------------------------------------------------------- worldcover
def wc_tiles() -> list[tuple[str, str]]:
    west, south, east, north = config.BBOX
    tiles = []
    for lat in range(-12, 6, 3):
        for lon in range(93, 142, 3):
            if lon > east or lon + 3 < west or lat > north or lat + 3 < south:
                continue
            la = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}"
            lo = f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
            tiles.append((la, lo))
    return tiles


def worldcover() -> None:
    import rasterio
    from rasterio.windows import Window

    gdf = load_adm3()
    have = done_keys("worldcover")
    tiles = wc_tiles()
    todo = [t for t in tiles if f"{t[0]}{t[1]}" not in have]
    print(f"[worldcover] {len(todo)}/{len(tiles)} tiles to do", flush=True)
    lut = {c: k for k, c in enumerate(WC_CLASSES)}
    codes = np.full(256, -1, "int64")
    for c, k in lut.items():
        codes[c] = k
    for la, lo in todo:
        key = f"{la}{lo}"
        guard(f"worldcover {key}")
        url = config.WORLDCOVER_S3 + config.WORLDCOVER_TILE.format(lat=la, lon=lo)
        tif = config.RAW / "worldcover" / f"{key}.tif"
        t0 = time.time()
        try:
            if not head_ok(url):
                mark("worldcover", key, absent=True)
                continue
            download(url, tif, label=f"worldcover {key}")
            acc: dict[str, np.ndarray] = {}
            with rasterio.open(tif) as src:
                for ro in range(0, src.height, WC_BLOCK):
                    for co in range(0, src.width, WC_BLOCK):
                        win = Window(co, ro, min(WC_BLOCK, src.width - co),
                                     min(WC_BLOCK, src.height - ro))
                        b = rasterio.windows.bounds(win, src.transform)
                        sub = gdf.cx[b[0]:b[2], b[1]:b[3]]
                        if sub.empty:
                            continue
                        sub = sub.reset_index(drop=True)
                        idx, _ = admin_raster(sub, b, src.transform.a)
                        data = src.read(1, window=win)
                        h = min(idx.shape[0], data.shape[0])
                        w = min(idx.shape[1], data.shape[1])
                        a, d = idx[:h, :w], data[:h, :w]
                        m = a > 0
                        if not m.any():
                            continue
                        ai = (a[m] - 1).astype("int64")
                        ck = codes[d[m]]
                        ok = ck >= 0
                        if not ok.any():
                            continue
                        counts = np.bincount(ai[ok] * len(WC_CLASSES) + ck[ok],
                                             minlength=len(sub) * len(WC_CLASSES))
                        counts = counts.reshape(len(sub), len(WC_CLASSES))
                        for pcode, rowv in zip(sub["pcode"].values, counts):
                            if rowv.any():
                                acc[pcode] = acc.get(pcode, np.zeros(len(WC_CLASSES), "int64")) + rowv
            if acc:
                df = pd.DataFrame([{"pcode": k, **{f"wc_{c}": int(v[j])
                                                   for j, c in enumerate(WC_CLASSES)}}
                                   for k, v in acc.items()])
                _write("worldcover", key, df)
                mark("worldcover", key, rows=len(df), secs=round(time.time() - t0, 1))
                print(f"[worldcover] {key}: {len(df)} ADM3 rows, {time.time()-t0:.0f}s",
                      flush=True)
            else:
                mark("worldcover", key, rows=0, secs=round(time.time() - t0, 1))
        except Exception as err:
            mark("worldcover", key, ok=False, error=str(err)[:300])
            print(f"[worldcover] {key} FAILED: {err}", flush=True)
        finally:
            tif.unlink(missing_ok=True)


# --------------------------------------------------------------------------- merge
def merge() -> None:
    """Fold the interim partitions into one ADM3 table (+ ADM2 rollup). NaN-safe: a unit with
    no coverage in a layer gets 0 for counts and NaN for means, never a silent drop."""
    import geopandas as gpd

    adm3 = gpd.read_parquet(config.BOUNDARIES_ADM3)[["pcode", "name", "adm2_code", "prov_code"]]
    out = adm3.copy()

    bld = [pd.read_parquet(p) for p in sorted((INTERIM / "buildings").glob("*.parquet"))]
    if bld:
        b = pd.concat(bld, ignore_index=True).groupby("pcode", as_index=False).sum(numeric_only=True)
        out = out.merge(b, on="pcode", how="left")
        for c in [c for c in out.columns if c.startswith("ob_")]:
            out[c] = out[c].fillna(0)
        print(f"[merge] buildings: {len(bld)} partitions, {int(b['ob_count'].sum()):,} buildings, "
              f"{len(b)} ADM3 units covered", flush=True)

    for stage, cols in (("worldpop", ["pop"]), ("lights", ["lights_sol", "lights_mean"])):
        parts = [pd.read_parquet(p) for p in sorted((INTERIM / stage).glob("*.parquet"))]
        if not parts:
            continue
        long = pd.concat(parts, ignore_index=True)
        wide = long.pivot_table(index="pcode", columns="year", values=cols, aggfunc="first")
        wide.columns = [f"{a}_{int(b)}" for a, b in wide.columns]
        out = out.merge(wide.reset_index(), on="pcode", how="left")
        print(f"[merge] {stage}: {len(parts)} years, {len(wide)} ADM3 units", flush=True)

    wc = [pd.read_parquet(p) for p in sorted((INTERIM / "worldcover").glob("*.parquet"))]
    if wc:
        w = pd.concat(wc, ignore_index=True).groupby("pcode", as_index=False).sum(numeric_only=True)
        out = out.merge(w, on="pcode", how="left")
        print(f"[merge] worldcover: {len(wc)} tiles, {len(w)} ADM3 units", flush=True)

    path = config.DATA_DIR / "features_raw_adm3.parquet"
    out.to_parquet(path, index=False)
    num = out.select_dtypes("number").columns
    adm2 = out.groupby("adm2_code", as_index=False)[list(num)].sum(min_count=1)
    adm2.to_parquet(config.DATA_DIR / "features_raw_adm2.parquet", index=False)
    print(f"[merge] {len(out)} ADM3 / {len(adm2)} ADM2 rows, {len(num)} numeric columns "
          f"-> {path.name}", flush=True)


# --------------------------------------------------------------------------- driver
def bps_stage() -> None:
    import bps

    df = bps.build()
    mark("bps", "621-624", rows=len(df), regencies=int(df["bps_code"].nunique()))


STAGES = {
    "bps": bps_stage,
    "boundaries": boundaries,
    "worldpop": worldpop,
    "lights": lights,
    "buildings": buildings,
    "worldcover": worldcover,
    "merge": merge,
}
ORDER = ["bps", "boundaries", "worldpop", "lights", "buildings", "worldcover", "merge"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stages", nargs="*", default=None,
                    help=f"one or more of {ORDER}, or 'all' (default)")
    args = ap.parse_args()
    stages = args.stages or ["all"]
    if stages == ["all"]:
        stages = ORDER
    config.RAW.mkdir(parents=True, exist_ok=True)
    INTERIM.mkdir(parents=True, exist_ok=True)
    print(f"[ingest] SCOPE={config.SCOPE} bbox={config.BBOX} free={free_gb():.1f} GB "
          f"stages={stages}", flush=True)
    failed = []
    for name in stages:
        if name not in STAGES:
            sys.exit(f"[ingest] unknown stage {name!r}; choose from {ORDER}")
        print(f"\n===== {name} =====", flush=True)
        t0 = time.time()
        try:
            STAGES[name]()
        except SystemExit:
            raise
        except Exception as err:
            failed.append(name)
            print(f"[ingest] stage {name} FAILED: {err}", flush=True)
            traceback.print_exc()
        print(f"[ingest] stage {name} done in {time.time()-t0:.0f}s, free {free_gb():.1f} GB",
              flush=True)
    print(f"[ingest] complete — failures: {failed or 'none'}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
