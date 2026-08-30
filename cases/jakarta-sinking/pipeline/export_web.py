"""Stage: export (spec C3) — NaN-safe web view-models from data/derived/.

  web/public/data/kelurahan.geojson   261 mainland kelurahan, simplified (~15 m), {id, name}
  web/public/data/exposure.json       per-kelurahan metrics + 2025→2050 series, city series, ramps
  web/public/data/velocity_field.png  600×600 encoded field: R = 128 + 16·v (cm/yr), G = 100·sd, A = valid
  web/public/data/velocity_rgba.png   the same field coloured with the bathymetric ramp (MapLibre overlay)
  web/public/data/hotspots.json       named hotspots + GNSS stations with their checks
  web/public/hero.jpg                 the velocity field rendered for the hero (1800×1080)
  web/src/data/summary.json           headline numbers, gates, tables for server-rendered copy

Every JSON is written with allow_nan=False after NaN → null cleaning. The six island
kelurahan (Kepulauan Seribu) are outside the velocity field and are exported in the
table with null metrics, not on the map.
"""

from __future__ import annotations

import json
import sys
from datetime import date

import numpy as np
import pandas as pd

import config

DERIVED = config.DATA_DIR / "derived"
WEB = config.CASE_DIR / "web"
PUB = WEB / "public" / "data"
SRC_DATA = WEB / "src" / "data"

# bathymetric ramp (tokens.css [data-case="sinking"]): stable navy → fast luminous cyan
RAMP_V = [(0.0, "#0A1F3A"), (-1.5, "#144E7A"), (-3.0, "#1F8FB5"), (-4.5, "#35C6E0"), (-6.0, "#D8F6FC")]
# coral ramp for flood exposure (never shares a hue with subsidence)
RAMP_X = ["#0F1B2B", "#5A2E33", "#B4503F", "#F26D5B", "#FFCFC3"]
GROUND = (7, 13, 22)                                                   # --ground #070D16
HERO_BOUNDS = (106.55, -6.42, 107.15, -6.06)                          # 0.6° × 0.36° → 1800×1080 at 3000 px/°
HERO_PPD = 3000

KEL_FIELDS = {  # parquet column → web key (rounding)
    "name": ("name", None), "kecamatan": ("kec", None), "kota": ("kota", None), "area_km2": ("area", 3),
    "cov": ("cov", 3), "v_mean": ("v_mean", 2), "v_med": ("v_med", 2), "v_p10": ("v_p10", 2), "v_sd": ("v_sd", 2),
    "fast2": ("fast2", 3), "fast1": ("fast1", 3), "pop": ("pop", 0), "ghs_pop": ("ghs_pop", 0), "pop_fast2": ("pop_fast2", 0),
    "built_km2": ("built", 3), "built_share": ("built_share", 3), "elev_mean": ("elev", 1), "ground_med": ("ground_med", 2),
    "ground_p10": ("ground_p10", 2), "low1": ("low1", 3), "low2": ("low2", 3),
    "fl_events": ("fl_ev", 0), "fl_rt": ("fl_rt", 0), "fl_share": ("fl_share", 3), "fl_depth_max": ("fl_depth", 0),
    "f20": ("f20", 3), "f20_unosat": ("f20_un", 3), "f20_eos": ("f20_eos", 3),
    "clock_med_0m": ("clk_med_0m", 1), "clock_p10_0m": ("clk_p10_0m", 1), "clock_med_1m": ("clk_med_1m", 1), "clock_p10_1m": ("clk_p10_1m", 1),
}


def log(msg: str) -> None:
    print(f"[export] {msg}", flush=True)


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if o is pd.NA or o is pd.NaT:
        return None
    return o


def dump(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(obj), allow_nan=False, separators=(",", ":"), ensure_ascii=False))
    log(f"{path.relative_to(config.CASE_DIR)} ({path.stat().st_size / 1e3:,.0f} kB)")


def rnd(v, d):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    if d is None:
        return v
    return int(round(v)) if d == 0 else round(float(v), d)


def hex_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def colour_field(vel):
    """Velocity array → RGB uint8 via RAMP_V (NaN → ground)."""
    xs = [-p[0] for p in RAMP_V]                          # 0 … 6 (subsidence magnitude)
    mag = np.clip(np.nan_to_num(-vel, nan=0.0), 0, 6)
    rgb = np.empty(vel.shape + (3,), dtype=np.uint8)
    for ch in range(3):
        ys = [hex_rgb(p[1])[ch] for p in RAMP_V]
        rgb[..., ch] = np.where(np.isfinite(vel), np.interp(mag, xs, ys), GROUND[ch]).astype(np.uint8)
    return rgb


def fill_gaps(a, passes=2, min_neighbours=3):
    """DISPLAY ONLY. The ~75 m point cloud leaves about half of the 0.001° cells empty as
    isolated speckle. Fill a NaN cell from its 3×3 neighbours when ≥ 3 are valid, twice;
    large gaps (sea, vegetation, decorrelated ground) stay empty. Analysis uses the raw grid."""
    from scipy.ndimage import uniform_filter

    out = a.copy()
    for _ in range(passes):
        valid = np.isfinite(out)
        s = uniform_filter(np.where(valid, out, 0.0).astype("float32"), 3, mode="constant") * 9
        n = uniform_filter(valid.astype("float32"), 3, mode="constant") * 9
        fill = (~valid) & (n >= min_neighbours - 0.5)
        out = np.where(fill, s / np.maximum(n, 1), out).astype("float32")
    return out


