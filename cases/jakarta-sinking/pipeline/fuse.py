"""Stage: fuse (spec C3) — kelurahan-level subsidence + flood exposure, gates G-C4/G-C5.

Everything is computed on ONE common 100 m analysis grid (WorldPop's own cells
over BBOX, EPSG:4326, ~720×720), so people, buildings, ground and velocity line
up cell for cell and city totals are conserved:

  pop       WorldPop 2020 constrained (people per cell)
  ghs_pop   GHSL GHS-POP E2020 — cross-check only, warped from Mollweide (sum)
  built_m2  GHSL GHS-BUILT-S E2020 built-up surface (fraction × cell area)
  elev      Copernicus GLO-30 cell mean. GLO-30 is a *surface* model, so the
            cell MINIMUM is used as the ground proxy: in a 100 m urban cell the
            lowest 30 m pixels are roads, yards and canals, not roofs.
  vel       Ohenhen et al. 2026 vertical velocity (cm/yr, 2017–2023) from
            grid.py, bilinear; negative = subsidence

Kelurahan (Jakarta Satu, 267) are burned onto the grid by cell centre: every cell
belongs to exactly one kelurahan or to none (Tangerang/Bekasi/Depok/sea), which
is unbiased at internal boundaries and keeps the city total honest.

THE CLOCK IS A LINEAR EXTRAPOLATION, NOT A FORECAST:
  ground(year) = ground(2025) + vel × (year − 2025) / 100
with the 2017–2023 rate held constant, cells without InSAR coverage held at 0,
and the GLO-30 heights (acquired 2011–2015, ±2–4 m vertical accuracy) taken as
the 2025 surface. Read the clock for ordering and magnitude, not for dates.
"""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import date

import numpy as np
import pandas as pd

import config

DERIVED = config.DATA_DIR / "derived"
RAW = config.RAW
UTM = "EPSG:32748"                       # UTM 48S — area math
YEARS = list(range(2025, 2051))
BASE_YEAR = 2025
THRESHOLDS = {"1m": 1.0, "0m": 0.0}      # metres above mean sea level (EGM2008)
GROUND_FLOOR = -5.0                      # clamp for DSM pits / canals (m)


def log(msg: str) -> None:
    print(f"[fuse] {msg}", flush=True)


def zip_member(path, suffix=".tif") -> str:
    return next(n for n in zipfile.ZipFile(path).namelist() if n.lower().endswith(suffix))


# ----------------------------------------------------------------------------- grid
def analysis_grid():
    """WorldPop's cells over BBOX define the grid; returns (pop, transform, shape, cell_area_m2[row])."""
    import rasterio
    from rasterio.windows import from_bounds

    with rasterio.open(RAW / "population" / "idn_ppp_2020_constrained.tif") as src:
        win = from_bounds(*config.BBOX, src.transform).round_offsets().round_lengths()
        pop = src.read(1, window=win).astype("float64")
        pop[~np.isfinite(pop) | (pop == src.nodata) | (pop < 0)] = 0
        T = src.window_transform(win)
    H, W = pop.shape
    lat = T.f + T.e * (np.arange(H) + 0.5)
    dx = abs(T.a) * 111_320 * np.cos(np.radians(lat))
    dy = abs(T.e) * 110_574
    area = (dx * dy)[:, None] * np.ones((1, W))
    log(f"analysis grid {W}×{H} @ {abs(T.a):.6f}° (~{dx.mean():.0f} m); WorldPop in bbox {pop.sum():,.0f} people")
    return pop, T, (H, W), area


def warp(src, T, shape, resampling, src_transform=None, src_crs=None, src_nodata=None):
    """Reproject a rasterio band or an in-memory array onto the analysis grid (NaN = nodata)."""
    from rasterio.warp import reproject

    dst = np.full(shape, np.nan, dtype="float32")
    kw = dict(dst_transform=T, dst_crs="EPSG:4326", dst_nodata=np.nan, resampling=resampling)
    if src_transform is not None:
        kw.update(src_transform=src_transform, src_crs=src_crs, src_nodata=src_nodata)
    elif src_nodata is not None:
        kw.update(src_nodata=src_nodata)
    reproject(src, dst, **kw)
    return dst


