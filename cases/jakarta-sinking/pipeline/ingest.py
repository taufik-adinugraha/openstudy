"""Stage: ingest (spec C3) — the small open inputs (~300 MB), no accounts.

  velocity    Jakarta slice of the deposited Java velocity field (HTTP Range,
              filtered to BBOX, duplicates kept for grid.py to average)
  gnss        Susilo et al. 2023 station files
  dem         two GLO-30 tiles
  population  WorldPop constrained 2020 (national; clipped later), GHSL GHS-POP + GHS-BUILT-S tiles
  admin       Jakarta Satu kelurahan polygons (GeoJSON)
  floods      BPBD flood history 2021–2024 (paginated), UNOSAT + EOS 2020 extents

Idempotent: existing non-empty files are skipped. Each fetch is independent, so
a failure in one source never blocks the others; failures are summarised.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import requests

import config

RAW = config.RAW


def _get(url: str, dest: Path, headers: dict | None = None, timeout=600) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[ingest]   {dest.name} cached")
        return dest
    with requests.get(url, headers={**config.BROWSER_UA, **(headers or {})}, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    print(f"[ingest]   {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def velocity() -> None:
    dest = RAW / "velocity" / "ohenhen2026_jakarta_slice.csv"
    if dest.exists() and dest.stat().st_size > 0:
        print("[ingest]   velocity slice cached"); return
    dest.parent.mkdir(parents=True, exist_ok=True)
    lo, hi = config.ZENODO_VLM_RANGE
    w, s, e, n = config.BBOX
    header = ["Longitude", "Latitude", "VLM_cm_per_yr", "VLM_sd_cm_per_yr", "EW_cm_per_yr", "EW_sd_cm_per_yr"]
    kept = 0
    with requests.get(config.ZENODO_VLM_URL, headers={**config.BROWSER_UA, "Range": f"bytes={lo}-{hi}"},
                      stream=True, timeout=900) as r:
        r.raise_for_status()
        ranged = r.status_code == 206
        print(f"[ingest]   Zenodo velocity: {'ranged' if ranged else 'FULL FILE (Range unsupported)'} download …")
        buf = ""
        first = True
        with open(dest, "w", newline="") as out:
            wr = csv.writer(out); wr.writerow(header)
            for chunk in r.iter_content(1 << 20):
                buf += chunk.decode("utf-8", errors="ignore")
                lines = buf.split("\n"); buf = lines.pop()
                if first and ranged:
                    lines = lines[1:]  # drop the partial first line
                first = False
                for line in lines:
                    parts = line.strip().split(",")
                    if len(parts) < 4 or not parts[0][:1].isdigit():
                        continue
                    try:
                        lon, lat = float(parts[0]), float(parts[1])
                    except ValueError:
                        continue
                    if w <= lon <= e and s <= lat <= n:
                        wr.writerow(parts[:6]); kept += 1
    print(f"[ingest]   velocity slice: {kept:,} points in bbox -> {dest.name}")


def gnss() -> None:
    meta = requests.get(config.ZENODO_GNSS_API, headers=config.BROWSER_UA, timeout=60).json()
    for f in meta.get("files", []):
        _get(f["links"]["self"], RAW / "gnss" / f["key"])


def dem() -> None:
    for url in config.GLO30_TILES:
        _get(url, RAW / "dem" / url.rsplit("/", 1)[-1])


def population() -> None:
    _get(config.WORLDPOP_URL, RAW / "population" / "idn_ppp_2020_constrained.tif")
    _get(config.GHSL_URL, RAW / "population" / "GHS_POP_E2020_R10_C29.zip")
    _get(config.GHSL_BUILT_URL, RAW / "population" / "GHS_BUILT_S_E2020_R10_C29.zip")


def arcgis_all(layer: str, dest: Path) -> None:
    """Paginate an ArcGIS FeatureServer layer to a single GeoJSON."""
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[ingest]   {dest.name} cached"); return
    dest.parent.mkdir(parents=True, exist_ok=True)
    import time

    feats, offset, page_size = [], 0, 500   # small pages: the server drops big polygon responses
    while True:
        for attempt in range(4):
            try:
                r = requests.get(f"{layer}/query", params={
                    "where": "1=1", "outFields": "*", "f": "geojson", "outSR": 4326,
                    "resultOffset": offset, "resultRecordCount": page_size},
                    headers=config.BROWSER_UA, timeout=180)
                r.raise_for_status()
                page = r.json()
                break
            except (requests.RequestException, ValueError) as err:
                if attempt == 3:
                    raise
                time.sleep(3 * (attempt + 1))
        got = page.get("features", [])
        feats.extend(got)
        print(f"[ingest]     {dest.name}: {len(feats):,} so far", flush=True)
        if len(got) < page_size:
            break
        offset += len(got)
    dest.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"[ingest]   {dest.name}: {len(feats):,} features")


def admin() -> None:
    arcgis_all(config.KELURAHAN_LAYER, RAW / "admin" / "kelurahan_dki.geojson")


def floods() -> None:
    errors = []
    for fn in (
        lambda: arcgis_all(config.FLOOD_HISTORY_LAYER, RAW / "floods" / "bpbd_flood_history_2021_2024.geojson"),
        lambda: _get(config.UNOSAT_2020_URL, RAW / "floods" / "unosat_FL20200101IDN_shp.zip"),
        lambda: _get(config.EOS_2020_URL, RAW / "floods" / "eos_aria_20200102_fpm_shp.zip"),
    ):
        try:
            fn()
        except Exception as err:  # noqa: BLE001 — each flood source is independent
            errors.append(f"{type(err).__name__}: {str(err)[:120]}")
    if errors:
        raise RuntimeError("; ".join(errors))


def main() -> int:
    stages = {"velocity": velocity, "gnss": gnss, "dem": dem, "population": population,
              "admin": admin, "floods": floods}
    want = sys.argv[1:] or list(stages)
    failures = []
    for name in want:
        print(f"[ingest] {name}")
        try:
            stages[name]()
        except Exception as err:  # noqa: BLE001 — independent sources, keep going
            failures.append(name); print(f"[ingest]   {name} FAILED: {type(err).__name__}: {str(err)[:200]}")
    print(f"[ingest] done — failures: {failures or 'none'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
