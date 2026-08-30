"""Gas-flare mask (spec §A3 · mask) — a DERIVED series; the raw ledger is kept.

Source (decision, 2026-08-30): Elvidge, C.D. & Zhizhin, M. (2021), *Global Gas
Flare Survey by Infrared Imaging, VIIRS Nightfire, 2012–2019*, ORNL DAAC,
https://doi.org/10.3334/ORNLDAAC/1874 — NASA EOSDIS data policy: "openly
shared, without restriction" (attribution requested; downloads need the same
Earthdata bearer token the ingest already uses). EOG's live VNF flare catalog
needs a manual account and is NOT used. The survey is a site list per year
(lat/lon, type upstream / refinery / lng, BCM, detection frequency, clear obs).

Method
  1. Indonesian sites 2012→2019, deduplicated to ~1 km cells (the 2012–2016
     files share one set of resolved locations; 2017–2019 are re-resolved),
     kept only when seen in ≥ MIN_YEARS of the 8 surveys — one-off detections
     (a 2016-only "flare" at the Sorowako smelter, a 2018-only one at Konawe's
     ferronickel park) are hot industry, not persistent flares.
  2. Circular buffers rasterised on the Black Marble 15" grid. Primary radius
     3 km — measured on the 2025 composite, isolated flares put ~60% of their
     excess light within 1 km and 84% within 3 km, and the median ring radiance
     reaches background beyond 3 km — with the spec's 5 km and a tight 1.5 km
     as sensitivity columns (5 km swallows whole towns: Kota Sorong −72%).
  3. For every annual composite on disk (data/raw/bm/YYYY-01/*A4_radiance.tif,
     2012→2025) and the latest monthly masked composite: per regency, the share
     of sum-of-lights inside the buffers. models.deseason multiplies each month's
     raw SOL by (1 − share of that year) → sol_deflared; 2026 reuses 2025.

Limits (disclosed on the page): sites first lit after 2019 are not in the
survey; flares fused into industrial towns (Bontang LNG, Cilegon) inevitably
take some genuine city light with them — the tight column bounds that.

Outputs: data/flares_regency.parquet (region × year), data/flares_sites.csv.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import config

RAW_FLARES = config.DATA_DIR / "raw" / "flares"
RAW_BM = config.DATA_DIR / "raw" / "bm"
OUT = config.DATA_DIR / "flares_regency.parquet"
SITES = config.DATA_DIR / "flares_sites.csv"
BUFFERS = {"": config.FLARE_BUFFER_KM, "_wide": 5.0, "_tight": 1.5}
MIN_YEARS = 3            # persistence: seen in ≥3 of the 8 annual surveys
KM_PER_DEG = 111.32
SURVEY = "Elvidge & Zhizhin 2021, VIIRS Nightfire global flare survey 2012–2019 (ORNL DAAC, doi:10.3334/ORNLDAAC/1874)"
LICENCE = "NASA EOSDIS data policy — openly shared without restriction; cite the dataset"


def load_sites() -> pd.DataFrame:
    files = sorted(RAW_FLARES.glob("eog_global_flare_survey_*_flare_list.csv"))
    if not files:
        sys.exit(f"[flares] no survey files under {RAW_FLARES} — download eog_global_flare_survey_<year>_flare_list.csv "
                 "from https://doi.org/10.3334/ORNLDAAC/1874 (Earthdata bearer token)")
    w, s, e, n = config.BBOX
    frames = []
    for f in files:
        year = int(f.name.split("_")[4])
        df = pd.read_csv(f, encoding_errors="replace")
        df = df[(df["cntry_iso"] == "IDN") & df["latitude"].between(s, n) & df["longitude"].between(w, e)]
        frames.append(df.assign(year=year)[["latitude", "longitude", "flr_volume", "avg_temp",
                                            "dtc_freq", "clr_obs", "flr_type", "year"]])
    raw = pd.concat(frames, ignore_index=True)
    raw["cell"] = (raw["latitude"] / 0.01).round().astype(int).astype(str) + ":" + \
                  (raw["longitude"] / 0.01).round().astype(int).astype(str)
    sites = raw.groupby("cell").agg(
        lat=("latitude", "mean"), lon=("longitude", "mean"),
        years_seen=("year", "nunique"), first_year=("year", "min"), last_year=("year", "max"),
        bcm_max=("flr_volume", "max"), temp_k=("avg_temp", "mean"), dtc_freq=("dtc_freq", "mean"),
        flr_type=("flr_type", lambda s: s.mode().iat[0]),
    ).reset_index(drop=True)
    sites["kept"] = sites["years_seen"] >= MIN_YEARS
    print(f"[flares] {len(raw)} survey rows (IDN) → {len(sites)} distinct sites; "
          f"types {sites['flr_type'].value_counts().to_dict()}; "
          f"{int(sites['kept'].sum())} persistent (≥{MIN_YEARS} survey years), {int((~sites['kept']).sum())} one-offs dropped")
    return sites


def assign_regions(sites: pd.DataFrame, gdf) -> pd.DataFrame:
    import geopandas as gpd

    pts = gpd.GeoDataFrame(sites, geometry=gpd.points_from_xy(sites["lon"], sites["lat"]), crs="EPSG:4326")
    joined = gpd.sjoin(pts, gdf[[config.REGION_ID, config.REGION_NAME, "geometry"]], how="left", predicate="within")
    joined = joined.rename(columns={config.REGION_ID: "region_id", config.REGION_NAME: "region_name"})
    return pd.DataFrame(joined.drop(columns=["geometry", "index_right"]))


def reference_grid():
    import rasterio

    for path in sorted(RAW_BM.glob("*/*A4_radiance.tif")):
        with rasterio.open(path) as src:
            return src.transform, src.shape
    sys.exit("[flares] no annual composite raster found under data/raw/bm/YYYY-01/")


def rasterize_buffers(sites: pd.DataFrame, km: float, transform, shape) -> np.ndarray:
    from rasterio import features
    from shapely.geometry import Point

    shapes = [(Point(r.lon, r.lat).buffer(km / KM_PER_DEG), 1) for r in sites.itertuples()]
    return features.rasterize(shapes, out_shape=shape, transform=transform, fill=0, dtype="uint8").astype(bool)


def region_raster(gdf, transform, shape) -> np.ndarray:
    from rasterio import features

    shapes = [(geom, i + 1) for i, geom in enumerate(gdf.geometry)]
    return features.rasterize(shapes, out_shape=shape, transform=transform, fill=0, dtype="int32")


def composites() -> list[tuple[str, str, Path]]:
    """(label, product, path) for every annual composite + the latest monthly masked raster."""
    out = []
    for d in sorted(RAW_BM.glob("*-01")):
        for p in d.glob("*A4_radiance.tif"):
            out.append((d.name[:4], p.name.split("_")[0], p))
    monthly = sorted((config.DATA_DIR / "derived" / "bm").glob("*/*_radiance_masked.tif"))
    if monthly:
        p = monthly[-1]
        out.append((p.parent.name, p.name.split("_")[0], p))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()
    import geopandas as gpd
    import rasterio

    sites = load_sites()
    gdf = gpd.read_file(config.BOUNDARIES)[[config.REGION_ID, config.REGION_NAME, "geometry"]]
    sites = assign_regions(sites, gdf)
    sites.to_csv(SITES, index=False)
    kept = sites[sites["kept"]]
    per_region_sites = kept.groupby("region_id").size()

    transform, shape = reference_grid()
    ids = region_raster(gdf, transform, shape)
    masks = {suffix: rasterize_buffers(kept, km, transform, shape) for suffix, km in BUFFERS.items()}
    print(f"[flares] grid {shape}; buffer pixels: " +
          ", ".join(f"{BUFFERS[sfx]} km → {m.sum():,}" for sfx, m in masks.items()))

    n = len(gdf)
    rows = []
    for label, product, path in composites():
        with rasterio.open(path) as src:
            rad = src.read(1)
        rad = np.where(np.isfinite(rad) & (rad > 0), rad, 0.0).astype("float32")
        flat_ids, flat_rad = ids.ravel(), rad.ravel()
        total = np.bincount(flat_ids, weights=flat_rad, minlength=n + 1)[1:]
        df = pd.DataFrame({
            "region_id": gdf[config.REGION_ID].values, "region_name": gdf[config.REGION_NAME].values,
            "year": label, "product": product, "sol_total": total,
        })
        for sfx, m in masks.items():
            sel = m.ravel()
            part = np.bincount(flat_ids[sel], weights=flat_rad[sel], minlength=n + 1)[1:]
            df[f"sol_flare{sfx}"] = part
            df[f"share{sfx}"] = np.divide(part, total, out=np.zeros_like(part), where=total > 0)
        df["n_sites"] = df["region_id"].map(per_region_sites).fillna(0).astype(int)
        rows.append(df)
        nat = df["sol_flare"].sum() / max(total.sum(), 1)
        print(f"[flares] {label} {product}: national share in {BUFFERS['']} km buffers {nat:.2%}", flush=True)
        del rad, flat_rad

    out = pd.concat(rows, ignore_index=True)
    out.to_parquet(OUT, index=False)
    latest = out[out["year"].str.len() == 4]
    latest = latest[latest["year"] == latest["year"].max()]
    top = latest.nlargest(12, "share")[["region_name", "share", "share_wide", "share_tight", "n_sites"]]
    print(f"[flares] {len(out)} rows → {OUT.name}; top regencies by share of SOL removed ({latest['year'].iat[0]}):")
    for r in top.itertuples():
        print(f"    {r.region_name:<26} {BUFFERS['']} km {r.share:6.1%}   5 km {r.share_wide:6.1%}   1.5 km {r.share_tight:6.1%}   sites {r.n_sites:3d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