def load_layers(T, shape, area):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.merge import merge

    # --- GHSL population + built-up (Mollweide 100 m) ---
    z = RAW / "population" / "GHS_POP_E2020_R10_C29.zip"
    with rasterio.open(f"zip://{z}!{zip_member(z)}") as src:
        ghs_pop = np.nan_to_num(warp(rasterio.band(src, 1), T, shape, Resampling.sum))
    zb = RAW / "population" / "GHS_BUILT_S_E2020_R10_C29.zip"
    with rasterio.open(f"zip://{zb}!{zip_member(zb)}") as src:
        frac = warp(rasterio.band(src, 1), T, shape, Resampling.average) / 10_000.0   # m² per 100 m cell → fraction
    built_m2 = np.nan_to_num(np.clip(frac, 0, 1)) * area
    log(f"GHSL: pop in bbox {ghs_pop.sum():,.0f}; built-up {built_m2.sum() / 1e6:,.0f} km²")

    # --- GLO-30 DSM: two tiles merged over the bbox, then mean + min per cell ---
    tiles = [rasterio.open(RAW / "dem" / u.rsplit("/", 1)[-1]) for u in config.GLO30_TILES]
    w, s, e, n = config.BBOX
    dem, dem_T = merge(tiles, bounds=(w - 0.01, s - 0.01, e + 0.01, n + 0.01), nodata=np.nan)
    for t in tiles:
        t.close()
    dem = dem[0].astype("float32")
    elev_mean = warp(dem, T, shape, Resampling.average, src_transform=dem_T, src_crs="EPSG:4326", src_nodata=np.nan)
    ground = warp(dem, T, shape, Resampling.min, src_transform=dem_T, src_crs="EPSG:4326", src_nodata=np.nan)
    ground = np.where(np.isfinite(ground), np.maximum(ground, GROUND_FLOOR), np.nan).astype("float32")
    log(f"GLO-30: surface mean {np.nanmean(elev_mean):.1f} m, ground-proxy median {np.nanmedian(ground):.1f} m, "
        f"{np.isfinite(ground).mean():.0%} of grid covered")

    # --- velocity (grid.py) ---
    with rasterio.open(DERIVED / "velocity_ohenhen2026_cmyr.tif") as src:
        vel = warp(rasterio.band(src, 1), T, shape, Resampling.bilinear, src_nodata=np.nan)
    with rasterio.open(DERIVED / "velocity_ohenhen2026_sd.tif") as src:
        vel_sd = warp(rasterio.band(src, 1), T, shape, Resampling.bilinear, src_nodata=np.nan)
    log(f"velocity: {np.isfinite(vel).mean():.0%} of grid covered; median {np.nanmedian(vel):+.2f} cm/yr")
    return ghs_pop, built_m2, elev_mean, ground, vel, vel_sd


# ----------------------------------------------------------------------------- kelurahan
def load_kelurahan():
    import geopandas as gpd

    k = gpd.read_file(RAW / "admin" / "kelurahan_dki.geojson").to_crs("EPSG:4326")
    k = k.rename(columns={"WADMKD": "name", "WADMKC": "kecamatan", "WADMKK": "kota", "KDEPUM": "id"})
    k["kota"] = k.kota.str.replace("Kota Adm. ", "", regex=False).str.replace("Kab. Adm. ", "", regex=False)
    k["island"] = k.kota.str.contains("Seribu")
    k["geometry"] = k.geometry.make_valid()
    k = k.sort_values("id").reset_index(drop=True)
    k["idx"] = np.arange(1, len(k) + 1)
    k["area_km2"] = k.to_crs(UTM).area / 1e6
    log(f"kelurahan: {len(k)} ({int((~k.island).sum())} mainland, {int(k.island.sum())} Kepulauan Seribu), "
        f"{k.area_km2.sum():,.0f} km²")
    return k[["id", "name", "kecamatan", "kota", "island", "idx", "area_km2", "geometry"]]


