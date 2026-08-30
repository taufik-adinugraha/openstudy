"""Stage 2 - alerts.  RADD ``date_conf`` pixels -> disturbance clusters, with the commodity
context sampled in the same pass (it is free while the pixel index is in hand).

Decoding: value = confidence * 10000 + days since 2014-12-31 (2 = low, 3 = high; 0 = none).

**Clustering is spatio-temporal, not purely spatial.**  Two alert pixels join the same event
only if they are 8-connected *and* their detection dates are within CLUSTER_WINDOW_DAYS of each
other.  Labelling on space alone is wrong in a way that is easy to miss and ruins every
downstream number: a frontier that creeps across the same hillside from 2020 to 2026 becomes one
component whose "first date" is 2020, which silently back-dates most of the archipelago's
hectares into the first year of the record.  We hit exactly that, and the reconciliation gate
against GFW's own aggregation is what caught it.

Implementation: the alert pixels of a block are extracted as a sparse index; candidate edges are
the four forward 8-neighbours, found by binary search on the sorted (row * BLOCK + col) keys and
then filtered on the date difference; components come from
``scipy.sparse.csgraph.connected_components``.  That is C-speed and needs no dense label array.

Memory discipline: a 10-degree RADD tile is 100000 x 100000 px (20 GB as uint16), so it is
never opened whole.  Each tile is walked in 10000 x 10000 blocks.  Clusters that straddle a
block seam would otherwise be double-counted, so block edges are stitched with a union-find
over global component ids: the right column of the previous block and the bottom row of the
block above are carried forward and unioned against the current block's first column / first
row over the three 8-connected offsets, under the same date rule.  Nothing is approximated away.

Every other layer sits on the same 10-degree grid, so the co-located window is a pure integer
scale: RADD is 10 m; palm (SDPT simpleName), peat, primary forest and GLAD-L are 30 m,
i.e. exactly 2.5x coarser, indexed with (i * 2) // 5.

No tree-cover mask is applied.  RADD is already a forest-disturbance product, and gate G-H2
compares our counts against the GFW API's own aggregation over the same geometry, which is
also unmasked - masking here would compare unlike with unlike.  Stated in the methodology.

Outputs: data/alerts/<tile>.parquet     one row per event (>= 0.5 ha)
         data/alerts/raw_<tile>.parquet unfiltered alert hectares on a
                                        0.05-degree x 1-week grid, the honest
                                        like-for-like comparator for gate G-H2
"""

from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

import config

BLOCK = config.BLOCK_PX
WIN = config.CLUSTER_WINDOW_DAYS
EPOCH = np.datetime64(config.ALERT_EPOCH)
DEG = config.TILE_DEG
NEIGHBOURS = ((0, 1), (1, -1), (1, 0), (1, 1))    # forward half of the 8-neighbourhood
RAW_GRID = 0.05                                   # degrees, the unfiltered-hectare grid


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


def _seam(u: Union, a: np.ndarray, ad: np.ndarray, b: np.ndarray, bd: np.ndarray) -> None:
    """Union two parallel 1-px strips of global ids (0 = background) over the three 8-connected
    offsets, under the same date rule that governs clustering inside a block."""
    n = min(a.size, b.size)
    for off in (-1, 0, 1):
        lo_a, lo_b = max(0, -off), max(0, off)
        hi = n - abs(off)
        aa, bb = a[lo_a:lo_a + hi], b[lo_b:lo_b + hi]
        both = (aa > 0) & (bb > 0)
        if not both.any():
            continue
        both &= np.abs(ad[lo_a:lo_a + hi].astype(np.int32)
                       - bd[lo_b:lo_b + hi].astype(np.int32)) <= WIN
        for x, y in zip(aa[both].tolist(), bb[both].tolist()):
            u.union(x, y)


