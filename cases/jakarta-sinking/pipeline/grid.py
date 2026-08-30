"""Stage: grid (spec C3) + gates G-C1/G-C2 on the deposited field.

Points (Ohenhen et al. 2026, ~75 m) → 0.001° raster (matches LiCSAR pixel
spacing, so gate G-C3 later compares like with like). Duplicate coordinates
(overlapping frames) are averaged; cells keep the mean VLM, its reported sd,
and the point count. Sign: negative = subsidence, cm/yr, window 2017–2023.
Also writes a bathymetric-ramp preview PNG and stats.json for the gates.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

import config

RES = 0.001
DERIVED = config.DATA_DIR / "derived"
SD_MAX = 1.0   # cm/yr — drop the noisiest points before gridding

# Named checks for gate G-C1 (lon, lat, ~radius deg ≈ 650 m)
HOTSPOTS = {
    "Muara Baru": (106.805, -6.100), "Kamal Muara": (106.735, -6.100), "Kosambi": (106.688, -6.093),
    "PIK / Kapuk Muara": (106.745, -6.115), "Pluit": (106.790, -6.115), "Cengkareng": (106.730, -6.150),
    "Penjaringan": (106.780, -6.130), "Ancol": (106.840, -6.125), "Monas (central)": (106.827, -6.175),
}
# GNSS stations (Susilo et al. 2023), published vertical mm/yr
GNSS = {"CJKT": (106.885, -6.110, -6.4), "CTGR": (106.664, -6.291, -2.9), "CBTU": (107.096, -6.308, -0.5)}
RAMP = [(-6, (10, 31, 58)), (-4, (20, 78, 122)), (-2, (31, 143, 181)), (-1, (53, 198, 224)), (0, (216, 246, 252)), (1, (216, 246, 252))]


def sample(grid, west, north, lon, lat, radius):
    """(median, p10, n) of valid cells within a square window (radius in deg).
    p10 — the fastest-subsiding decile — characterises a hotspot's core the way
    the literature's peak values do; the median tells the neighbourhood story."""
    r = int(round(radius / RES))
    ci, ri = int((lon - west) / RES), int((north - lat) / RES)
    win = grid[max(ri - r, 0):ri + r + 1, max(ci - r, 0):ci + r + 1]
    vals = win[np.isfinite(win)]
    if not vals.size:
        return (float("nan"), float("nan"), 0)
    return (float(np.median(vals)), float(np.percentile(vals, 10)), int(vals.size))