def burn(kel, T, shape):
    from rasterio import features

    ids = features.rasterize(zip(kel.geometry, kel.idx), out_shape=shape, transform=T, fill=0, dtype="int32")
    log(f"burned {int((ids > 0).sum()):,} cells into {len(np.unique(ids)) - 1} kelurahan (cell-centre rule)")
    return ids


def zonal(kel, ids, pop, ghs_pop, built_m2, elev_mean, ground, vel, vel_sd, area):
    n = len(kel)
    m = ids > 0
    g = ids[m]

    def bsum(x, mask=None):
        mm = m if mask is None else (m & mask)
        return np.bincount(ids[mm], weights=x[mm], minlength=n + 1)[1:]

    out = pd.DataFrame(index=kel.idx.values)
    out["cells"] = bsum(np.ones_like(pop))
    out["grid_area_km2"] = bsum(area) / 1e6
    out["pop"] = bsum(pop)
    out["ghs_pop"] = bsum(ghs_pop)
    out["built_km2"] = bsum(built_m2) / 1e6
    out["built_share"] = out.built_km2 / out.grid_area_km2

    vm = np.isfinite(vel)
    nv = np.bincount(ids[m & vm], minlength=n + 1)[1:]
    out["cov"] = nv / out.cells
    with np.errstate(invalid="ignore", divide="ignore"):
        out["v_mean"] = bsum(np.nan_to_num(vel), vm) / nv
        out["v_sd"] = bsum(np.nan_to_num(vel_sd), vm) / nv
        out["fast2"] = bsum((vel < -2).astype(float), vm) / nv     # share of covered area faster than −2 cm/yr
        out["fast1"] = bsum((vel < -1).astype(float), vm) / nv
    q = pd.DataFrame({"k": ids[m & vm], "v": vel[m & vm]}).groupby("k").v.quantile([0.5, 0.1]).unstack()
    out["v_med"] = q[0.5].reindex(out.index)
    out["v_p10"] = q[0.1].reindex(out.index)
    out["pop_fast2"] = bsum(pop * (vel < -2), vm)
    out["pop_fast1"] = bsum(pop * (vel < -1), vm)

    gm = np.isfinite(ground)
    ng = np.bincount(ids[m & gm], minlength=n + 1)[1:]
    with np.errstate(invalid="ignore", divide="ignore"):
        out["elev_mean"] = bsum(np.nan_to_num(elev_mean), np.isfinite(elev_mean)) / np.bincount(ids[m & np.isfinite(elev_mean)], minlength=n + 1)[1:]
        out["low1"] = bsum((ground < 1).astype(float), gm) / ng       # share of cells whose ground sits below +1 m
        out["low2"] = bsum((ground < 2).astype(float), gm) / ng
    qg = pd.DataFrame({"k": ids[m & gm], "g": ground[m & gm]}).groupby("k").g.quantile([0.5, 0.1]).unstack()
    out["ground_med"] = qg[0.5].reindex(out.index)
    out["ground_p10"] = qg[0.1].reindex(out.index)
    return out


def clock_years(ground, v, threshold):
    """Years until `ground` (m) falls below `threshold` at rate v (cm/yr). None = not at this rate."""
    if not (np.isfinite(ground) and np.isfinite(v)):
        return None
    if ground <= threshold:
        return 0.0
    if v >= -0.05:                     # slower than 0.5 mm/yr: treated as stable
        return None
    return float((ground - threshold) / (-v / 100.0))


def timeseries(kel, ids, pop, built_m2, ground, vel):
    """Per kelurahan and city: people / built-up (km²) on ground below each threshold, 2025→2050."""
    n = len(kel)
    m = (ids > 0) & np.isfinite(ground)
    v = np.where(np.isfinite(vel), vel, 0.0)       # no InSAR coverage → held stable (stated assumption)
    rows = []
    series = {t: {"pop": np.zeros((n, len(YEARS))), "built": np.zeros((n, len(YEARS)))} for t in THRESHOLDS}
    for j, y in enumerate(YEARS):
        gy = ground + v * (y - BASE_YEAR) / 100.0
        for t, thr in THRESHOLDS.items():
            below = m & (gy < thr)
            p = np.bincount(ids[below], weights=pop[below], minlength=n + 1)[1:]
            b = np.bincount(ids[below], weights=built_m2[below], minlength=n + 1)[1:] / 1e6
            series[t]["pop"][:, j] = p
            series[t]["built"][:, j] = b
            rows.append({"threshold": t, "year": y, "pop_below": float(p.sum()), "built_below_km2": float(b.sum())})
    city = pd.DataFrame(rows)
    return series, city


