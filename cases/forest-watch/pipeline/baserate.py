"""Stage 7 (review) — baserate.  The two tests the case page never ran on itself.

The page's headline is a conditional probability with no denominator published:
*75 % of alerted hectares fall inside a palm-mill sourcing catchment.*  A share is only
informative against its base rate.  If most of the land where a RADD alert can physically
be raised is already within 50 km of a mill, then 75 % is close to what pure chance would
produce and the number carries almost no information about palm.

TEST A — the catchment base rate, and the lift.
    Sample the RADD detection domain itself (the UMD 2001 primary humid-tropical-forest
    mask, which is exactly where RADD issues alerts), clipped to Indonesia, on a regular
    lattice.  For every sampled cell compute the great-circle distance to the nearest
    Universal Mill List mill.  That gives P(within r km | a hectare where an alert is
    possible) — the correct null.  Compare with P(within r km | an alerted hectare),
    computed from the case's own linked.parquet.  lift = observed / base.
    The same is computed over all Indonesian land, and per province, because the gate
    the case published (Riau linked share >= 25 %) is only meaningful against Riau's
    own base rate.

TEST B — how much of Indonesia's "tree-cover loss" is plantation, not deforestation.
    Hansen tree-cover loss is gross removal of canopy and counts an oil-palm block being
    replanted exactly as it counts primary forest being cleared; that is the single most
    common misreading in this literature.  The case states the caveat in prose but never
    measures it.  We cross the Hansen loss year raster with the SDPT v2 planted-tree
    extent (oil palm / wood fibre / rubber) and with the 2001 primary-forest mask, at
    full 30 m resolution over Indonesian province polygons, and publish the split.
    The primary-forest-loss column doubles as an independent check: GFW publishes its own
    Indonesian primary-forest loss for 2023 and 2024, so ours has something to miss.

Outputs: data/baserate.json (merged, so either test can be re-run alone).

Run:
    uv run python pipeline/baserate.py --test a     # minutes
    uv run python pipeline/baserate.py --test b     # ~30 min, do it under systemd-run
"""

from __future__ import annotations

import json
import sys
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window
from scipy.spatial import cKDTree

import config
from alerts import log, px_area_ha, tile_origin

# Test A: lattice samples per 10-degree tile edge.  2500 -> a 480 m lattice, ~93 M national
# samples before masking, which is dense enough that the domain-area estimate is stable to
# well under a percentage point and coarse enough to hold in memory.
LATTICE = 2500
RADII_KM = (5, 10, 20, 30, 40, 50, 75, 100)
DIST_BINS = (0, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 1000)
# Test B: Hansen loss is walked at native 30 m in square blocks.  5000 keeps the working set
# near 150 MB (loss.py hit an OOM at 10000 on the same box).
BLOCK30 = 5_000
OUT = config.DATA_DIR / "baserate.json"

PALM_NAMES = {1: "oil_palm", 2: "wood_fibre", 3: "rubber"}


