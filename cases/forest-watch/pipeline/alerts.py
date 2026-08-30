"""Stage 2 - alerts.  RADD ``date_conf`` pixels -> disturbance clusters, with the commodity
context sampled in the same pass (it is free while the pixel index is in hand).

Decoding: value = confidence * 10000 + days since 2014-12-31 (2 = low, 3 = high; 0 = none).

Memory discipline: a 10-degree RADD tile is 100000 x 100000 px (20 GB as uint16), so it is
never opened whole.  Each tile is walked in 10000 x 10000 blocks (200 MB read + 400 MB label
array, ~1 GB peak) and the alert pixels of a block are pulled out as a sparse index.  Clusters
that straddle a block seam would otherwise be double-counted, so block edges are stitched with
a union-find over global label ids: the right column of the previous block and the bottom row
of the block above are carried forward and unioned against the current block's first column /
first row over the three 8-connected offsets.  Nothing is approximated away.

Every other layer sits on the same 10-degree grid, so the co-located window is a pure integer
scale: palm (SDPT simpleType) is 10 m like RADD; peat, primary forest and GLAD-L are 30 m,
i.e. exactly 2.5x coarser, indexed with (i * 2) // 5.

No tree-cover mask is applied.  RADD is already a forest-disturbance product, and gate G-H2
compares our counts against the GFW API's own aggregation over the same geometry, which is
also unmasked - masking here would compare unlike with unlike.  Stated in the methodology.

Output: data/alerts/<tile>.parquet, one row per cluster (>= 0.5 ha).
"""

from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from scipy import ndimage

import config