# ----------------------------------------------------------------------------- floods
def floods(kel):
    import geopandas as gpd
    from shapely.ops import unary_union

    kel_utm = kel.to_crs(UTM)
    out = pd.DataFrame(index=kel.idx.values)

    # --- BPBD flood history 2021–2024 (RT-level polygons, one record per RT per event) ---
    bp = gpd.read_file(RAW / "floods" / "bpbd_flood_history_2021_2024.geojson")
    bp = bp[(bp.WILAYAH != "TANGGAL_TEXT") & bp.geometry.notna() & ~bp.geometry.is_empty].copy()
    depth = pd.to_numeric(bp.GENANGAN.astype(str).str.extract(r"(\d+)")[0], errors="coerce")
    flagged = bp.FLOOD.astype(str).str.strip().str.upper().isin(["YES", "TES"])
    bp = bp[flagged | (depth > 0)].copy()
    bp["depth_cm"] = depth.reindex(bp.index)
    bp["date"] = bp.WAKTU_TEXT.astype(str).str[:10]
    bp["geometry"] = bp.geometry.make_valid()
    dates = sorted(bp.date.unique())
    log(f"BPBD: {len(bp):,} flooded-RT records, {bp.ID_RT_AR.nunique():,} RT, {len(dates)} event dates {dates[0]}…{dates[-1]}")
    pts = bp.copy()
    pts["geometry"] = bp.geometry.representative_point()
    j = gpd.sjoin(pts[["ID_RT_AR", "date", "depth_cm", "geometry"]], kel[["idx", "geometry"]], predicate="within", how="inner")
    agg = j.groupby("idx").agg(fl_events=("date", "nunique"), fl_rt=("ID_RT_AR", "nunique"),
                               fl_records=("date", "size"), fl_depth_max=("depth_cm", "max"), fl_depth_med=("depth_cm", "median"))
    for c in agg.columns:
        out[c] = agg[c].reindex(out.index)
    out[["fl_events", "fl_rt", "fl_records"]] = out[["fl_events", "fl_rt", "fl_records"]].fillna(0).astype(int)
    flooded = unary_union(bp.to_crs(UTM).geometry.values)
    out["fl_share"] = (kel_utm.geometry.intersection(flooded).area / kel_utm.area).values
    out["fl_share"] = out.fl_share.clip(0, 1)

    # --- January 2020 flood: UNOSAT water extent ∪ EOS-ARIA flood proxy map ---
    zu = RAW / "floods" / "unosat_FL20200101IDN_shp.zip"
    un = gpd.read_file(f"zip://{zu}!{next(n for n in zipfile.ZipFile(zu).namelist() if 'WaterExtent' in n and n.endswith('.shp'))}")
    ze = RAW / "floods" / "eos_aria_20200102_fpm_shp.zip"
    eos = gpd.read_file(f"zip://{ze}!{zip_member(ze, '.shp')}")
    eos = eos[eos.DN > 0] if "DN" in eos.columns else eos
    w, s, e, n = kel.total_bounds
    un = un.to_crs("EPSG:4326").clip((w, s, e, n)).to_crs(UTM)
    eos = eos.to_crs("EPSG:4326").clip((w, s, e, n)).to_crs(UTM)
    u_un = unary_union(un.geometry.make_valid().values) if len(un) else None
    u_eos = unary_union(eos.geometry.make_valid().values) if len(eos) else None
    both = unary_union([g for g in (u_un, u_eos) if g is not None])
    out["f20_unosat"] = (kel_utm.geometry.intersection(u_un).area / kel_utm.area).values if u_un is not None else 0.0
    out["f20_eos"] = (kel_utm.geometry.intersection(u_eos).area / kel_utm.area).values if u_eos is not None else 0.0
    out["f20"] = (kel_utm.geometry.intersection(both).area / kel_utm.area).values
    log(f"2020 flood: UNOSAT {un.area.sum() / 1e6 if len(un) else 0:,.0f} km² and EOS {eos.area.sum() / 1e6 if len(eos) else 0:,.0f} km² "
        f"inside DKI; kelurahan touched {(out.f20 > 0.01).sum()}")
    return out


