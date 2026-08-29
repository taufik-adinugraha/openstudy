"""Stage: ingest (spec §A3, source per decision D12).

Download the month's Black Marble tiles from LAADS (Earthdata bearer token):
VJ146A3 from 2018-01, VNP46A3 for earlier months — both during the overlap
era, which feeds the splice inter-calibration (gate G-A4a). HDF5 tiles are
mosaicked, clipped to the Indonesia bbox, and written as COGs under
data/raw/bm/YYYY-MM/. Ocean-only tiles simply don't exist in the archive.

Clean-exit rule: if LAADS has not published the month yet, exit 0 with
NOT_PUBLISHED so the cron run is a no-op, not a failure.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import requests

import config

GRID_PATH = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields"
LAYERS = {
    "radiance": "NearNadir_Composite_Snow_Free",
    "nobs": "NearNadir_Composite_Snow_Free_Num",
    "quality": "NearNadir_Composite_Snow_Free_Quality",
}
TILE_SIZE = 2400          # 10 deg / 15 arcsec
TILE_DEG = 10.0
VNP46A3_END = "2026-10"   # Suomi-NPP production ceases 2026-11-01


HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"


def laads_headers() -> dict[str, str]:
    # LAADS file downloads require a LAADS App Key (ladsweb profile → App Keys);
    # a plain EDL token gets bounced to the login/license page.
    key = os.environ.get("LAADS_APP_KEY") or os.environ["EARTHDATA_TOKEN"]
    return {"Authorization": f"Bearer {key}"}


def _is_hdf5(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(8) == HDF5_MAGIC
    except OSError:
        return False


def products_for(month: str) -> tuple[str, ...]:
    if month < config.SPLICE_START:
        return ("VNP46A3",)
    if month <= VNP46A3_END:
        return ("VJ146A3", "VNP46A3")  # overlap era: both feed the splice calibration
    return ("VJ146A3",)


def month_listing(month: str, product: str) -> list[dict]:
    year, mm = month.split("-")
    doy = date(int(year), int(mm), 1).timetuple().tm_yday
    url = f"{config.LAADS_BASE}/{product}/{year}/{doy:03d}.json"
    resp = requests.get(url, headers=laads_headers(), timeout=60)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    payload = resp.json()
    entries = payload.get("content", payload) if isinstance(payload, dict) else payload
    return [e for e in entries if isinstance(e, dict) and str(e.get("name", "")).endswith(".h5")]


def download(month: str, product: str, tiles: tuple[str, ...], raw_dir: Path) -> dict[str, Path]:
    year, mm = month.split("-")
    doy = date(int(year), int(mm), 1).timetuple().tm_yday
    listing = month_listing(month, product)
    got: dict[str, Path] = {}
    for entry in listing:
        name = str(entry["name"]).split("/")[-1]
        match = re.search(r"\.(h\d{2}v\d{2})\.", name)
        if not match or match.group(1) not in tiles:
            continue
        tile = match.group(1)
        dest = raw_dir / "h5" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        want = int(entry.get("size", 0) or 0)
        if dest.exists() and _is_hdf5(dest) and (want == 0 or dest.stat().st_size == want):
            print(f"[ingest]   {name} cached")
            got[tile] = dest
            continue
        url = f"{config.LAADS_BASE}/{product}/{year}/{doy:03d}/{name}"
        for attempt in range(3):
            try:
                with requests.get(url, headers=laads_headers(), stream=True, timeout=(10, 300)) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1 << 20):
                            f.write(chunk)
                break
            except requests.RequestException as err:
                if attempt == 2:
                    raise
                print(f"[ingest]   retry {name}: {err}")
        if not _is_hdf5(dest):
            dest.unlink()
            raise RuntimeError(
                f"{name}: server returned a non-HDF5 body (login/license page). Fix: "
                "log into ladsweb.modaps.eosdis.nasa.gov once in a browser, download any "
                "VJ146A3 file to accept the data license, then set LAADS_APP_KEY in .env "
                "from Profile → App Keys."
            )
        print(f"[ingest]   {name} ({dest.stat().st_size / 1e6:.0f} MB)")
        got[tile] = dest
    return got


def _attr(ds, *names, default=None):
    for n in names:
        if n in ds.attrs:
            return np.atleast_1d(ds.attrs[n])[0]
    return default


def read_layer(h5path: Path, field: str) -> tuple[np.ndarray, float, float, float | None]:
    import h5py

    with h5py.File(h5path, "r") as f:
        key = f"{GRID_PATH}/{field}"
        if key not in f:
            found: list[str] = []
            f.visit(lambda k: found.append(k) if k.endswith(field) else None)
            if not found:
                raise KeyError(f"{field} not in {h5path.name}; try h5py .visit() to inspect")
            key = found[0]
        ds = f[key]
        arr = ds[...]
        scale = float(_attr(ds, "scale_factor", default=1.0))
        offset = float(_attr(ds, "add_offset", "offset", default=0.0))
        fill = _attr(ds, "_FillValue")
        return arr, scale, offset, (float(fill) if fill is not None else None)


def mosaic_and_clip(files: dict[str, Path], field: str, as_radiance: bool) -> tuple[np.ndarray, object]:
    from rasterio.transform import from_origin

    hs = sorted({int(t[1:3]) for t in files})
    vs = sorted({int(t[4:6]) for t in files})
    res = TILE_DEG / TILE_SIZE
    west, north = -180.0 + hs[0] * TILE_DEG, 90.0 - vs[0] * TILE_DEG
    grid = np.full((len(vs) * TILE_SIZE, len(hs) * TILE_SIZE), np.nan, dtype="float64")

    for tile, path in files.items():
        arr, scale, offset, fill = read_layer(path, field)
        arr = arr.astype("float64")
        if fill is not None:
            arr[arr == fill] = np.nan
        arr = arr * scale + offset
        r0 = (int(tile[4:6]) - vs[0]) * TILE_SIZE
        c0 = (int(tile[1:3]) - hs[0]) * TILE_SIZE
        grid[r0:r0 + TILE_SIZE, c0:c0 + TILE_SIZE] = arr

    # crop to bbox ∩ mosaic
    w, s, e, n = config.BBOX
    col0 = max(0, int((w - west) / res))
    col1 = min(grid.shape[1], int(np.ceil((e - west) / res)))
    row0 = max(0, int((north - n) / res))
    row1 = min(grid.shape[0], int(np.ceil((north - s) / res)))
    clipped = grid[row0:row1, col0:col1]
    transform = from_origin(west + col0 * res, north - row0 * res, res, res)
    return (clipped.astype("float32") if as_radiance else clipped, transform)


def write_cog(arr: np.ndarray, transform, out: Path, dtype: str, nodata) -> None:
    import rasterio

    out.parent.mkdir(parents=True, exist_ok=True)
    profile = dict(
        driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
        dtype=dtype, crs="EPSG:4326", transform=transform, nodata=nodata,
        tiled=True, compress="deflate", predictor=2 if dtype != "float32" else 3,
    )
    data = arr if dtype == "float32" else np.nan_to_num(arr, nan=nodata).astype(dtype)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data, 1)
        dst.build_overviews([2, 4, 8, 16])


def write_preview(radiance: np.ndarray, out: Path) -> None:
    """Log-scaled amber render (case palette #E8A33D) — a look at the data."""
    from PIL import Image

    v = np.nan_to_num(radiance, nan=0.0)
    v = np.clip(v, 0, None)
    top = np.nanpercentile(v[v > 0], 99.5) if (v > 0).any() else 1.0
    norm = np.log1p(v) / np.log1p(top)
    norm = np.clip(norm, 0, 1)
    rgb = np.stack([norm, norm * 0.64, norm * 0.24], axis=-1)  # sodium amber
    img = (rgb * 255).astype("uint8")
    step = max(1, img.shape[0] // 2000)  # keep previews reasonable
    Image.fromarray(img[::step, ::step]).save(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--products", help="comma-separated override (default: era-appropriate)")
    parser.add_argument("--tiles", help="comma-separated override (default: all Indonesia tiles)")
    parser.add_argument("--preview", action="store_true", help="write an amber PNG preview")
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}", args.month):
        parser.error("--month must be YYYY-MM")

    tiles = tuple(args.tiles.split(",")) if args.tiles else config.TILES
    products = tuple(args.products.split(",")) if args.products else products_for(args.month)
    raw_dir = config.DATA_DIR / "raw" / "bm" / args.month
    wrote_any = False

    for product in products:
        print(f"[ingest] {args.month} {product}: listing …")
        files = download(args.month, product, tiles, raw_dir)
        if not files:
            print(f"[ingest] NOT_PUBLISHED: no {product} tiles for {args.month} — clean exit")
            continue
        print(f"[ingest] {len(files)} tiles: {', '.join(sorted(files))}")
        radiance = None
        for layer, field in LAYERS.items():
            as_rad = layer == "radiance"
            arr, transform = mosaic_and_clip(files, field, as_radiance=as_rad)
            out = raw_dir / f"{product}_{layer}.tif"
            if as_rad:
                write_cog(arr, transform, out, "float32", nodata=np.nan)
                radiance = arr
                valid = arr[~np.isnan(arr)]
                print(f"[ingest]   {out.name}: {arr.shape[1]}x{arr.shape[0]} px, "
                      f"valid {valid.size / arr.size:.0%}, mean {valid.mean():.2f}, "
                      f"p99 {np.percentile(valid, 99):.1f} nW/cm²/sr")
            else:
                write_cog(arr, transform, out, "uint16", nodata=65535)
                print(f"[ingest]   {out.name} written")
        if args.preview and radiance is not None:
            png = raw_dir / f"{product}_preview.png"
            write_preview(radiance, png)
            print(f"[ingest]   preview: {png}")
        wrote_any = True

    print(f"[ingest] done — {'data written' if wrote_any else 'nothing published yet'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