BLOCK = config.BLOCK_PX
STRUCT = np.ones((3, 3), dtype=bool)          # 8-connectivity
EPOCH = np.datetime64(config.ALERT_EPOCH)
DEG = config.TILE_DEG


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Union:
    """Tiny union-find over global label ids."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, a: int) -> int:
        p = self.parent
        root = a
        while p.get(root, root) != root:
            root = p[root]
        while p.get(a, a) != a:                # path compression
            p[a], a = root, p[a]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def tile_origin(tile: str) -> tuple[float, float]:
    """'10S_120E' -> (west, north) in degrees."""
    ns, ew = tile.split("_")
    lat = int(ns[:-1]) * (1 if ns[-1] == "N" else -1)
    lon = int(ew[:-1]) * (1 if ew[-1] == "E" else -1)
    return float(lon), float(lat)


def px_area_ha(lat_deg: np.ndarray, px_deg: float) -> np.ndarray:
    """Geodetic pixel area in hectares for a square pixel of ``px_deg`` at these latitudes."""
    m_per_deg = 111_320.0
    return (px_deg * m_per_deg) ** 2 * np.cos(np.radians(lat_deg)) / 10_000.0


def _open(layer: str, tile: str):
    p = config.RAW / layer / f"{tile}.tif"
    return rasterio.open(p) if p.exists() else None


def _seam(u: Union, a: np.ndarray, b: np.ndarray) -> None:
    """Union two parallel 1-px strips (global ids, 0 = background) over 8-connected offsets."""
    n = min(a.size, b.size)
    a, b = a[:n], b[:n]
    for off in (-1, 0, 1):
        aa = a[max(0, -off): n - max(0, off)]
        bb = b[max(0, off): n - max(0, -off)]
        both = (aa > 0) & (bb > 0)
        if not both.any():
            continue
        for x, y in zip(aa[both].tolist(), bb[both].tolist()):
            u.union(x, y)


def process_tile(tile: str) -> pd.DataFrame | None:
    radd = _open("radd", tile)
    if radd is None:
        return None
    west, north = tile_origin(tile)
    px10 = DEG / radd.width                                    # degrees per 10 m pixel
    palm, peat, prim, glad = (_open(k, tile) for k in ("palm", "peat", "primary", "glad"))
    palm_vals = np.array(load_palm_values(), dtype=np.uint8)

    u = Union()
    parts: list[pd.DataFrame] = []
    nblk = radd.width // BLOCK
    offset = 1
    prev_right: np.ndarray | None = None
    bottom_prev: dict[int, np.ndarray] = {}
    t0 = time.time()

    for br in range(nblk):
        prev_right = None
        for bc in range(nblk):
            win = Window(bc * BLOCK, br * BLOCK, BLOCK, BLOCK)
            arr = radd.read(1, window=win)
            mask = arr > 0
            npx = int(mask.sum())
            if npx == 0:
                prev_right = None
                bottom_prev.pop(bc, None)
                del arr, mask
                continue

            lab, nlab = ndimage.label(mask, structure=STRUCT)
            lab = lab.astype(np.int64)
            lab[mask] += offset - 1                            # 0 stays background
            left_col, right_col = lab[:, 0].copy(), lab[:, -1].copy()
            top_row, bot_row = lab[0, :].copy(), lab[-1, :].copy()

            flat = np.flatnonzero(mask.ravel())
            lab_f = lab.ravel()[flat]
            val_f = arr.ravel()[flat].astype(np.int32)
            del arr, mask, lab
            rr = (flat // BLOCK).astype(np.int32)              # block-local row / col
            cc = (flat % BLOCK).astype(np.int32)
            del flat

            lat = north - (br * BLOCK + rr + 0.5) * px10       # pixel centres, degrees
            d = {"lab": lab_f,
                 "day": (val_f % 10000).astype(np.int32),
                 "hi": (val_f // 10000) >= config.ALERT_CONF_HIGH,
                 "ha": px_area_ha(lat, px10).astype(np.float32),
                 "lon": west + (bc * BLOCK + cc + 0.5) * px10,
                 "lat": lat}
            del val_f

            # --- co-located context, same 10-degree grid ------------------------------
            n30 = (BLOCK * 2) // 5                             # 30 m block edge = 4000 px
            w30 = Window((bc * BLOCK * 2) // 5, (br * BLOCK * 2) // 5, n30, n30)
            r5, c5 = (rr.astype(np.int64) * 2) // 5, (cc.astype(np.int64) * 2) // 5
            if palm is not None and palm_vals.size:            # SDPT simpleName, 30 m
                a30 = palm.read(1, window=w30)
                d["palm"] = np.isin(a30[np.clip(r5, 0, a30.shape[0] - 1),
                                        np.clip(c5, 0, a30.shape[1] - 1)], palm_vals)
                del a30
            else:
                d["palm"] = np.zeros(npx, dtype=bool)
            for key, ds in (("peat", peat), ("primary", prim)):
                if ds is None:
                    d[key] = np.zeros(npx, dtype=bool)
                    continue
                a30 = ds.read(1, window=w30)
                d[key] = a30[np.clip(r5, 0, a30.shape[0] - 1),
                             np.clip(c5, 0, a30.shape[1] - 1)] > 0
                del a30
            if glad is not None:
                g30 = glad.read(1, window=w30).astype(np.int32)
                gv = g30[np.clip(r5, 0, g30.shape[0] - 1), np.clip(c5, 0, g30.shape[1] - 1)]
                d["glad_day"] = np.where(gv > 0, gv % 10000, -1).astype(np.int32)
                del g30, gv
            else:
                d["glad_day"] = np.full(npx, -1, dtype=np.int32)
            del rr, cc, r5, c5, lat

            df = pd.DataFrame(d)
            g = df.groupby("lab", sort=False)
            agg = g.agg(px=("day", "size"), ha=("ha", "sum"),
                        day_min=("day", "min"), day_max=("day", "max"),
                        hi_px=("hi", "sum"), lon=("lon", "mean"), lat=("lat", "mean"),
                        palm_px=("palm", "sum"), peat_px=("peat", "sum"),
                        primary_px=("primary", "sum")).reset_index()
            gl = df.loc[df.glad_day >= 0].groupby("lab")["glad_day"]
            if len(gl):
                agg = agg.merge(gl.agg(glad_min="min", glad_max="max",
                                       glad_px="size").reset_index(), on="lab", how="left")
            else:
                agg[["glad_min", "glad_max", "glad_px"]] = np.nan
            del df, d, g

            edge = set(np.unique(np.concatenate([left_col, right_col, top_row,
                                                 bot_row])).tolist()) - {0}
            keep = agg.px.ge(5) | agg.lab.isin(edge)
            parts.append(agg.loc[keep])

            if prev_right is not None:
                _seam(u, prev_right, left_col)
            if bc in bottom_prev:
                _seam(u, bottom_prev[bc], top_row)
            prev_right = right_col
            bottom_prev[bc] = bot_row
            offset += nlab
        log(f"    {tile} row {br+1}/{nblk} — {sum(len(p) for p in parts)} raw parts, "
            f"{time.time()-t0:.0f}s")

    for ds in (radd, palm, peat, prim, glad):
        if ds is not None:
            ds.close()
    if not parts:
        return None

    raw = pd.concat(parts, ignore_index=True)
    del parts
    raw["root"] = [u.find(int(x)) for x in raw.lab.to_numpy()]
    w = raw.px.astype(np.float64)
    raw["lonw"], raw["latw"] = raw.lon * w, raw.lat * w
    out = raw.groupby("root").agg(
        px=("px", "sum"), ha=("ha", "sum"), day_min=("day_min", "min"),
        day_max=("day_max", "max"), hi_px=("hi_px", "sum"), lonw=("lonw", "sum"),
        latw=("latw", "sum"), palm_px=("palm_px", "sum"), peat_px=("peat_px", "sum"),
        primary_px=("primary_px", "sum"), glad_min=("glad_min", "min"),
        glad_max=("glad_max", "max"), glad_px=("glad_px", "sum")).reset_index()
    out = out.loc[out.px >= config.MIN_CLUSTER_PX].copy()
    out["lon"] = out.lonw / out.px
    out["lat"] = out.latw / out.px
    out.drop(columns=["lonw", "latw"], inplace=True)
    out["tile"] = tile
    out["cluster_id"] = tile + "_" + out.root.astype(str)
    out["first_date"] = (EPOCH + out.day_min.to_numpy().astype("timedelta64[D]"))
    out["last_date"] = (EPOCH + out.day_max.to_numpy().astype("timedelta64[D]"))
    out["hi_share"] = (out.hi_px / out.px).astype(np.float32)
    out["palm_share"] = (out.palm_px / out.px).astype(np.float32)
    out["peat_share"] = (out.peat_px / out.px).astype(np.float32)
    out["primary_share"] = (out.primary_px / out.px).astype(np.float32)
    gmin, gmax = out.glad_min.to_numpy(), out.glad_max.to_numpy()
    lo, hi = out.day_min.to_numpy(), out.day_max.to_numpy()
    D = config.GLAD_AGREEMENT_DAYS
    with np.errstate(invalid="ignore"):
        out["glad_agree"] = np.where(np.isnan(gmin), False,
                                     (gmin <= hi + D) & (gmax >= lo - D))
    return out.drop(columns=["root"])


def load_palm_values() -> list[int]:
    if config.PALM_CLASS_FILE.exists():
        try:
            v = json.loads(config.PALM_CLASS_FILE.read_text()).get("palm_values")
            if v:
                return [int(x) for x in v]
        except Exception:                                          # noqa: BLE001
            pass
    return list(config.PALM_CLASSES_DEFAULT)


def main(argv: list[str]) -> None:
    config.ALERTS_DIR.mkdir(parents=True, exist_ok=True)
    only = argv[argv.index("--tiles") + 1].split(",") if "--tiles" in argv else config.TILES_IDN
    pv = load_palm_values()
    if not pv:
        log("FATAL: no calibrated oil-palm raster class — run "
            "`uv run python pipeline/ingest.py --calibrate-palm` first. Refusing to run with a "
            "silently empty palm layer.")
        raise SystemExit(2)
    log("palm raster values treated as oil palm:", pv)
    for tile in only:
        out = config.ALERTS_DIR / f"{tile}.parquet"
        if out.exists():
            log(f"[{tile}] done, skipping")
            continue
        if not (config.RAW / "radd" / f"{tile}.tif").exists():
            log(f"[{tile}] no RADD tile — skipping")
            continue
        if not config.disk_ok(need_gb=0.2):
            log("DISK GUARD — stopping cleanly")
            raise SystemExit(0)
        log(f"[{tile}] clustering")
        t0 = time.time()
        df = process_tile(tile)
        if df is None or df.empty:
            pd.DataFrame().to_parquet(out)
            log(f"[{tile}] no clusters")
            continue
        df.to_parquet(out, index=False)
        log(f"[{tile}] {len(df):,} clusters, {df.ha.sum():,.0f} ha, "
            f"{time.time()-t0:.0f}s")
    log("alerts complete")


if __name__ == "__main__":
    main(sys.argv[1:])
