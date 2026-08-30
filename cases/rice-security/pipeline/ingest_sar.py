"""Stage 4 · sar — the Sentinel-1 time series.  The long stage; read this before running it.

WHAT THE SPEC SAID, WHAT REALITY SAID, AND WHY THE DIFFERENCE IS AN UPGRADE
---------------------------------------------------------------------------
The spec's primary route is ASF's OPERA L2 RTC-S1 on the premise that the repo's existing
``EARTHDATA_TOKEN`` is the credential.  It is not, and no retry fixes it.  The token is a valid,
unexpired EDL JWT that LP DAAC's egress accepts; ASF answers every object — data and static
layers alike, and ``cumulus.asf.alaska.edu/s3credentials`` too — with

    403 {"error":"invalid_token","error_description":"EULA Acceptance Failure",
         "resolution_url":"https://urs.earthdata.nasa.gov/approve_app?client_id=BO_n7nTIlMljdvU6kRRB3g"}

That approval is a one-time, interactive, browser-and-account-owner action.  It is the single
highest-value thing a human can do for this case (see the README), and it is not something a
pipeline can do for itself.

So the shipped route is Microsoft Planetary Computer's ``sentinel-1-rtc`` collection: the same
physical quantity — radiometrically terrain-corrected gamma0 — produced by Catalyst from the
same ESA GRD products, **at 10 m instead of 30 m**, licensed **CC-BY-4.0**, served anonymously
as COGs with 512x512 tiles and six overview levels.  Three consequences, all good:

  * the spec's largest stated risk — that 30 m mixed pixels dilute the flooding minimum on
    0.3-0.5 ha Javanese sawah — is retired rather than mitigated;
  * nothing bulky is transferred.  The AOI is window-read from the remote COG, so the ~50 GB
    OPERA transfer budget becomes ~2 GB and the "delete the raw burst after aggregating" dance
    is unnecessary because no raw burst is ever written;
  * the multi-look happens server-side and provably correctly.  Reading overview level 8 is a
    64-look average of 10 m pixels IN LINEAR POWER, which is exactly the house rule; measured
    against the full-resolution read the linear means agree to five decimals (0.11770 at 80 m,
    0.11769 at 40 m, 0.11774 at 160 m, 0.11770 at 10 m).

WHAT IS PRESERVED WHATEVER THE ROUTE
------------------------------------
  * ORBIT SEPARATION.  gamma0 depends on incidence angle, so relative orbits are NOT
    interchangeable: mixing them produces a sawtooth that looks like phenology.  Series are
    built per relative orbit, stored per relative orbit, and only combined in ``backscatter.py``
    after normalisation.  Two orbits per kabupaten (chosen from the catalogue by observed date
    count, not assumed) take the effective revisit from 12 days to about 6 — which is what makes
    a flooding minimum two to three weeks wide datable at all.
  * VV and VH separately.  VH is the workhorse for rice — volume scattering from a canopy of
    vertical stalks is what climbs through tillering — but the flooding minimum is clearest
    in VV.
  * the acquisition's own metadata: relative orbit, pass direction, platform.  S1B failed in
    2021 and S1C ramped up later, so revisit density changes mid-record.  That is a change in
    sampling, not in the crop, and it is recorded rather than corrected.

RESUMABILITY
------------
One ``.npy`` per (kabupaten, relative orbit, acquisition date), shape (2, n_cells) int16 in
hundredths of a dB with -32768 for missing.  The manifest is the ledger; a finished slot is
skipped on rerun, so the stage can be killed and restarted without losing a byte.  Free disk is
checked before every date.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import config
import util
from util import log

SAR_DIR = config.DATA_DIR / "sar"
SAR_INDEX = config.DATA_DIR / "sar_index.json"
NODATA = -32768
_LOCK = threading.Lock()


# ── catalogue ─────────────────────────────────────────────────────────────────────────
def search(bbox, start: str, end: str, collection: str | None = None) -> list[dict]:
    """MPC STAC search over a bbox and window; one row per item, paged to exhaustion.

    Records relative orbit, pass direction and platform for every hit, so a cell whose orbit
    coverage changes mid-record is visible here rather than discovered later as a kink in a
    phenology curve.
    """
    import requests

    body = {"collections": [collection or config.MPC_RTC_COLLECTION], "bbox": list(bbox),
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z", "limit": 500}
    url = f"{config.MPC_STAC}/search"
    out, seen, guard = [], set(), 0
    while guard < 60:
        guard += 1
        for attempt in range(5):
            try:
                r = requests.post(url, json=body, timeout=180)
                r.raise_for_status()
                break
            except Exception as exc:                            # noqa: BLE001
                log(f"stac: {type(exc).__name__} {exc} (attempt {attempt + 1}/5)")
                time.sleep(4 * (attempt + 1))
        else:
            raise RuntimeError("STAC search failed after 5 attempts")
        j = r.json()
        for f in j.get("features", []):
            if f["id"] in seen:
                continue
            seen.add(f["id"])
            p = f["properties"]
            out.append(dict(id=f["id"], dt=p["datetime"], date=p["datetime"][:10],
                            orbit=p.get("sat:relative_orbit"),
                            direction=p.get("sat:orbit_state"),
                            platform=p.get("platform", "?"),
                            geom=f.get("geometry"),
                            vv=f["assets"].get("vv", {}).get("href"),
                            vh=f["assets"].get("vh", {}).get("href")))
        nxt = [l for l in j.get("links", []) if l.get("rel") == "next"]
        if not nxt or not nxt[0].get("body"):
            break
        # MPC's next link is a POST with ``merge: true`` and a body of only {"token": ...}.
        # Replacing the body instead of merging it silently drops the collection, bbox and
        # datetime and pages through the WHOLE archive — a bug that presents as plausible
        # extra items rather than as an error.
        body = {**body, **nxt[0]["body"]} if nxt[0].get("merge") else nxt[0]["body"]
    return out


def choose_orbits(items: list[dict], kab_geom, floor: float = 0.15,
                  target: float = 0.98, cap: int = 4) -> tuple[list[int], dict]:
    """Which relative orbits to build series from — chosen by COVERAGE, not by date count.

    The obvious selection (the orbits with the most acquisitions) is wrong here and wrong in a
    way that looks fine.  Indramayu sits on the EDGE of relative orbit 98's swath: orbit 98 has
    the most dates of any orbit over the kabupaten and reaches only about 36 % of its area,
    stopping dead at 108.06 degE.  Taking it alone yields a dense, clean, complete-looking time
    series for a third of a regency and silently no data for the rest — which would then be read
    downstream as "no rice detected" over two thirds of Indonesia's largest rice producer.

    So orbits are ranked by the fraction of the kabupaten polygon their footprints actually
    cover, and added greedily until the union reaches ``target`` or ``cap`` orbits are in.  Each
    orbit still gets its OWN series — nothing here mixes incidence angles; the union is only
    what makes a cell observed at all, and the by-product is a shorter effective revisit.
    """
    from shapely.geometry import shape
    from shapely.ops import unary_union

    per: dict[int, dict] = {}
    for it in items:
        if it["orbit"] is None or not it.get("geom"):
            continue
        o = int(it["orbit"])
        e = per.setdefault(o, {"dates": set(), "geoms": [], "dir": it.get("direction")})
        e["dates"].add(it["date"])
        if len(e["geoms"]) < 40:            # a handful of footprints define the swath exactly
            e["geoms"].append(shape(it["geom"]))
    area = kab_geom.area
    stats = {}
    for o, e in per.items():
        u = unary_union(e["geoms"])
        stats[o] = dict(cover=float(u.intersection(kab_geom).area / area),
                        dates=len(e["dates"]), direction=e["dir"], _u=u)
    ranked = sorted(stats.items(), key=lambda kv: (-kv[1]["cover"], -kv[1]["dates"]))
    chosen, acc = [], None
    for o, s in ranked:
        if s["cover"] < floor and chosen:
            continue
        chosen.append(o)
        acc = s["_u"] if acc is None else acc.union(s["_u"])
        if acc.intersection(kab_geom).area / area >= target or len(chosen) >= cap:
            break
    # Coverage is the constraint; REVISIT is the second objective.  One orbit is a 12-day
    # series, and a flooding minimum is two to three weeks wide — datable, but only just, and
    # the date lands on a 12-day lattice.  A second near-complete orbit roughly halves that
    # without ever mixing incidence angles, because the two are carried as separate series to
    # the last possible moment.  So keep adding whole-kabupaten orbits up to MIN_ORBITS.
    # For the top-up the objective flips: coverage is already satisfied, so rank the remaining
    # near-complete orbits by DATE COUNT.  Ranking them by coverage instead picks (for Karawang)
    # T47 at 93 % with 50 acquisitions over T149 at 79 % with 101 — a worse series for the same
    # money.
    for o, s in sorted(ranked, key=lambda kv: -kv[1]["dates"]):
        if len(chosen) >= max(config.MIN_ORBITS_PER_UNIT, 1) or len(chosen) >= cap:
            break
        if o in chosen or s["cover"] < 0.6:
            continue
        chosen.append(o)
        acc = acc.union(s["_u"])
    report = {str(o): dict(cover=round(s["cover"], 3), dates=s["dates"],
                           direction=s["direction"], chosen=o in chosen)
              for o, s in ranked}
    union_cover = float(acc.intersection(kab_geom).area / area) if acc is not None else 0.0
    return chosen, dict(orbits=report, union_cover=round(union_cover, 4))


# ── the windowed read ─────────────────────────────────────────────────────────────────
def _dst_grid(cells_kab):
    """The kabupaten's own 100 m raster grid, and each cell's (row, col) in it."""
    import numpy as np
    from rasterio.transform import from_origin

    s = float(config.CELL_M)
    x = cells_kab["x"].to_numpy("float64")
    y = cells_kab["y"].to_numpy("float64")
    x0 = x.min() - s / 2
    y1 = y.max() + s / 2
    w = int(round((x.max() + s / 2 - x0) / s))
    h = int(round((y1 - (y.min() - s / 2)) / s))
    tr = from_origin(x0, y1, s, s)
    col = np.round((x - x0 - s / 2) / s).astype("int32")
    row = np.round((y1 - y - s / 2) / s).astype("int32")
    return tr, h, w, row, col, int(cells_kab["epsg"].iloc[0])


def _read_into(href: str, sas: str, dst_crs, dst_transform, h, w):
    """Read one asset's overlap with the destination grid, in LINEAR POWER.

    Returns ``None`` when the item does not actually touch the grid (STAC bboxes are generous)
    or when the read fails, so a single bad object never fails a whole date.
    """
    import numpy as np
    import rasterio
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import from_bounds

    ov = config.MPC_OVERVIEW
    left, top = dst_transform.c, dst_transform.f
    right = left + w * dst_transform.a
    bottom = top + h * dst_transform.e
    with rasterio.open(f"/vsicurl/{href}?{sas}") as ds:
        sb = transform_bounds(dst_crs, ds.crs, left, bottom, right, top, densify_pts=21)
        b = ds.bounds
        if sb[0] > b.right or sb[2] < b.left or sb[1] > b.top or sb[3] < b.bottom:
            return None
        win = from_bounds(max(sb[0], b.left), max(sb[1], b.bottom),
                          min(sb[2], b.right), min(sb[3], b.top),
                          transform=ds.transform).round_offsets().round_lengths()
        if win.width < 2 or win.height < 2:
            return None
        oh = max(1, int(win.height) // ov)
        ow = max(1, int(win.width) // ov)
        arr = ds.read(1, window=win, out_shape=(oh, ow), resampling=Resampling.average,
                      boundless=False).astype("float32")
        src_tr = ds.window_transform(win) * rasterio.Affine.scale(
            float(win.width) / ow, float(win.height) / oh)
        src_crs = ds.crs
    arr[~np.isfinite(arr)] = np.nan
    arr[arr <= 0] = np.nan
    if not np.isfinite(arr).any():
        return None
    dst = np.full((h, w), np.nan, "float32")
    reproject(source=arr, destination=dst, src_transform=src_tr, src_crs=src_crs,
              dst_transform=dst_transform, dst_crs=dst_crs,
              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.average)
    return dst


def _slot(kab: str, orbit: int, date: str, items: list[dict], grid, epsg) -> str:
    """One (kabupaten, orbit, date): read every covering item, mosaic, write int16 dB."""
    import numpy as np
    from rasterio.crs import CRS

    tr, h, w, row, col, _ = grid
    out = SAR_DIR / kab / str(orbit) / f"{date}.npy"
    key = f"sar:{kab}:{orbit}:{date}"
    if out.exists():
        return "skip"
    dst_crs = CRS.from_epsg(epsg)
    stack = np.full((2, len(row)), NODATA, "int16")
    got = 0
    for pi, pol in enumerate(("vv", "vh")):
        acc = np.zeros((h, w), "float64")
        cnt = np.zeros((h, w), "int16")
        for it in items:
            href = it.get(pol)
            if not href:
                continue
            for attempt in range(3):
                try:
                    a = _read_into(href, util.mpc_sas(), dst_crs, tr, h, w)
                    break
                except Exception as exc:                        # noqa: BLE001
                    if attempt == 2:
                        log(f"  read fail {kab}/{orbit}/{date}/{pol}: "
                            f"{type(exc).__name__} {exc}")
                        a = None
                    else:
                        util.mpc_sas(force=True)
                        time.sleep(2 + 3 * attempt)
            if a is None:
                continue
            m = np.isfinite(a)
            acc[m] += a[m]
            cnt[m] += 1
        if cnt.max() == 0:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            mos = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
        vals = mos[row, col]
        ok = np.isfinite(vals) & (vals > 0)
        db = np.full(len(row), NODATA, "int16")
        db[ok] = np.clip(np.round(10.0 * np.log10(vals[ok]) * 100), -4000, 2000).astype("int16")
        stack[pi] = db
        got += int(ok.sum())
    if got == 0:
        with _LOCK:
            util.manifest_put(key, status="empty", items=len(items))
        return "empty"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, stack)
    with _LOCK:
        util.manifest_put(key, status="ok", items=len(items), valid=got,
                          bytes=out.stat().st_size)
    return "ok"


# ── driver ────────────────────────────────────────────────────────────────────────────
def main() -> None:
    import numpy as np
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--kab", default=None, help="one kabupaten only")
    ap.add_argument("--orbit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="stop after N slots (smoke test)")
    args = ap.parse_args()

    util.require(Path(config.DATA_DIR / "cells.parquet").exists(),
                 "cells.parquet missing — run `make aux` first")
    cells = pd.read_parquet(config.DATA_DIR / "cells.parquet")
    index = json.loads(SAR_INDEX.read_text()) if SAR_INDEX.exists() else {}
    manifest = util.manifest_read()
    done = 0

    for kab in config.SCOPE_DEEP:
        if args.kab and kab != args.kab:
            continue
        ck = cells[cells["kabupaten"] == kab]
        if not len(ck):
            log(f"{kab}: no cells — skipped")
            continue
        grid = _dst_grid(ck)
        epsg = grid[5]
        bbox = (float(ck["lon"].min()) - 0.02, float(ck["lat"].min()) - 0.02,
                float(ck["lon"].max()) + 0.02, float(ck["lat"].max()) + 0.02)

        if kab in index and index[kab].get("items") and index[kab].get("coverage"):
            items = index[kab]["items"]
            orbits = index[kab]["orbits"]
        else:
            from shapely import wkb as _wkb
            adm = pd.read_parquet(config.DATA_DIR / "adm.parquet")
            geom = _wkb.loads(adm.loc[(adm["level"] == "ADM2") &
                                      (adm["name"] == kab), "wkb"].iloc[0])
            items = search(bbox, config.SAR_START, config.SAR_END)
            orbits, cover = choose_orbits(items, geom)
            index[kab] = dict(bbox=bbox, epsg=epsg, n_cells=int(len(ck)),
                              orbits=orbits, expected=list(config.SCOPE_ORBITS.get(kab, ())),
                              coverage=cover, items=items,
                              searched=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            SAR_INDEX.parent.mkdir(parents=True, exist_ok=True)
            SAR_INDEX.write_text(json.dumps(index))
            log(f"{kab}: orbit coverage " + ", ".join(
                f"T{o}={v['cover']:.0%}/{v['dates']}d{'*' if v['chosen'] else ''}"
                for o, v in cover["orbits"].items()) +
                f"  union={cover['union_cover']:.1%}")
        by_orbit: dict[int, dict[str, list]] = {}
        for it in items:
            if it["orbit"] is None or int(it["orbit"]) not in orbits:
                continue
            if args.orbit and int(it["orbit"]) != args.orbit:
                continue
            by_orbit.setdefault(int(it["orbit"]), {}).setdefault(it["date"], []).append(it)
        log(f"{kab}: {len(ck):,} cells, grid {grid[1]}x{grid[2]} EPSG:{epsg}, "
            f"orbits {orbits} (expected {config.SCOPE_ORBITS.get(kab)}), "
            f"dates {[f'{o}:{len(d)}' for o, d in by_orbit.items()]}")

        jobs = []
        for o, dd in by_orbit.items():
            for d, its in sorted(dd.items()):
                if manifest.get(f"sar:{kab}:{o}:{d}", {}).get("status") == "empty":
                    continue                       # genuinely no overlap; never retried
                if (SAR_DIR / kab / str(o) / f"{d}.npy").exists():
                    continue                       # the file IS the ledger entry that matters
                jobs.append((o, d, its))
        log(f"{kab}: {len(jobs)} slots to fetch")
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_slot, kab, o, d, its, grid, epsg): (o, d)
                    for o, d, its in jobs}
            for i, fut in enumerate(as_completed(futs), 1):
                o, d = futs[fut]
                try:
                    st = fut.result()
                except Exception as exc:                        # noqa: BLE001
                    log(f"  slot {kab}/{o}/{d}: {type(exc).__name__} {exc}")
                    st = "error"
                done += 1
                if i % 20 == 0 or i == len(futs):
                    rate = i / max(time.time() - t0, 1e-6)
                    log(f"  {kab}: {i}/{len(futs)} slots ({rate * 60:.1f}/min, "
                        f"eta {(len(futs) - i) / max(rate, 1e-9) / 60:.0f} min) last={st}")
                if not util.guard_disk(need_gb=1.0):
                    ex.shutdown(cancel_futures=True)
                    log("sar: disk guard — exiting 0, rerun to resume")
                    return
                if args.limit and done >= args.limit:
                    ex.shutdown(cancel_futures=True)
                    log(f"sar: --limit {args.limit} reached")
                    return
    log(f"sar: done, {done} slots this run")


if __name__ == "__main__":
    main()
