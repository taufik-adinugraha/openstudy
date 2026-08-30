"""Annual hero frames for the nightlights timelapse.

Reads the annual Black Marble composites ingested per year into
data/raw/bm/YYYY-01/{VNP46A4|VJ146A4}_radiance.tif (see ~/annual-frames.sh on
the dev server), renders each with ONE fixed log stretch — the per-image
percentile stretch in ingest.write_preview makes years look identical — and
block-max downsamples so small towns survive at hero resolution.

Writes web/public/frames/YYYY.webp + index.json. Rerun any time; years whose
source raster is missing are skipped, so the hero grows as years land.

Sensor note: 2012–2017 are Suomi-NPP (VNP46A4), 2018→ NOAA-20 (VJ146A4). The
two are cross-calibrated by NASA but not identical — the hero shows years, not
numbers, and the methodology footer discloses the splice.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import config

FRAMES = config.CASE_DIR / "web" / "public" / "frames"
RAW_BM = config.DATA_DIR / "raw" / "bm"
STRETCH_TOP = 60.0   # nW/cm²/sr — ramp ceiling; Jakarta's core saturates, towns stay visible
BLOCK = 4            # 15-arcsecond pixels per block → ~1.85 km block-max
LIT_THRESHOLD = 0.5  # nW/cm²/sr — "lit pixel" for the index stats
GROUND = np.array([5, 7, 15], dtype="float32") / 255  # case midnight indigo


def product_for(year: int) -> str:
    return "VJ146A4" if year >= 2018 else "VNP46A4"


def source(year: int) -> Path:
    return RAW_BM / f"{year}-01" / f"{product_for(year)}_radiance.tif"


def block_max(a: np.ndarray, k: int) -> np.ndarray:
    h, w = a.shape
    h2, w2 = h - h % k, w - w % k
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN blocks are expected (sea)
        return np.nanmax(a[:h2, :w2].reshape(h2 // k, k, w2 // k, k), axis=(1, 3))


def render(rad: np.ndarray) -> np.ndarray:
    v = np.clip(np.nan_to_num(rad, nan=0.0), 0, None)
    norm = np.clip(np.log1p(v) / np.log1p(STRETCH_TOP), 0, 1).astype("float32")
    core = np.clip((norm - 0.72) / 0.28, 0, 1)  # brightest cores warm towards white
    r = norm
    g = norm * 0.64 + core * 0.36
    b = norm * 0.24 + core * 0.62
    rgb = np.stack([r, g, b], axis=-1)
    out = GROUND * (1 - norm[..., None]) + np.clip(rgb, 0, 1)
    return (np.clip(out, 0, 1) * 255).astype("uint8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", default="2012-2025", help="range YYYY-YYYY or comma list")
    parser.add_argument("--force", action="store_true", help="re-render even if the frame exists")
    args = parser.parse_args()
    if "-" in args.years:
        a, b = args.years.split("-")
        years = list(range(int(a), int(b) + 1))
    else:
        years = [int(y) for y in args.years.split(",")]

    import rasterio
    from PIL import Image

    FRAMES.mkdir(parents=True, exist_ok=True)
    index_path = FRAMES / "index.json"
    entries = {}
    if index_path.exists():
        entries = {e["year"]: e for e in json.loads(index_path.read_text())["years"]}

    for year in years:
        src = source(year)
        out = FRAMES / f"{year}.webp"
        if not src.exists():
            print(f"[frames] {year}: no composite yet ({src.name}) — skipped")
            continue
        if out.exists() and not args.force and year in entries:
            print(f"[frames] {year}: exists")
            continue
        with rasterio.open(src) as ds:
            rad = ds.read(1, masked=True).filled(np.nan).astype("float32")
        lit_px = int(np.count_nonzero(rad > LIT_THRESHOLD))
        total = float(np.nansum(np.clip(rad, 0, None)))
        img = render(block_max(rad, BLOCK))
        Image.fromarray(img).save(out, format="WEBP", quality=80, method=6)
        entries[year] = {"year": year, "product": product_for(year), "file": out.name,
                         "litPx": lit_px, "sum": round(total, 1),
                         "w": int(img.shape[1]), "h": int(img.shape[0])}
        print(f"[frames] {year}: {out.name} {img.shape[1]}×{img.shape[0]} "
              f"{out.stat().st_size / 1024:.0f} KB · lit px {lit_px:,}")
        del rad, img

    index = {
        "years": [entries[y] for y in sorted(entries)],
        "stretchTop": STRETCH_TOP, "block": BLOCK, "litThreshold": LIT_THRESHOLD,
        "bbox": list(config.BBOX),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
    }
    index_path.write_text(json.dumps(index, indent=1, allow_nan=False))
    print(f"[frames] index: {len(index['years'])} years → {index_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