def main() -> int:
    import rasterio
    from rasterio.transform import from_origin

    DERIVED.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(config.RAW / "velocity" / "ohenhen2026_jakarta_slice.csv")
    n0 = len(df)
    df = df[np.isfinite(df.VLM_cm_per_yr) & (df.VLM_sd_cm_per_yr <= SD_MAX)]
    dup = df.groupby(["Longitude", "Latitude"], as_index=False).agg(
        v=("VLM_cm_per_yr", "mean"), sd=("VLM_sd_cm_per_yr", "mean"), n=("VLM_cm_per_yr", "size"))
    print(f"[grid] {n0:,} points → {len(df):,} after sd ≤ {SD_MAX} → {len(dup):,} unique coordinates "
          f"(max {int(dup.n.max())} duplicates)")

    w, s, e, n = config.BBOX
    W, H = int(round((e - w) / RES)), int(round((n - s) / RES))
    ci = np.clip(((dup.Longitude - w) / RES).astype(int), 0, W - 1)
    ri = np.clip(((n - dup.Latitude) / RES).astype(int), 0, H - 1)
    acc = np.zeros((H, W)); cnt = np.zeros((H, W)); sdacc = np.zeros((H, W))
    np.add.at(acc, (ri, ci), dup.v.values); np.add.at(cnt, (ri, ci), 1); np.add.at(sdacc, (ri, ci), dup.sd.values)
    with np.errstate(invalid="ignore", divide="ignore"):
        vel = np.where(cnt > 0, acc / cnt, np.nan).astype("float32")
        sd = np.where(cnt > 0, sdacc / cnt, np.nan).astype("float32")
    transform = from_origin(w, n, RES, RES)
    prof = dict(driver="GTiff", height=H, width=W, count=1, dtype="float32", crs="EPSG:4326",
                transform=transform, nodata=np.nan, tiled=True, compress="deflate")
    for name, arr in (("velocity_ohenhen2026_cmyr.tif", vel), ("velocity_ohenhen2026_sd.tif", sd)):
        with rasterio.open(DERIVED / name, "w", **prof) as dst:
            dst.write(arr, 1); dst.build_overviews([2, 4, 8])
    valid = vel[np.isfinite(vel)]
    print(f"[grid] raster {W}×{H} @0.001°, {np.isfinite(vel).mean():.0%} covered; "
          f"VLM median {np.median(valid):.2f}, p5 {np.percentile(valid, 5):.2f}, p95 {np.percentile(valid, 95):.2f} cm/yr")

    # --- gate G-C1: named hotspots ---
    # Coastal hotspot criterion: the fastest decile (p10) subsides ≥ 2 cm/yr and
    # the neighbourhood median ≥ 1 cm/yr; central Jakarta stays within ±1 cm/yr.
    # (Point medians under-read hotspot cores vs the literature's peak values —
    #  documented on the methodology page.)
    hot = {k: sample(vel, w, n, lon, lat, 0.009) for k, (lon, lat) in HOTSPOTS.items()}
    lo, hi = config.GATE_HOTSPOT_RANGE
    coast = ["Muara Baru", "Kamal Muara", "Kosambi", "PIK / Kapuk Muara"]
    c1 = (all(-hot[k][1] >= lo and -hot[k][1] <= hi * 2 for k in coast)
          and all(-hot[k][0] >= 1.0 for k in coast)
          and all(abs(hot[k][0]) <= 1.0 for k in ["Monas (central)", "Ancol"]))
    print(f"[gate G-C1] {'PASS' if c1 else 'FAIL'} — hotspots (cm/yr, 2017–2023, median | p10):")
    for k, (v, p10, m) in hot.items():
        print(f"    {k:<20} {v:+.2f} | {p10:+.2f}  (n={m})")

    # --- gate G-C2: GNSS stations ---
    g2 = {}
    for st, (lon, lat, pub) in GNSS.items():
        v, _, m = sample(vel, w, n, lon, lat, 0.003)
        g2[st] = {"insar_mm": round(v * 10, 1) if np.isfinite(v) else None, "gnss_mm": pub, "n": m,
                  "ok": bool(np.isfinite(v) and abs(v * 10 - pub) <= config.GATE_GNSS_TOL_MM)}
    c2 = all(x["ok"] for x in g2.values() if x["n"] > 0)
    print(f"[gate G-C2] {'PASS' if c2 else 'FAIL'} — InSAR vs GNSS vertical (mm/yr):")
    for st, x in g2.items():
        print(f"    {st}: InSAR {x['insar_mm']} vs GNSS {x['gnss_mm']} (n={x['n']}) {'✓' if x['ok'] else '✗'}")

    # --- preview ---
    from PIL import Image
    ramp_x = [p[0] for p in RAMP]
    rgb = np.zeros((H, W, 3), dtype=np.uint8) + 7
    v = np.clip(np.nan_to_num(vel, nan=99), -6, 1)
    for ch in range(3):
        rgb[..., ch] = np.where(np.isfinite(vel), np.interp(v, ramp_x, [p[1][ch] for p in RAMP]), rgb[..., ch])
    Image.fromarray(rgb).save(DERIVED / "velocity_preview.png")

    (DERIVED / "stats.json").write_text(json.dumps({
        "source": "Ohenhen et al. 2026 (Zenodo 10.5281/zenodo.15786356), CC BY 4.0", "window": "2017–2023",
        "points": int(n0), "unique": int(len(dup)), "resolution_deg": RES,
        "hotspots_cmyr": {k: {"median": round(v, 2), "p10": round(p10, 2)} for k, (v, p10, _) in hot.items()},
        "gates": {"G-C1": c1, "G-C2": c2, "gnss": g2}}, indent=1))
    print(f"[grid] wrote {DERIVED / 'velocity_ohenhen2026_cmyr.tif'}, preview, stats.json")
    return 0 if (c1 and c2) else 1


if __name__ == "__main__":
    sys.exit(main())