def field_pngs(kel):
    import rasterio
    from PIL import Image
    from rasterio import features
    from rasterio.transform import from_origin
    from scipy.ndimage import zoom

    with rasterio.open(DERIVED / "velocity_ohenhen2026_cmyr.tif") as src:
        raw = src.read(1)
        b = src.bounds
    with rasterio.open(DERIVED / "velocity_ohenhen2026_sd.tif") as src:
        sd = fill_gaps(src.read(1))
    vel = fill_gaps(raw)
    valid = np.isfinite(vel)
    log(f"field gap fill for display: {np.isfinite(raw).mean():.0%} → {valid.mean():.0%} of cells")
    enc = np.zeros(vel.shape + (4,), dtype=np.uint8)
    enc[..., 0] = np.clip(np.round(128 + 16 * np.nan_to_num(vel)), 0, 255)
    enc[..., 1] = np.clip(np.round(100 * np.nan_to_num(sd)), 0, 255)
    enc[..., 3] = np.where(valid, 255, 0)
    Image.fromarray(enc, "RGBA").save(PUB / "velocity_field.png", optimize=True)
    rgba = np.dstack([colour_field(vel), np.where(valid, 255, 0).astype(np.uint8)])
    Image.fromarray(rgba, "RGBA").save(PUB / "velocity_rgba.png", optimize=True)
    log(f"velocity_field.png + velocity_rgba.png ({vel.shape[1]}×{vel.shape[0]}, {valid.mean():.0%} valid)")

    # --- hero: ×3 bicubic upsample (NaN-aware), crop to HERO_BOUNDS, faint kelurahan edges ---
    k = int(round(HERO_PPD * 0.001))                                    # 0.001° cells → 3 px
    v0 = zoom(np.nan_to_num(vel), k, order=3)
    m0 = zoom(valid.astype("float32"), k, order=1)
    vz = np.where(m0 > 0.5, v0 / np.maximum(m0, 1e-6), np.nan)          # normalise by the smoothed mask
    w, s, e, n = HERO_BOUNDS
    W, H = int(round((e - w) * HERO_PPD)), int(round((n - s) * HERO_PPD))
    c0, r0 = int(round((w - b.left) * HERO_PPD)), int(round((b.top - n) * HERO_PPD))
    crop = vz[r0:r0 + H, c0:c0 + W]
    rgb = colour_field(crop).astype("float32")
    edges = features.rasterize(((g, 1) for g in kel.geometry.boundary), out_shape=(H, W),
                               transform=from_origin(w, n, 1 / HERO_PPD, 1 / HERO_PPD), fill=0, all_touched=True, dtype="uint8")
    line = np.array(hex_rgb("#8CA0B5"), dtype="float32")
    rgb = np.where(edges[..., None] > 0, 0.72 * rgb + 0.28 * line, rgb)
    Image.fromarray(rgb.clip(0, 255).astype(np.uint8), "RGB").save(WEB / "public" / "hero.jpg", quality=86, optimize=True, progressive=True)
    log(f"hero.jpg {W}×{H} bounds {HERO_BOUNDS}")
    return [b.left, b.bottom, b.right, b.top]