# ----------------------------------------------------------------------------- gates
def gates(kel, tab, prior):
    from scipy.stats import spearmanr

    main = tab[~kel.island.values]
    pop_total = float(tab["pop"].sum())
    ghs_total = float(tab.ghs_pop.sum())
    covered = float((main["cov"] >= config.GATE_COVERAGE_MIN).mean())
    c4 = {
        "kelurahan_n": int(len(kel)), "kelurahan_ok": int(len(kel)) == config.GATE_KELURAHAN_N,
        "worldpop_total": round(pop_total), "official_2020": config.OFFICIAL_DKI_POP_2020,
        "worldpop_vs_official": round(pop_total / config.OFFICIAL_DKI_POP_2020 - 1, 3),
        "worldpop_ok": abs(pop_total / config.OFFICIAL_DKI_POP_2020 - 1) <= config.GATE_POP_TOL,
        "ghsl_total": round(ghs_total), "ghsl_vs_worldpop": round(ghs_total / pop_total - 1, 3),
        "ghsl_ok": abs(ghs_total / pop_total - 1) <= config.GATE_GHSL_TOL,
        "mainland_covered_share": round(covered, 3), "coverage_ok": covered >= config.GATE_COVERAGE_SHARE,
        "pop_nan": int(tab["pop"].isna().sum()), "mainland_zero_pop": int((main["pop"] <= 0).sum()),
    }
    c4["pass"] = bool(c4["kelurahan_ok"] and c4["worldpop_ok"] and c4["ghsl_ok"] and c4["coverage_ok"]
                      and c4["pop_nan"] == 0 and c4["mainland_zero_pop"] == 0)
    log(f"[gate G-C4] {'PASS' if c4['pass'] else 'FAIL'} — {c4['kelurahan_n']} kelurahan; WorldPop {pop_total:,.0f} "
        f"({c4['worldpop_vs_official']:+.1%} vs census); GHSL {ghs_total:,.0f} ({c4['ghsl_vs_worldpop']:+.1%} vs WorldPop); "
        f"{covered:.0%} of mainland kelurahan ≥{config.GATE_COVERAGE_MIN:.0%} InSAR coverage")

    # G-C5 (spec's plausibility check): exposure ranking vs observed BPBD flood frequency
    ok = main[(main["cov"] >= 0.2) & main.low1.notna()]
    idx = (ok.low1.rank() + (-ok.v_p10).rank()) / 2
    rho = lambda a, b: round(float(spearmanr(a, b, nan_policy="omit").statistic), 3)  # noqa: E731
    c5 = {
        "n": int(len(ok)),
        "rho_index_vs_flood_events": rho(idx, ok.fl_events),
        "rho_low1_vs_flood_events": rho(ok.low1, ok.fl_events),
        "rho_p10_vs_flood_events": rho(-ok.v_p10, ok.fl_events),
        "rho_2020_vs_flood_events": rho(ok.f20, ok.fl_events),
        "rho_2020_vs_flood_share": rho(ok.f20, ok.fl_share),
        "top20_exposure_2020_share": round(float(ok.loc[idx.nlargest(20).index].f20.mean()), 3),
        "all_2020_share": round(float(ok.f20.mean()), 3),
        "threshold": config.GATE_FLOOD_SPEARMAN,
    }
    c5["pass"] = bool(c5["rho_index_vs_flood_events"] >= config.GATE_FLOOD_SPEARMAN)
    log(f"[gate G-C5] {'PASS' if c5['pass'] else 'FAIL'} — Spearman exposure index vs BPBD flood events ρ={c5['rho_index_vs_flood_events']} "
        f"(low-ground share {c5['rho_low1_vs_flood_events']}, p10 rate {c5['rho_p10_vs_flood_events']}); "
        f"2020 extent share top-20 exposure {c5['top20_exposure_2020_share']:.0%} vs all {c5['all_2020_share']:.0%}")
    g = dict(prior.get("gates", {}))
    g.update({"G-C3": "deferred", "G-C4": c4["pass"], "G-C5": c5["pass"], "exposure_sanity": c4, "flood_plausibility": c5})
    return g


