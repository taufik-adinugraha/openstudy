"""Stages: mask + zonal (spec §A3).

mask : null cells below the observation-count threshold (composite QA);
       clamp negatives; write the masked radiance as a derived COG.
       TODO week 2: quality-flag semantics + VNF gas-flare buffers.
zonal: exactextract sum-of-lights / mean radiance / valid-pixel count per
       regency against the frozen geoBoundaries-2020 polygons; upsert one row
       per (month, product, region) into the parquet ledger — idempotent:
       re-running a month replaces exactly its own rows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

import config


def apply_masks(month: str, product: str) -> tuple[Path, dict]:
    import rasterio

    raw = config.DATA_DIR / "raw" / "bm" / month
    out_dir = config.DATA_DIR / "derived" / "bm" / month
    out_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(raw / f"{product}_radiance.tif") as src:
        radiance = src.read(1)
        profile = src.profile
    with rasterio.open(raw / f"{product}_nobs.tif") as src:
        nobs = src.read(1)

    valid_before = int(np.isfinite(radiance).sum())
    radiance[nobs < config.MIN_NUM_OBS] = np.nan
    radiance = np.clip(radiance, 0, None)
    masked_share = 1.0 - (np.isfinite(radiance).sum() / max(valid_before, 1))

    out = out_dir / f"{product}_radiance_masked.tif"
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(radiance, 1)
        dst.build_overviews([2, 4, 8, 16])
    return out, {"masked_share": float(masked_share)}


def zonal_stats(month: str, product: str, masked_tif: Path):
    import geopandas as gpd
    import pandas as pd
    from exactextract import exact_extract

    gdf = gpd.read_file(config.BOUNDARIES)[[config.REGION_ID, config.REGION_NAME, "geometry"]]
    res = exact_extract(str(masked_tif), gdf, ["sum", "mean", "count"], output="pandas")
    stats = pd.DataFrame({
        "region_id": gdf[config.REGION_ID].values,
        "region_name": gdf[config.REGION_NAME].values,
        "sol": res["sum"].values,          # sum of lights (nW/cm²/sr · px)
        "mean_rad": res["mean"].values,
        "n_px": res["count"].values,
        "month": month,
        "product": product,
    })

    # idempotent upsert into the ledger
    if config.LEDGER.exists():
        ledger = pd.read_parquet(config.LEDGER)
        ledger = ledger[~((ledger["month"] == month) & (ledger["product"] == product))]
        ledger = pd.concat([ledger, stats], ignore_index=True)
    else:
        ledger = stats
    config.LEDGER.parent.mkdir(parents=True, exist_ok=True)
    ledger.to_parquet(config.LEDGER, index=False)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--product", default="VJ146A3")
    parser.add_argument("--top", type=int, default=0, help="print top-N regencies by SOL")
    args = parser.parse_args()

    print(f"[zonal] month={args.month} product={args.product} "
          f"boundaries={config.BOUNDARY_VINTAGE}")
    masked, health = apply_masks(args.month, args.product)
    print(f"[zonal] masked share: {health['masked_share']:.1%} -> {masked.name}")
    stats = zonal_stats(args.month, args.product, masked)
    print(f"[zonal] {len(stats)} regency rows upserted into {config.LEDGER.name}")

    if args.top:
        top = stats.nlargest(args.top, "sol")[["region_name", "sol", "mean_rad"]]
        print(f"[zonal] top {args.top} by sum-of-lights:")
        for _, row in top.iterrows():
            print(f"    {row.region_name:<28} SOL {row.sol:>12,.0f}   mean {row.mean_rad:6.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