def main() -> int:
    import geopandas as gpd

    PUB.mkdir(parents=True, exist_ok=True)
    SRC_DATA.mkdir(parents=True, exist_ok=True)
    stats = json.loads((DERIVED / "stats.json").read_text())
    fuse = stats.get("fuse")
    if not fuse:
        log("stats.json has no fuse block — run `make fuse` first")
        return 1
    kel = gpd.read_parquet(DERIVED / "kelurahan_exposure.parquet")
    ts = pd.read_parquet(DERIVED / "exposure_timeseries.parquet")
    city = pd.read_parquet(DERIVED / "city_timeseries.parquet")
    years = fuse["years"]
    main_k = kel[~kel.island].copy()
    log(f"{len(kel)} kelurahan ({len(main_k)} on the map)")

    # --- geometry: simplified mainland polygons, 5-decimal coordinates ---
    geo = main_k[["id", "name", "geometry"]].copy()
    geo["geometry"] = geo.geometry.simplify(0.00012, preserve_topology=True)
    gj = json.loads(geo.to_json(drop_id=True))
    for f in gj["features"]:
        def rc(c):
            return [rc(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], 5), round(c[1], 5)]
        f["geometry"]["coordinates"] = rc(f["geometry"]["coordinates"])
    dump(PUB / "kelurahan.geojson", gj)

    # --- field PNGs + hero ---
    field_bounds = field_pngs(main_k)

    # --- exposure table + series ---
    series = {}
    for (kid, thr), grp in ts.groupby(["id", "threshold"]):
        grp = grp.sort_values("year")
        series.setdefault(kid, {})[thr] = (grp.pop_below.round(0).tolist(), grp.built_below_km2.round(4).tolist())
    table = {}
    for _, r in kel.iterrows():
        row = {}
        for col, (key, d) in KEL_FIELDS.items():
            row[key] = rnd(r[col], d) if col in kel.columns else None
        row["island"] = bool(r.island)
        if not r.island:
            for thr in ("1m", "0m"):
                p, bl = series[r.id][thr]
                row[f"pop{thr[0]}"] = [int(x) for x in p]
                row[f"built{thr[0]}"] = bl
        table[r.id] = row
    city_s = {}
    for thr, grp in city.groupby("threshold"):
        grp = grp.sort_values("year")
        city_s[f"pop{thr[0]}"] = grp.pop_below.round(0).astype(int).tolist()
        city_s[f"built{thr[0]}"] = grp.built_below_km2.round(3).tolist()
    w, s, e, n = main_k.total_bounds
    exposure = {
        "generated": fuse["generated"], "window": stats.get("window", "2017–2023"), "base_year": years[0], "years": years,
        "thresholds": fuse["thresholds"], "bounds": [round(w, 4), round(s, 4), round(e, 4), round(n, 4)],
        "field": {"file": "velocity_field.png", "rgba": "velocity_rgba.png", "bounds": field_bounds, "encode": {"offset": 128, "scale": 16, "sd_scale": 100},
                  "note": "display only: isolated empty cells filled from ≥3 of 8 neighbours (two passes); analysis uses the raw grid"},
        "ramp_v": RAMP_V, "ramp_x": RAMP_X,
        "city": {"pop": fuse["pop_total"], "ghs_pop": fuse["ghs_pop_total"], "built": fuse["built_total_km2"], **city_s},
        "kel": table,
    }
    dump(PUB / "exposure.json", exposure)

    # --- hotspots + GNSS (grid.py) ---
    from grid import GNSS, HOTSPOTS
    hot = [{"name": k, "lon": lon, "lat": lat, **stats["hotspots_cmyr"].get(k, {})} for k, (lon, lat) in HOTSPOTS.items()]
    gnss = [{"station": st, "lon": lon, "lat": lat, "gnss_mm": pub, **stats["gates"]["gnss"].get(st, {})} for st, (lon, lat, pub) in GNSS.items()]
    dump(PUB / "hotspots.json", {"hotspots": hot, "gnss": gnss, "window": stats.get("window")})

    # --- summary for server-rendered copy ---
    g = stats["gates"]
    summary = {
        "generated": fuse["generated"], "built_on": date.today().isoformat(), "window": stats.get("window", "2017–2023"),
        "source": stats.get("source"), "points": stats.get("points"), "unique": stats.get("unique"),
        "n_kel": len(kel), "n_mainland": len(main_k), "n_ranked": fuse["n_ranked"],
        "pop_total": fuse["pop_total"], "ghs_pop_total": fuse["ghs_pop_total"], "official_pop": config.OFFICIAL_DKI_POP_2020,
        "built_total_km2": fuse["built_total_km2"], "area_km2": fuse["area_km2"],
        "pop_fast2": fuse["pop_fast2"], "pop_fast1": fuse["pop_fast1"],
        "area_fast1_share": fuse["area_fast1_share"], "area_fast2_share": fuse["area_fast2_share"],
        "n_kel_p10_fast2": fuse["n_kel_p10_fast2"], "n_kel_med_fast2": fuse["n_kel_med_fast2"], "n_kel_med_fast1": fuse["n_kel_med_fast1"],
        "below": fuse["below"], "already_below_0m": fuse["already_below_0m"],
        "fastest_p10": fuse["fastest_p10"], "fastest_med": fuse["fastest_med"], "soonest_0m": fuse["soonest_0m"],
        "most_exposed_2050": fuse["most_exposed_2050"], "most_flooded": fuse["most_flooded"],
        "hotspots": hot, "gnss": gnss,
        "gates": {
            "G-C1": {"pass": g.get("G-C1"), "label": "Literature agreement",
                     "detail": "NW-coast hotspots: fastest decile ≥ 2 cm/yr and neighbourhood median ≥ 1 cm/yr; central Jakarta within ±1 cm/yr"},
            "G-C2": {"pass": g.get("G-C2"), "label": "GNSS agreement", "detail": "InSAR vertical rate within ±5 mm/yr of Susilo et al. 2023 at CJKT, CTGR, CBTU"},
            "G-C3": {"pass": None, "label": "Own LiCSBAS run vs the deposit", "detail": "Deferred — 13 GB LiCSAR download; pixelwise r ≥ 0.7, hotspot medians within 1 cm/yr"},
            "G-C4": {"pass": g.get("G-C4"), "label": "Exposure sanity", "detail": g.get("exposure_sanity")},
            "G-C5": {"pass": g.get("G-C5"), "label": "Flood plausibility", "detail": g.get("flood_plausibility")},
        },
        "assumption": fuse["assumption"],
    }
    dump(SRC_DATA / "summary.json", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
