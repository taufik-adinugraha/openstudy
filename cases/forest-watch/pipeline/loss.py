"""Stage 2b - loss.  Two decades of Hansen tree-cover loss, per province per year.

Uses ``umd_tree_cover_loss`` v1.13's ``year__tcd30_2000`` tile set, which GFW publishes
already masked to >= 30 % canopy in 2000 - the same forest definition its own country
statistics use.  We therefore reconcile like with like in G-H1, and skip ~5 GB of Hansen
treecover2000 / datamask downloads.  (Decision logged in the README.)

Walked in 10000 x 10000 blocks at 30 m (16 per 10-degree tile, ~100 MB per read).  Provinces
are rasterised into each block, so the hectare total respects the true geodetic pixel area
(cos-latitude weighted) rather than a nominal 0.09 ha.
"""

from __future__ import annotations

import sys
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window

import config
from alerts import log, px_area_ha, tile_origin

# 5000 px at 30 m = 64 blocks per 10-degree tile.  10000 px OOM-killed the 3 GB unit: the
# int32 province raster alone is 400 MB at that size, before the loss array and the index
# arrays. At 5000 the whole working set is ~150 MB.
BLOCK30 = 5_000


def main(argv: list[str]) -> None:
    prov = gpd.read_parquet(config.BOUNDARIES).reset_index(drop=True)
    prov["pid"] = np.arange(1, len(prov) + 1, dtype=np.int32)
    names = dict(zip(prov.pid, prov.province))
    acc: dict[tuple[int, int], float] = {}
    year_base: int | None = None

    for tile in config.TILES_IDN:
        p = config.RAW / "tcl30" / f"{tile}.tif"
        if not p.exists():
            log(f"[{tile}] no tcl30 tile")
            continue
        west, north = tile_origin(tile)
        with rasterio.open(p) as ds:
            px = config.TILE_DEG / ds.width
            nb = ds.width // BLOCK30
            tb = prov.cx[west:west + config.TILE_DEG, north - config.TILE_DEG:north]
            if tb.empty:
                continue
            t0 = time.time()
            for br in range(nb):
                for bc in range(nb):
                    win = Window(bc * BLOCK30, br * BLOCK30, BLOCK30, BLOCK30)
                    arr = ds.read(1, window=win)
                    if not arr.any():
                        continue
                    if year_base is None:
                        year_base = 0 if int(arr.max()) > 100 else 2000
                    tr = rasterio.transform.from_origin(
                        west + bc * BLOCK30 * px, north - br * BLOCK30 * px, px, px)
                    sub = tb.cx[west + bc * BLOCK30 * px:west + (bc + 1) * BLOCK30 * px,
                                north - (br + 1) * BLOCK30 * px:north - br * BLOCK30 * px]
                    if sub.empty:
                        continue
                    pr = rasterize(zip(sub.geometry, sub.pid), out_shape=arr.shape,
                                   transform=tr, fill=0, dtype="uint8")   # <= 255 provinces
                    sel = (arr > 0) & (pr > 0)
                    if not sel.any():
                        del arr, pr, sel
                        continue
                    rr, _ = np.nonzero(sel)                    # only as long as the hit count
                    lat = north - (br * BLOCK30 + rr + 0.5) * px
                    ha = px_area_ha(lat, px)
                    key = pr[sel].astype(np.int32) * np.int32(1000) + arr[sel].astype(np.int32)
                    del arr, pr, sel, rr, lat
                    dfb = pd.DataFrame({"k": key, "ha": ha}).groupby("k")["ha"].sum()
                    for k, v in dfb.items():
                        pid, yv = divmod(int(k), 1000)
                        acc[(pid, yv)] = acc.get((pid, yv), 0.0) + float(v)
                    del ha, key, dfb
                log(f"    {tile} row {br+1}/{nb} ({time.time()-t0:.0f}s)")
    if not acc:
        log("no loss accumulated")
        return
    base = year_base if year_base is not None else 2000
    df = pd.DataFrame([{"province": names[pid], "year": base + yv, "loss_ha": ha}
                       for (pid, yv), ha in acc.items()])
    df = df.loc[df.year.between(2001, 2030)].sort_values(["province", "year"])
    df.to_parquet(config.LOSS_TABLE, index=False)
    log(f"loss: {len(df)} province-year rows, national 2023 = "
        f"{df.loc[df.year == 2023, 'loss_ha'].sum():,.0f} ha, 2024 = "
        f"{df.loc[df.year == 2024, 'loss_ha'].sum():,.0f} ha")


if __name__ == "__main__":
    main(sys.argv[1:])
