"""Stage 1 - ingest.  Resumable, idempotent, disk-guarded, unattended.

Pulls every raster tile set in ``config.RASTERS`` for the 13 Indonesian 10-degree tiles
through the GFW Data API (``download/geotiff`` -> 307 -> presigned S3), plus the Universal
Mill List point table and the COD-AB province boundaries.  ~1.5 GB total, HEAD-verified.

Resumability: ``data/manifest.json`` is the ledger.  A tile that is present on disk with the
recorded byte count is skipped; a partial file (``.part``) is resumed with a Range request.
Before every tile the free-disk guard runs -- under ``config.MIN_FREE_GB`` the process logs
and exits 0 so systemd does not restart-loop and the next run picks up where it stopped.

  uv run python pipeline/ingest.py                 # everything
  uv run python pipeline/ingest.py --alerts-only   # weekly refresh: RADD + GLAD only
  uv run python pipeline/ingest.py --layers radd,tcl30
  uv run python pipeline/ingest.py --calibrate-palm
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

import config

LOG = sys.stdout


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), *a, file=LOG, flush=True)


# ---------------------------------------------------------------- manifest ------------
def load_manifest() -> dict:
    if config.MANIFEST.exists():
        try:
            return json.loads(config.MANIFEST.read_text())
        except json.JSONDecodeError:
            log("manifest corrupt, starting a new one")
    return {"api_host": config.GFW_API, "layers": {}, "licences": config.LICENCES,
            "rejected": config.REJECTED}


def save_manifest(m: dict) -> None:
    config.MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=1, sort_keys=True))
    tmp.replace(config.MANIFEST)


# ---------------------------------------------------------------- http ----------------
def _sleep_backoff(attempt: int, resp: requests.Response | None = None) -> None:
    wait = min(120, 5 * 2 ** attempt)
    if resp is not None and resp.headers.get("Retry-After", "").isdigit():
        wait = min(300, int(resp.headers["Retry-After"]))
    log(f"    backing off {wait}s")
    time.sleep(wait)


def signed_url(spec: dict, tile: str) -> str | None:
    """download/geotiff answers 307 with a presigned S3 URL (the bucket is requester-pays,
    so the redirect is the only anonymous-ish way in)."""
    url = config.GFW_DOWNLOAD_URL.format(tile=tile, **spec)
    for attempt in range(5):
        try:
            r = requests.get(url, headers=config.GFW_HEADERS, allow_redirects=False, timeout=120)
        except requests.RequestException as exc:
            log("    request error", type(exc).__name__)
            _sleep_backoff(attempt)
            continue
        if r.status_code in (301, 302, 307, 308):
            return r.headers["Location"]
        if r.status_code in (404, 400):
            return None                                   # tile genuinely absent (all ocean)
        if r.status_code in (429, 500, 502, 503, 504):
            log(f"    HTTP {r.status_code} on {tile}")
            _sleep_backoff(attempt, r)
            continue
        log(f"    HTTP {r.status_code} on {tile}: {r.text[:160]}")
        return None
    return None


def fetch_tile(spec: dict, tile: str, dest: Path) -> int | None:
    """Stream one tile to ``dest``, resuming a ``.part`` file when one is there."""
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(5):
        url = signed_url(spec, tile)
        if url is None:
            return None
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(30, 300)) as r:
                if r.status_code == 404:
                    # The API happily presigns a key that was never written (the tile is
                    # all ocean).  That is an absent tile, not a transient failure.
                    part.unlink(missing_ok=True)
                    return None
                if r.status_code in (403, 416) and have:
                    part.unlink(missing_ok=True)          # stale presign / bad offset
                    continue
                if r.status_code not in (200, 206):
                    log(f"    S3 HTTP {r.status_code} on {tile}")
                    _sleep_backoff(attempt, r)
                    continue
                mode = "ab" if r.status_code == 206 and have else "wb"
                with open(part, mode) as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
        except requests.RequestException as exc:
            log("    stream error", type(exc).__name__)
            _sleep_backoff(attempt)
            continue
        size = part.stat().st_size
        if size == 0:
            part.unlink(missing_ok=True)
            return None
        part.replace(dest)
        return size
    return None


# ---------------------------------------------------------------- layers --------------
def ingest_raster(name: str, manifest: dict) -> None:
    spec = config.RASTERS[name]
    out = config.RAW / name
    out.mkdir(parents=True, exist_ok=True)
    entry = manifest["layers"].setdefault(name, {"tiles": {}})
    entry.update({k: spec[k] for k in
                  ("dataset", "version", "grid", "pixel_meaning", "licence", "cite")})
    log(f"[{name}] {spec['dataset']} {spec['version']} {spec['pixel_meaning']} "
        f"(~{spec['size_mb']} MB)")
    for tile in config.TILES_IDN:
        dest = out / f"{tile}.tif"
        rec = entry["tiles"].get(tile)
        if rec is not None and (rec.get("absent") or
                                (dest.exists() and dest.stat().st_size == rec.get("bytes"))):
            continue
        if not config.disk_ok(need_gb=0.4):
            log(f"DISK GUARD: {config.free_gb():.1f} GB free < {config.MIN_FREE_GB} GB — "
                "stopping cleanly, rerun to resume")
            save_manifest(manifest)
            raise SystemExit(0)
        t0 = time.time()
        size = fetch_tile(spec, tile, dest)
        if size is None:
            entry["tiles"][tile] = {"absent": True}
            log(f"  {tile}: no tile published (ocean / outside coverage)")
        else:
            entry["tiles"][tile] = {"bytes": size, "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                           time.gmtime())}
            log(f"  {tile}: {size/2**20:.1f} MB in {time.time()-t0:.0f}s "
                f"({config.free_gb():.1f} GB free)")
        save_manifest(manifest)


def ingest_mills(manifest: dict) -> None:
    """UML points.  The 50 km buffered raster 403s on a free key, so we keep the points and
    build catchments with a KD-tree in link.py (which also gives distance + mill identity)."""
    import pandas as pd
    if config.MILLS_PARQUET.exists():
        log("[mills] already present")
        return
    # The published field list varies by UML version (v202508 has no `group_name`), so ask for
    # the columns the API actually exposes rather than a hard-coded list.
    fr = requests.get(f"{config.GFW_API}/dataset/{config.MILLS['dataset']}/"
                      f"{config.MILLS['version']}/fields", headers=config.GFW_HEADERS, timeout=90)
    fr.raise_for_status()
    avail = {f["name"] for f in fr.json()["data"]}
    cols = [c for c in config.MILL_FIELDS if c in avail]
    sql = f"SELECT {', '.join(cols)} FROM data WHERE country = 'Indonesia'"
    r = requests.post(config.GFW_QUERY_URL.format(**config.MILLS),
                      headers=config.GFW_HEADERS, json={"sql": sql}, timeout=300)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["data"])
    for missing in set(config.MILL_FIELDS) - set(df.columns):
        df[missing] = None
    log(f"[mills] UML fields used: {', '.join(cols)}")
    for c in ("latitude", "longitude"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
    config.MILLS_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(config.MILLS_PARQUET, index=False)
    manifest["layers"]["mills"] = {"dataset": config.MILLS["dataset"],
                                   "version": config.MILLS["version"],
                                   "licence": config.MILLS["licence"],
                                   "cite": config.MILLS["cite"], "rows": int(len(df))}
    log(f"[mills] {len(df)} Indonesian mills "
        f"({int((df.rspo_statu == 'RSPO Certified').sum())} RSPO certified)")
    save_manifest(manifest)


def ingest_boundaries(manifest: dict) -> None:
    """COD-AB (CC BY-IGO) provinces; geoBoundaries ADM1 as the fallback."""
    import geopandas as gpd
    if config.BOUNDARIES.exists():
        log("[adm1] already present")
        return
    gdb = config.RAW / "idn_admin_boundaries.gdb.zip"
    src, licence = None, config.LICENCES["hdx_cod_ab_idn"]
    if not gdb.exists() and config.disk_ok(need_gb=0.5):
        try:
            with requests.get(config.COD_AB_GDB_URL, stream=True, timeout=(30, 600)) as r:
                r.raise_for_status()
                gdb.parent.mkdir(parents=True, exist_ok=True)
                with open(gdb, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
        except Exception as exc:                                   # noqa: BLE001
            log("[adm1] COD-AB download failed:", type(exc).__name__, str(exc)[:120])
            gdb.unlink(missing_ok=True)
    if gdb.exists():
        try:
            layers = gpd.list_layers(gdb)["name"].tolist()
            adm1 = next(n for n in layers if "adm1" in n.lower())
            g = gpd.read_file(gdb, layer=adm1)
            src = f"COD-AB {adm1}"
        except Exception as exc:                                   # noqa: BLE001
            log("[adm1] COD-AB read failed:", type(exc).__name__, str(exc)[:120])
    if src is None:
        g = gpd.read_file(config.GB_ADM1_URL)
        src, licence = "geoBoundaries IDN ADM1 (gbOpen)", "CC BY 3.0 IGO / ODbL upstream"
    namecol = next((c for c in g.columns
                    if c.upper().startswith(("ADM1_EN", "SHAPENAME", "ADM1_NAME", "NAME"))), None)
    g = g.rename(columns={namecol: "province"})
    keep = ["province", "geometry"] + [c for c in g.columns if c.upper().startswith("ADM1_PCODE")]
    g = g[keep].to_crs(4326)
    g["geometry"] = g.geometry.make_valid()
    g.to_parquet(config.BOUNDARIES, index=False)
    manifest["layers"]["adm1"] = {"source": src, "licence": licence, "rows": int(len(g))}
    log(f"[adm1] {len(g)} provinces from {src}")
    save_manifest(manifest)
    if gdb.exists():
        gdb.unlink()                                    # 219 MB — do not keep it around


def calibrate_palm(manifest: dict) -> None:
    """Map SDPT ``simpleType`` raster values -> class names, empirically.

    The raster carries no value table, so we sample Indonesian SDPT polygons through the
    vector query endpoint and read the raster at their representative points.  The result is
    written to data/raw/palm_classes.json and consumed by link.py, so the palm class is never
    a guess baked into code.
    """
    import numpy as np
    import rasterio

    tif = config.RAW / "palm" / "00N_100E.tif"
    if not tif.exists():
        log("[palm] raster tile missing; run the palm layer first")
        return
    # Sample pixel locations per distinct raster value, then ask the vector table what class
    # sits there.  A geometry-filtered query is fast; the unfiltered global one times out.
    url = config.GFW_QUERY_URL.format(dataset=config.RASTERS["palm"]["dataset"],
                                      version=config.RASTERS["palm"]["version"])
    counts: dict[int, dict[str, int]] = {}
    with rasterio.open(tif) as ds:
        rng = np.random.default_rng(11)
        seen: dict[int, list[tuple[float, float]]] = {}
        for _ in range(240):
            r0, c0 = int(rng.integers(0, ds.height - 2000)), int(rng.integers(0, ds.width - 2000))
            a = ds.read(1, window=((r0, r0 + 2000), (c0, c0 + 2000)))
            for v in np.unique(a):
                v = int(v)
                if v == 0 or len(seen.get(v, [])) >= 3:
                    continue
                rr, cc = (np.argwhere(a == v)[0]).tolist()
                x, y = ds.xy(r0 + rr, c0 + cc)
                seen.setdefault(v, []).append((float(x), float(y)))
            if len(seen) >= 12 and all(len(p) >= 3 for p in seen.values()):
                break
    for v, pts in seen.items():
        for x, y in pts:
            d = 0.002
            box = {"type": "Polygon", "coordinates": [[[x - d, y - d], [x + d, y - d],
                                                       [x + d, y + d], [x - d, y + d],
                                                       [x - d, y - d]]]}
            r = requests.post(url, headers=config.GFW_HEADERS, timeout=180,
                              json={"sql": "SELECT simplename FROM data LIMIT 5",
                                    "geometry": box})
            if r.status_code != 200:
                continue
            for rec in r.json().get("data", []):
                st = rec.get("simplename")
                if st:
                    counts.setdefault(v, {}).setdefault(st, 0)
                    counts[v][st] += 1
    mapping = {int(v): max(sts, key=sts.get) for v, sts in counts.items() if sts}
    palm_vals = sorted(v for v, st in mapping.items() if "oil palm" in st.lower())
    if not palm_vals:
        log("[palm] CALIBRATION FOUND NO OIL-PALM CLASS — palm linkage would be silently "
            "empty, so nothing is written. Sampled:", json.dumps(mapping))
        return
    out = {"simplename_by_value": mapping, "palm_values": palm_vals,
           "n_samples": {int(v): sum(s.values()) for v, s in counts.items()}}
    config.PALM_CLASS_FILE.write_text(json.dumps(out, indent=1))
    log("[palm] class calibration:", json.dumps(mapping))
    manifest["layers"].setdefault("palm", {})["palm_values"] = palm_vals
    save_manifest(manifest)


# ---------------------------------------------------------------- main ----------------
def main(argv: list[str]) -> None:
    config.RAW.mkdir(parents=True, exist_ok=True)
    if not config.GFW_API_KEY:
        log("FATAL: GFW_API_KEY missing from the repo-root .env")
        raise SystemExit(2)
    if not config.gfw_key_ok():
        log("FATAL: the GFW key was rejected by an authenticated endpoint. NOTE: the header "
            "must be spelled exactly 'x-api-key' — urllib title-cases it and fails.")
        raise SystemExit(2)
    log(f"disk: {config.free_gb():.1f} GB free (floor {config.MIN_FREE_GB} GB)")

    manifest = load_manifest()
    if "--calibrate-palm" in argv:
        calibrate_palm(manifest)
        return
    if "--layers" in argv:
        layers = argv[argv.index("--layers") + 1].split(",")
    elif "--alerts-only" in argv:
        layers = ["radd", "glad"]
    else:
        layers = list(config.INGEST_ORDER)

    for name in layers:
        ingest_raster(name, manifest)
    if "--alerts-only" not in argv:
        ingest_mills(manifest)
        ingest_boundaries(manifest)
        calibrate_palm(manifest)
    total = sum(t.get("bytes", 0) for lay in manifest["layers"].values()
                for t in lay.get("tiles", {}).values())
    log(f"ingest complete — {total/2**30:.2f} GB on disk, {config.free_gb():.1f} GB free")


if __name__ == "__main__":
    main(sys.argv[1:])