def _xyz(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def _merge(patch: dict) -> None:
    cur = json.loads(OUT.read_text()) if OUT.exists() else {}
    cur.update(patch)
    cur["generated"] = time.strftime("%Y-%m-%d")
    OUT.write_text(json.dumps(cur, indent=1))
    log("wrote", OUT)


# ── TEST A ─────────────────────────────────────────────────────────────────────────────
def test_a() -> dict:
    prov = gpd.read_parquet(config.BOUNDARIES).reset_index(drop=True)
    prov["pid"] = np.arange(1, len(prov) + 1, dtype=np.int32)
    names = dict(zip(prov.pid, prov.province))
    mills = pd.read_parquet(config.MILLS_PARQUET)
    tree = cKDTree(_xyz(mills.longitude.to_numpy(), mills.latitude.to_numpy()))
    log(f"{len(mills)} mills, {len(prov)} provinces, lattice {LATTICE} per tile")

    # accumulators: hectares of land / of RADD domain, per province, per distance bin
    nb = len(DIST_BINS) - 1
    land = np.zeros((len(prov) + 1, nb))
    dom = np.zeros((len(prov) + 1, nb))

    for tile in config.TILES_IDN:
        p = config.RAW / "primary" / f"{tile}.tif"
        if not p.exists():
            log(f"[{tile}] no primary tile — skipped")
            continue
        west, north = tile_origin(tile)
        cell = config.TILE_DEG / LATTICE
        tb = prov.cx[west:west + config.TILE_DEG, north - config.TILE_DEG:north]
        if tb.empty:
            continue
        with rasterio.open(p) as ds:
            # nearest-neighbour decimation = a systematic sample of the mask at cell centres
            prim = ds.read(1, out_shape=(LATTICE, LATTICE)) > 0
        tr = rasterio.transform.from_origin(west, north, cell, cell)
        pr = rasterize(zip(tb.geometry, tb.pid), out_shape=(LATTICE, LATTICE),
                       transform=tr, fill=0, dtype="uint8")
        sel = pr > 0
        if not sel.any():
            continue
        rr, cc = np.nonzero(sel)
        lat = north - (rr + 0.5) * cell
        lon = west + (cc + 0.5) * cell
        ha = px_area_ha(lat, cell)
        d, _ = tree.query(_xyz(lon, lat), k=1)
        km = 2 * 6371.0088 * np.arcsin(np.clip(d, 0, 2) / 2)
        b = np.clip(np.digitize(km, DIST_BINS) - 1, 0, nb - 1)
        pid = pr[sel].astype(np.int64)
        isdom = prim[sel]
        np.add.at(land, (pid, b), ha)
        np.add.at(dom, (pid, b), ha * isdom)
        log(f"[{tile}] {sel.sum():,} land cells, {isdom.sum():,} in the RADD domain")

    # the observed side, from the case's own event table
    lk = pd.read_parquet(config.LINKED)
    tot_ha = float(lk.ha.sum())
    ab = np.clip(np.digitize(lk.mill_dist_km.to_numpy(), DIST_BINS) - 1, 0, nb - 1)
    alert_hist = np.zeros(nb)
    np.add.at(alert_hist, ab, lk.ha.to_numpy())
    alert_n = np.zeros(nb)
    np.add.at(alert_n, ab, 1.0)

    def curve(hist: np.ndarray) -> dict:
        t = hist.sum()
        return {str(r): float(hist[np.array(DIST_BINS[1:]) <= r].sum() / t) if t else None
                for r in RADII_KM}

    land_nat, dom_nat = land[1:].sum(0), dom[1:].sum(0)
    out = {
        "method": ("Systematic lattice sample of the UMD 2001 primary humid-tropical-forest "
                   "mask (RADD's detection domain) at "
                   f"{config.TILE_DEG / LATTICE * 111_320:.0f} m spacing, clipped to Indonesian "
                   "province polygons, with great-circle distance to the nearest Universal "
                   "Mill List mill at every sample."),
        "lattice_m": round(config.TILE_DEG / LATTICE * 111_320, 1),
        "n_mills": int(len(mills)),
        "land_ha": float(land_nat.sum()),
        "domain_ha": float(dom_nat.sum()),
        "domain_share_of_land": float(dom_nat.sum() / land_nat.sum()),
        "alert_ha": tot_ha,
        "bins_km": list(DIST_BINS),
        "hist": {"land_ha": land_nat.round(0).tolist(), "domain_ha": dom_nat.round(0).tolist(),
                 "alert_ha": alert_hist.round(1).tolist(), "alert_n": alert_n.tolist()},
        "national": {},
        "by_province": {},
    }
    cl, cd, ca, cn = curve(land_nat), curve(dom_nat), curve(alert_hist), curve(alert_n)
    for r in RADII_KM:
        k = str(r)
        out["national"][k] = {
            "land_base": cl[k], "domain_base": cd[k],
            "alert_ha_share": ca[k], "alert_event_share": cn[k],
            "lift_vs_domain": (ca[k] / cd[k]) if cd[k] else None,
            "lift_vs_land": (ca[k] / cl[k]) if cl[k] else None,
        }
    for pid in range(1, len(prov) + 1):
        name = names[pid]
        if dom[pid].sum() <= 0:
            continue
        sub = lk.loc[lk.province == name]
        if not len(sub):
            continue
        pb = np.clip(np.digitize(sub.mill_dist_km.to_numpy(), DIST_BINS) - 1, 0, nb - 1)
        ah = np.zeros(nb)
        np.add.at(ah, pb, sub.ha.to_numpy())
        cdp, cap, clp = curve(dom[pid]), curve(ah), curve(land[pid])
        out["by_province"][name] = {
            "alert_ha": float(sub.ha.sum()),
            "domain_ha": float(dom[pid].sum()),
            "land_ha": float(land[pid].sum()),
            "domain_base_50": cdp["50"], "land_base_50": clp["50"],
            "alert_share_50": cap["50"],
            "lift_50": (cap["50"] / cdp["50"]) if cdp["50"] else None,
        }
    n50 = out["national"]["50"]
    log(f"national 50 km: domain base {n50['domain_base']:.4f}, land base "
        f"{n50['land_base']:.4f}, alerted {n50['alert_ha_share']:.4f}, "
        f"lift {n50['lift_vs_domain']:.3f}")
    return out


# ── TEST B ─────────────────────────────────────────────────────────────────────────────
def test_b() -> dict:
    prov = gpd.read_parquet(config.BOUNDARIES).reset_index(drop=True)
    prov["pid"] = np.arange(1, len(prov) + 1, dtype=np.int32)
    acc: dict[tuple[int, int, int], float] = {}
    year_base: int | None = None

    for tile in config.TILES_IDN:
        pl = config.RAW / "tcl30" / f"{tile}.tif"
        if not pl.exists():
            log(f"[{tile}] no tcl30 tile")
            continue
        pp = config.RAW / "palm" / f"{tile}.tif"
        pr_ = config.RAW / "primary" / f"{tile}.tif"
        west, north = tile_origin(tile)
        tb = prov.cx[west:west + config.TILE_DEG, north - config.TILE_DEG:north]
        if tb.empty:
            continue
        t0 = time.time()
        with rasterio.open(pl) as dl:
            px = config.TILE_DEG / dl.width
            nblk = dl.width // BLOCK30
            dp = rasterio.open(pp) if pp.exists() else None
            dpr = rasterio.open(pr_) if pr_.exists() else None
            for br in range(nblk):
                for bc in range(nblk):
                    win = Window(bc * BLOCK30, br * BLOCK30, BLOCK30, BLOCK30)
                    loss = dl.read(1, window=win)
                    if not loss.any():
                        continue
                    if year_base is None:
                        year_base = 0 if int(loss.max()) > 100 else 2000
                    tr = rasterio.transform.from_origin(
                        west + bc * BLOCK30 * px, north - br * BLOCK30 * px, px, px)
                    sub = tb.cx[west + bc * BLOCK30 * px:west + (bc + 1) * BLOCK30 * px,
                                north - (br + 1) * BLOCK30 * px:north - br * BLOCK30 * px]
                    if sub.empty:
                        del loss
                        continue
                    pmask = rasterize(zip(sub.geometry, sub.pid), out_shape=loss.shape,
                                      transform=tr, fill=0, dtype="uint8")
                    sel = (loss > 0) & (pmask > 0)
                    if not sel.any():
                        del loss, pmask, sel
                        continue
                    palm = (dp.read(1, window=win)[sel].astype(np.int32) if dp is not None
                            else np.zeros(int(sel.sum()), np.int32))
                    prim = (dpr.read(1, window=win)[sel] > 0 if dpr is not None
                            else np.zeros(int(sel.sum()), bool))
                    palm = np.where(np.isin(palm, [1, 2, 3]), palm, 0)
                    rr, _ = np.nonzero(sel)
                    lat = north - (br * BLOCK30 + rr + 0.5) * px
                    ha = px_area_ha(lat, px)
                    yv = loss[sel].astype(np.int32)
                    key = yv * 10 + palm * 2 + prim.astype(np.int32)
                    del loss, pmask, sel, rr, lat
                    s = pd.DataFrame({"k": key, "ha": ha}).groupby("k")["ha"].sum()
                    for k, v in s.items():
                        k = int(k)
                        acc[(k // 10, (k % 10) // 2, k % 2)] = \
                            acc.get((k // 10, (k % 10) // 2, k % 2), 0.0) + float(v)
                    del ha, key, yv, s, palm, prim
                log(f"    {tile} row {br + 1}/{nblk} ({time.time() - t0:.0f}s)")
            if dp is not None:
                dp.close()
            if dpr is not None:
                dpr.close()
    if not acc:
        log("no loss accumulated")
        return {}
    base = year_base if year_base is not None else 2000
    rows = [{"year": base + y, "plantation": PALM_NAMES.get(p, "none"),
             "primary_2001": bool(pm), "loss_ha": ha}
            for (y, p, pm), ha in acc.items() if 2001 <= base + y <= 2030]
    df = pd.DataFrame(rows)
    by_year = df.groupby("year").loss_ha.sum()
    plant = df.loc[df.plantation != "none"].groupby("year").loss_ha.sum()
    prim_y = df.loc[df.primary_2001].groupby("year").loss_ha.sum()
    prim_out = df.loc[df.primary_2001 & (df.plantation == "none")].groupby("year").loss_ha.sum()
    years = sorted(by_year.index.tolist())
    out = {
        "method": ("Hansen umd_tree_cover_loss v1.13 year__tcd30_2000 crossed at native 30 m "
                   "with SDPT v2 planted-tree extent (simpleName: 1 oil palm, 2 wood fibre or "
                   "timber, 3 rubber) and the UMD 2001 primary-forest mask, over Indonesian "
                   "province polygons, with geodetic pixel area."),
        "years": years,
        "total_ha": {str(y): float(by_year[y]) for y in years},
        "in_plantation_ha": {str(y): float(plant.get(y, 0.0)) for y in years},
        "in_plantation_share": {str(y): float(plant.get(y, 0.0) / by_year[y]) for y in years},
        "primary_2001_ha": {str(y): float(prim_y.get(y, 0.0)) for y in years},
        "primary_outside_plantation_ha": {str(y): float(prim_out.get(y, 0.0)) for y in years},
        "by_plantation_class_ha": {
            k: {str(y): float(v) for y, v in g.groupby("year").loss_ha.sum().items()}
            for k, g in df.groupby("plantation")},
        "gfw_published_primary_loss_ha": config.GFW_IDN_PRIMARY_LOSS_HA,
        "rows": df.round(2).to_dict("records"),
    }
    tot = sum(out["total_ha"].values())
    pl_ = sum(out["in_plantation_ha"].values())
    log(f"2001-{years[-1]}: {tot:,.0f} ha loss, {pl_:,.0f} ha ({pl_/tot:.1%}) inside mapped "
        f"plantation; primary-2001 {sum(out['primary_2001_ha'].values()):,.0f} ha")
    return out


# ── TEST C ─────────────────────────────────────────────────────────────────────────────
def test_c() -> dict:
    """Two cheap controls on the headline.

    C1  The >= 0.5 ha event floor keeps only about a fifth of alerted hectares.  If small
        detections sit systematically closer to (or further from) mills than large ones,
        the published linkage share is a property of the floor rather than of the country.
        alerts.py already writes the unfiltered 0.05-degree x weekly grid for the alert
        reconciliation check, so the unfiltered mill-catchment share costs one join.
    C2  How much oil palm the stored SDPT v2 layer actually maps in Indonesia, against
        the two published remote-sensed extents.  The case's own README asserts SDPT
        under-maps smallholder palm; nobody had measured by how much.
    """
    prov = gpd.read_parquet(config.BOUNDARIES)[["province", "geometry"]]
    mills = pd.read_parquet(config.MILLS_PARQUET)
    tree = cKDTree(_xyz(mills.longitude.to_numpy(), mills.latitude.to_numpy()))

    # --- C1: unfiltered alert hectares by distance to the nearest mill -------------------
    from alerts import RAW_GRID
    frames = []
    for f in sorted(config.ALERTS_DIR.glob("raw_*.parquet")):
        g = pd.read_parquet(f)
        frames.append(g.groupby(["gx", "gy"], as_index=False).ha.sum())
    raw = (pd.concat(frames, ignore_index=True).groupby(["gx", "gy"], as_index=False).ha.sum()
           if frames else pd.DataFrame(columns=["gx", "gy", "ha"]))
    lon = config.BBOX_IDN[0] + (raw.gx.to_numpy() + 0.5) * RAW_GRID
    lat = config.BBOX_IDN[3] - (raw.gy.to_numpy() + 0.5) * RAW_GRID
    pts = gpd.GeoDataFrame(raw[["ha"]], geometry=gpd.points_from_xy(lon, lat), crs=4326)
    j = gpd.sjoin(pts, prov, how="inner", predicate="within")
    keep = j.index.to_numpy()
    d, _ = tree.query(_xyz(lon[keep], lat[keep]), k=1)
    km = 2 * 6371.0088 * np.arcsin(np.clip(d, 0, 2) / 2)
    w = raw.ha.to_numpy()[keep]
    lk = pd.read_parquet(config.LINKED)
    nb = len(DIST_BINS) - 1
    ub = np.clip(np.digitize(km, DIST_BINS) - 1, 0, nb - 1)
    uhist = np.zeros(nb)
    np.add.at(uhist, ub, w)
    c1 = {
        "grid_deg": RAW_GRID,
        "bins_km": list(DIST_BINS),
        "unfiltered_hist_ha": uhist.round(0).tolist(),
        "unfiltered_ha": float(w.sum()),
        "event_table_ha": float(lk.ha.sum()),
        "event_floor_keeps": float(lk.ha.sum() / w.sum()) if w.sum() else None,
        "unfiltered_within_50km": float(w[km <= 50].sum() / w.sum()) if w.sum() else None,
        "events_within_50km": float(lk.loc[lk.mill_dist_km <= 50].ha.sum() / lk.ha.sum()),
        "note": ("The unfiltered grid is 0.05 degrees (~5.5 km), so a cell's mill distance is "
                 "its centre's; that is coarse against a 50 km radius but unbiased."),
    }
    # size gradient: does mill distance vary with event size?
    q = lk.assign(band=pd.cut(lk.ha, [0, 1, 2, 5, 20, 100, 1e9],
                              labels=["0.5-1", "1-2", "2-5", "5-20", "20-100", "100+"]))
    c1["by_size"] = [
        {"band": str(b), "events": int(len(g)), "ha": float(g.ha.sum()),
         "within_50km_ha_share": float(g.loc[g.mill_dist_km <= 50].ha.sum() / g.ha.sum()),
         "median_mill_km": float(g.mill_dist_km.median())}
        for b, g in q.groupby("band", observed=True)]

    # --- C2: how much palm the stored layer maps -----------------------------------------
    prov2 = prov.reset_index(drop=True).copy()
    prov2["pid"] = np.arange(1, len(prov2) + 1, dtype=np.int32)
    area = {1: 0.0, 2: 0.0, 3: 0.0}
    dom_palm = 0.0
    for tile in config.TILES_IDN:
        p = config.RAW / "palm" / f"{tile}.tif"
        if not p.exists():
            continue
        west, north = tile_origin(tile)
        cell = config.TILE_DEG / LATTICE
        tb = prov2.cx[west:west + config.TILE_DEG, north - config.TILE_DEG:north]
        if tb.empty:
            continue
        with rasterio.open(p) as ds:
            arr = ds.read(1, out_shape=(LATTICE, LATTICE))
        pp = config.RAW / "primary" / f"{tile}.tif"
        if pp.exists():
            with rasterio.open(pp) as dsp:
                prim = dsp.read(1, out_shape=(LATTICE, LATTICE)) > 0
        else:
            prim = np.zeros(arr.shape, bool)
        tr = rasterio.transform.from_origin(west, north, cell, cell)
        pm = rasterize(zip(tb.geometry, tb.pid), out_shape=(LATTICE, LATTICE),
                       transform=tr, fill=0, dtype="uint8") > 0
        for v in (1, 2, 3):
            sel = pm & (arr == v)
            if not sel.any():
                continue
            rr, _ = np.nonzero(sel)
            area[v] += float(px_area_ha(north - (rr + 0.5) * cell, cell).sum())
            if v == 1:
                rr2, _ = np.nonzero(sel & prim)
                if len(rr2):
                    dom_palm += float(px_area_ha(north - (rr2 + 0.5) * cell, cell).sum())
        log(f"[{tile}] palm sampled")
    c2 = {"sdpt_ha": {PALM_NAMES[v]: round(a, 0) for v, a in area.items()},
          "sdpt_palm_in_radd_domain_ha": round(dom_palm, 0),
          "sdpt_palm_in_radd_domain_share": round(dom_palm / area[1], 4) if area[1] else None,
          "descals_2021_idn_mapped_mha": 11.54,
          "descals_2021_idn_estimate_mha": 12.05,
          "gaveau_2022_idn_mapped_mha": 16.24,
          "gaveau_2022_idn_adjusted_mha": 18.83,
          "note": ("SDPT v2 oil-palm extent measured on the same lattice as the base rate, "
                   "against the two published remote-sensed Indonesian extents.")}
    log(f"SDPT oil palm {area[1]/1e6:.2f} Mha vs Descals 11.54 Mha vs Gaveau 16.24 Mha; "
        f"{dom_palm/area[1]:.2%} of it lies inside RADD's detection domain")

    # --- C3: how many mills claim the same hectare, and how far radar leads optical -------
    mw = lk.mills_within_50km.to_numpy()
    ha = lk.ha.to_numpy()
    inc = mw > 0
    both = lk.loc[lk.glad_agree & lk.glad_min.notna()]
    lead_all = (both.glad_min.to_numpy() - both.day_min.to_numpy())
    # The agreement flag allows the GLAD date RANGE inside a footprint to overlap the RADD
    # range within +/- 60 days, so glad_min itself can sit years earlier where the same
    # ground was disturbed before. The lead statistic is therefore reported on the
    # in-window subset, and the out-of-window share is published beside it.
    inwin = (lead_all >= -config.GLAD_AGREEMENT_DAYS) & (lead_all <= config.GLAD_AGREEMENT_DAYS)
    lead = lead_all[inwin]
    LEAD_EDGES = np.arange(-60, 61, 10)
    lead_hist = np.histogram(lead, bins=LEAD_EDGES)[0]
    c3 = {
        "mills_claiming_a_hectare": {
            "mean_over_alert_ha_in_catchment": float((mw[inc] * ha[inc]).sum() / ha[inc].sum()),
            "median": float(np.median(mw[inc])) if inc.any() else None,
            "p90": float(np.quantile(mw[inc], 0.9)) if inc.any() else None,
            "max": int(mw.max()),
            "share_of_catchment_ha_with_2plus_mills":
                float(ha[(mw >= 2)].sum() / ha[inc].sum()) if inc.any() else None,
            "share_of_catchment_ha_with_5plus_mills":
                float(ha[(mw >= 5)].sum() / ha[inc].sum()) if inc.any() else None,
        },
        "radar_lead_days": {
            "n_matched": int(len(both)),
            "n_in_window": int(len(lead)),
            "out_of_window_share": float(1 - inwin.mean()) if len(both) else None,
            "mean": float(lead.mean()) if len(lead) else None,
            "median": float(np.median(lead)) if len(lead) else None,
            "p25": float(np.quantile(lead, 0.25)) if len(lead) else None,
            "p75": float(np.quantile(lead, 0.75)) if len(lead) else None,
            "share_radar_first": float((lead > 0).mean()) if len(lead) else None,
            "hist_edges": LEAD_EDGES.tolist(),
            "hist": lead_hist.tolist(),
            "note": ("Days between the first RADD detection of an event and the first GLAD-L "
                     "optical alert inside the same footprint, restricted to pairs inside the "
                     "60-day agreement window. Positive means radar saw it first."),
        },
        "glad_agreement_by_size": [
            {"band": str(b), "events": int(len(g)), "agree": float(g.glad_agree.mean())}
            for b, g in lk.assign(band=pd.cut(lk.ha, [0, 1, 2, 5, 20, 100, 1e9],
                                              labels=["0.5-1", "1-2", "2-5", "5-20",
                                                      "20-100", "100+"]))
            .groupby("band", observed=True)],
    }
    log(f"mills claiming an alerted hectare: mean "
        f"{c3['mills_claiming_a_hectare']['mean_over_alert_ha_in_catchment']:.2f}; "
        f"radar leads optical by a median {c3['radar_lead_days']['median']} days")
    return {"event_floor": c1, "palm_extent": c2, "identifiability": c3}


def main(argv: list[str]) -> None:
    which = "abc"
    if "--test" in argv:
        which = argv[argv.index("--test") + 1].lower()
    if "a" in which:
        _merge({"catchment": test_a()})
    if "b" in which:
        _merge({"loss_split": test_b()})
    if "c" in which:
        _merge({"controls": test_c()})


if __name__ == "__main__":
    main(sys.argv[1:])