# ----------------------------------------------------------------------------- main
def main() -> int:
    import geopandas as gpd
    import rasterio

    DERIVED.mkdir(parents=True, exist_ok=True)
    prior = json.loads((DERIVED / "stats.json").read_text()) if (DERIVED / "stats.json").exists() else {}
    if not prior.get("gates", {}).get("G-C1") or not prior["gates"].get("G-C2"):
        log("WARNING: grid.py gates G-C1/G-C2 not both PASS in stats.json — run `make grid` first")

    pop, T, shape, area = analysis_grid()
    ghs_pop, built_m2, elev_mean, ground, vel, vel_sd = load_layers(T, shape, area)
    kel = load_kelurahan()
    ids = burn(kel, T, shape)

    tab = zonal(kel, ids, pop, ghs_pop, built_m2, elev_mean, ground, vel, vel_sd, area)
    fl = floods(kel)
    tab = tab.join(fl)
    series, city = timeseries(kel, ids, pop, built_m2, ground, vel)
    for t in THRESHOLDS:
        tab[f"pop_{t}_2025"] = series[t]["pop"][:, 0]
        tab[f"pop_{t}_2030"] = series[t]["pop"][:, YEARS.index(2030)]
        tab[f"pop_{t}_2050"] = series[t]["pop"][:, -1]
        tab[f"built_{t}_2025"] = series[t]["built"][:, 0]
        tab[f"built_{t}_2050"] = series[t]["built"][:, -1]
    # the clock: years until the kelurahan's low ground (p10 of the ground proxy) crosses a threshold
    for t, thr in THRESHOLDS.items():
        tab[f"clock_med_{t}"] = pd.Series([clock_years(g, v, thr) for g, v in zip(tab.ground_p10, tab.v_med)], index=tab.index, dtype="float64")
        tab[f"clock_p10_{t}"] = pd.Series([clock_years(g, v, thr) for g, v in zip(tab.ground_p10, tab.v_p10)], index=tab.index, dtype="float64")
    tab = tab.astype({c: "float64" for c in ("fl_events", "fl_rt", "fl_records")})
    # island kelurahan lie outside the grid/velocity field → metrics are not meaningful
    isl = kel.island.values
    tab.loc[isl, [c for c in tab.columns if c not in ("cells", "grid_area_km2", "pop", "ghs_pop", "built_km2", "built_share")]] = np.nan

    kel_out = kel.drop(columns="idx").join(tab.reset_index(drop=True))
    kel_out.to_parquet(DERIVED / "kelurahan_exposure.parquet")
    kel_out.to_file(DERIVED / "kelurahan_exposure.geojson", driver="GeoJSON")
    long = []
    for t in THRESHOLDS:
        for i, kid in enumerate(kel.id):
            for j, y in enumerate(YEARS):
                long.append((kid, t, y, series[t]["pop"][i, j], series[t]["built"][i, j]))
    pd.DataFrame(long, columns=["id", "threshold", "year", "pop_below", "built_below_km2"]).to_parquet(DERIVED / "exposure_timeseries.parquet")
    city.to_parquet(DERIVED / "city_timeseries.parquet")

    prof = dict(driver="GTiff", height=shape[0], width=shape[1], count=7, dtype="float32", crs="EPSG:4326",
                transform=T, nodata=np.nan, tiled=True, compress="deflate")
    with rasterio.open(DERIVED / "analysis_grid_100m.tif", "w", **prof) as dst:
        for b, (name, arr) in enumerate([("pop", pop), ("ghs_pop", ghs_pop), ("built_m2", built_m2), ("elev_mean", elev_mean),
                                          ("ground", ground), ("vel_cmyr", vel), ("kel_idx", ids.astype("float32"))], start=1):
            dst.write(arr.astype("float32"), b)
            dst.set_band_description(b, name)

    # --- headline numbers ---
    main = kel_out[~kel_out.island].copy()
    ranked = main[main["cov"] >= config.GATE_COVERAGE_MIN]
    c = lambda t, y, k: float(city[(city.threshold == t) & (city.year == y)][k].iloc[0])  # noqa: E731
    head = {
        "pop_total": round(float(kel_out["pop"].sum())), "ghs_pop_total": round(float(kel_out.ghs_pop.sum())),
        "built_total_km2": round(float(kel_out.built_km2.sum()), 1), "area_km2": round(float(kel_out.area_km2.sum()), 1),
        "pop_fast2": round(float(main.pop_fast2.sum())), "pop_fast1": round(float(main.pop_fast1.sum())),
        "area_fast1_share": round(float((main.fast1 * main["cov"] * main.grid_area_km2).sum() / (main["cov"] * main.grid_area_km2).sum()), 3),
        "area_fast2_share": round(float((main.fast2 * main["cov"] * main.grid_area_km2).sum() / (main["cov"] * main.grid_area_km2).sum()), 3),
        "n_kel_p10_fast2": int((ranked.v_p10 < -2).sum()), "n_kel_med_fast2": int((ranked.v_med < -2).sum()),
        "n_kel_med_fast1": int((ranked.v_med < -1).sum()), "n_ranked": int(len(ranked)),
        "below": {t: {str(y): {"pop": round(c(t, y, "pop_below")), "built_km2": round(c(t, y, "built_below_km2"), 1)}
                      for y in (2025, 2030, 2040, 2050)} for t in THRESHOLDS},
        "fastest_p10": ranked.nsmallest(10, "v_p10")[["id", "name", "kota", "v_p10", "v_med", "pop"]].round(2).to_dict("records"),
        "fastest_med": ranked.nsmallest(10, "v_med")[["id", "name", "kota", "v_p10", "v_med", "pop"]].round(2).to_dict("records"),
        "soonest_0m": ranked[ranked.clock_med_0m > 0].nsmallest(10, "clock_med_0m")[["id", "name", "kota", "ground_p10", "v_med", "clock_med_0m", "clock_p10_0m"]].round(2).to_dict("records"),
        "already_below_0m": int((ranked.clock_med_0m == 0).sum()),
        "most_exposed_2050": ranked.nlargest(10, "pop_1m_2050")[["id", "name", "kota", "pop", "pop_1m_2025", "pop_1m_2050", "built_1m_2050"]].round(1).to_dict("records"),
        "most_flooded": main.nlargest(10, "fl_events")[["id", "name", "kota", "fl_events", "fl_rt", "fl_share", "v_p10"]].round(3).to_dict("records"),
    }
    log(f"DKI: {head['pop_total']:,} people (WorldPop) / {head['ghs_pop_total']:,} (GHSL); {head['pop_fast2']:,} on ground sinking >2 cm/yr; "
        f"below +1 m: {head['below']['1m']['2025']['pop']:,} (2025) → {head['below']['1m']['2050']['pop']:,} (2050)")
    f = head["fastest_p10"][0]
    log(f"fastest kelurahan (p10): {f['name']} ({f['kota']}) {f['v_p10']:+.2f} cm/yr; median {f['v_med']:+.2f}")

    g = gates(kel, tab, prior)
    stats = dict(prior)
    stats.update({
        "fuse": {"generated": date.today().isoformat(), "years": YEARS, "thresholds": THRESHOLDS, "ground_floor_m": GROUND_FLOOR,
                 "assumption": "linear extrapolation of 2017–2023 rates; GLO-30 (2011–2015 acquisitions) as the 2025 surface; "
                               "cells without InSAR coverage held stable", **head},
        "gates": g,
    })
    (DERIVED / "stats.json").write_text(json.dumps(_clean(stats), indent=1, allow_nan=False))
    log(f"wrote kelurahan_exposure.{{parquet,geojson}}, exposure_timeseries.parquet, analysis_grid_100m.tif, stats.json")
    return 0 if (g.get("G-C1") and g.get("G-C2") and g["G-C4"]) else 1


def _clean(o):
    """Recursively replace NaN/inf with None and numpy scalars with Python ones."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


if __name__ == "__main__":
    sys.exit(main())