def _strip(size: int, sel: np.ndarray, idx: np.ndarray, lab: np.ndarray,
           day: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Scatter the sparse pixels lying on one block edge into dense (label, day) strips."""
    out_l = np.zeros(size, dtype=np.int64)
    out_d = np.zeros(size, dtype=np.int32)
    if sel.any():
        out_l[idx[sel]] = lab[sel]
        out_d[idx[sel]] = day[sel]
    return out_l, out_d


def process_tile(tile: str) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    radd = _open("radd", tile)
    if radd is None:
        return None, None
    west, north = tile_origin(tile)
    px10 = DEG / radd.width                                    # degrees per 10 m pixel
    palm, peat, prim, glad = (_open(k, tile) for k in ("palm", "peat", "primary", "glad"))
    palm_vals = np.array(load_palm_values(), dtype=np.uint8)

    u = Union()
    parts: list[pd.DataFrame] = []
    raw_parts: list[pd.DataFrame] = []
    nblk = radd.width // BLOCK
    offset = 1
    prev_right: tuple[np.ndarray, np.ndarray] | None = None
    bottom_prev: dict[int, tuple[np.ndarray, np.ndarray]] = {}
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

            keys = np.flatnonzero(mask.ravel())                # sorted row-major pixel ids
            val_f = arr.ravel()[keys].astype(np.int32)
            del arr, mask
            rr = (keys // BLOCK).astype(np.int32)              # block-local row / col
            cc = (keys % BLOCK).astype(np.int32)
            day_f = (val_f % 10000).astype(np.int32)

            # --- spatio-temporal components -------------------------------------------
            ei, ej = [], []
            for dr, dc in NEIGHBOURS:
                nk = keys + dr * BLOCK + dc
                ok = (cc + dc >= 0) & (cc + dc < BLOCK) & (rr + dr < BLOCK)
                pos = np.searchsorted(keys, nk)
                np.clip(pos, 0, keys.size - 1, out=pos)
                hit = ok & (keys[pos] == nk)
                if not hit.any():
                    continue
                i = np.flatnonzero(hit).astype(np.int32)
                j = pos[hit].astype(np.int32)
                near = np.abs(day_f[i] - day_f[j]) <= WIN
                ei.append(i[near]); ej.append(j[near])
                del nk, ok, pos, hit, i, j, near
            if ei:
                i = np.concatenate(ei); j = np.concatenate(ej)
            else:
                i = j = np.empty(0, dtype=np.int32)
            del ei, ej
            g = coo_matrix((np.ones(i.size, dtype=np.int8), (i, j)),
                           shape=(npx, npx)).tocsr()
            del i, j
            nlab, comp = connected_components(g, directed=False)
            del g
            lab_f = comp.astype(np.int64) + offset
            del comp

            left_col, left_day = _strip(BLOCK, cc == 0, rr, lab_f, day_f)
            right_col, right_day = _strip(BLOCK, cc == BLOCK - 1, rr, lab_f, day_f)
            top_row, top_day = _strip(BLOCK, rr == 0, cc, lab_f, day_f)
            bot_row, bot_day = _strip(BLOCK, rr == BLOCK - 1, cc, lab_f, day_f)
            del keys

            lat = north - (br * BLOCK + rr + 0.5) * px10       # pixel centres, degrees
            d = {"lab": lab_f,
                 "day": day_f,
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
            # Raw alert hectares on a 0.05-degree x 1-week grid, BEFORE the 0.5 ha event floor.
            # G-H2 compares against GFW's aggregation of every alert pixel, so the event table
            # (which drops sub-0.5 ha detections by design) is the wrong comparator; this is the
            # right one, and the difference between the two is itself reported.
            raw_parts.append(
                df.assign(gx=np.floor((df.lon - config.BBOX_IDN[0]) / RAW_GRID).astype(np.int32),
                          gy=np.floor((config.BBOX_IDN[3] - df.lat) / RAW_GRID).astype(np.int32),
                          wk=(df.day // 7).astype(np.int32))
                  .groupby(["gx", "gy", "wk"], sort=False)
                  .agg(ha=("ha", "sum"), px=("ha", "size")).reset_index())
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
            # A component split by a seam necessarily touches a block edge, so keeping every
            # edge-touching component makes the >= 5 px pre-filter lossless for stitching.
            keep = agg.px.ge(5) | agg.lab.isin(edge)
            parts.append(agg.loc[keep])

            if prev_right is not None:
                _seam(u, prev_right[0], prev_right[1], left_col, left_day)
            if bc in bottom_prev:
                _seam(u, bottom_prev[bc][0], bottom_prev[bc][1], top_row, top_day)
            prev_right = (right_col, right_day)
            bottom_prev[bc] = (bot_row, bot_day)
            offset += nlab
        log(f"    {tile} row {br+1}/{nblk} — {sum(len(p) for p in parts)} raw parts, "
            f"{time.time()-t0:.0f}s")

    for ds in (radd, palm, peat, prim, glad):
        if ds is not None:
            ds.close()
    # NB: name it rawgrid, not raw — the cluster aggregation below binds `raw` too, and the
    # collision silently wrote the cluster table into raw_<tile>.parquet.
    rawgrid = (pd.concat(raw_parts, ignore_index=True)
                 .groupby(["gx", "gy", "wk"], as_index=False).sum()) if raw_parts else None
    del raw_parts
    if not parts:
        return None, rawgrid

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
    return out.drop(columns=["root"]), rawgrid


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
        df, raw = process_tile(tile)
        if raw is not None and len(raw):
            raw.to_parquet(config.ALERTS_DIR / f"raw_{tile}.parquet", index=False)
        if df is None or df.empty:
            pd.DataFrame().to_parquet(out)
            log(f"[{tile}] no clusters")
            continue
        df.to_parquet(out, index=False)
        log(f"[{tile}] {len(df):,} events >= {config.MIN_CLUSTER_HA} ha, {df.ha.sum():,.0f} ha "
            f"of {raw.ha.sum():,.0f} ha raw ({df.ha.sum()/raw.ha.sum():.0%}), "
            f"{time.time()-t0:.0f}s")
    log("alerts complete")


if __name__ == "__main__":
    main(sys.argv[1:])
