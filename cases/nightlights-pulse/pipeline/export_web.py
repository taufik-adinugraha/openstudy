"""Export web view-models from the ledger + boundaries (spec §A3 · publish, local slice).

Writes into web/:
  public/data/regencies.geojson   simplified 514-regency polygons (shapeID only)
  public/data/ledger.json         per-region series {month: [sol, mean, n_px]}
  public/hero.jpg                 compressed hero render (latest month)
  src/data/summary.json           build-time stats (latest month, Java share, top lists)
  src/styles/tokens.css           copy of the shared design tokens

Rerun any time — the dashboard grows as the backfill fills the ledger.
"""

from __future__ import annotations

import json
import shutil
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

import config

WEB = config.CASE_DIR / "web"
JAVA_PROVINCES = {"31", "32", "33", "34", "35", "36"}
JAVA_LAND_SHARE = 0.066  # Java ≈ 6.6% of Indonesia's land area


def main() -> int:
    (WEB / "public" / "data").mkdir(parents=True, exist_ok=True)
    (WEB / "src" / "data").mkdir(parents=True, exist_ok=True)
    (WEB / "src" / "styles").mkdir(parents=True, exist_ok=True)

    shutil.copy(config.REPO_ROOT / "shared" / "design" / "tokens.css",
                WEB / "src" / "styles" / "tokens.css")

    xwalk = pd.read_csv(config.CROSSWALK.parent / "region_crosswalk.csv", dtype=str)
    xwalk = xwalk[xwalk["match"] != "EXCLUDED"]

    ledger = pd.read_parquet(config.LEDGER)
    ledger = ledger.merge(xwalk[["shapeID", "bps_code"]],
                          left_on="region_id", right_on="shapeID", how="inner")
    months = sorted(ledger["month"].unique())
    latest = months[-1]

    # --- boundaries, simplified for the browser ---
    gdf = gpd.read_file(config.BOUNDARIES)[[config.REGION_ID, "geometry"]]
    gdf = gdf[gdf[config.REGION_ID].isin(set(xwalk["shapeID"]))]
    gdf["geometry"] = gdf.geometry.simplify(0.01, preserve_topology=True)
    gdf["geometry"] = shapely.set_precision(gdf.geometry.values, 1e-4)
    gdf = gdf.rename(columns={config.REGION_ID: "id"})
    gdf.to_file(WEB / "public" / "data" / "regencies.geojson", driver="GeoJSON")

    # --- per-region series ---
    regions: dict = {}
    for _, row in xwalk.iterrows():
        regions[row["shapeID"]] = {"name": row["shapeName"], "bps": row["bps_code"], "series": {}}
    fin = lambda v, nd: round(float(v), nd) if np.isfinite(v) else 0.0
    for _, r in ledger.iterrows():
        # fully-masked monsoon cells yield NaN — browsers reject NaN in JSON,
        # so export 0 and let n_px=0 carry the "no data" signal
        n_px = int(r["n_px"]) if np.isfinite(r["n_px"]) else 0
        regions[r["region_id"]]["series"][r["month"]] = [
            fin(r["sol"], 1), fin(r["mean_rad"], 2), n_px]
    (WEB / "public" / "data" / "ledger.json").write_text(
        json.dumps({"months": months, "regions": regions}, allow_nan=False))

    # --- build-time summary ---
    national = ledger.groupby("month").agg(sol=("sol", "sum"), n_px=("n_px", "sum"))
    peak_px = national["n_px"].max()
    latest_rows = ledger[ledger["month"] == latest]
    java = latest_rows[latest_rows["bps_code"].str[:2].isin(JAVA_PROVINCES)]
    top_sol = latest_rows.nlargest(10, "sol")[["region_name", "sol"]]
    top_mean = latest_rows.nlargest(10, "mean_rad")[["region_name", "mean_rad"]]
    summary = {
        "latestMonth": latest,
        "monthCount": len(months),
        "javaShareSOL": round(float(java["sol"].sum() / latest_rows["sol"].sum()), 3),
        "javaLandShare": JAVA_LAND_SHARE,
        "national": [
            {"month": m, "sol": round(float(v.sol), 0),
             "coverage": round(float(v.n_px / peak_px), 3)}
            for m, v in national.iterrows()],
        "topSol": [{"name": r.region_name, "v": round(float(r.sol))} for r in top_sol.itertuples()],
        "topMean": [{"name": r.region_name, "v": round(float(r.mean_rad), 1)}
                    for r in top_mean.itertuples()],
    }
    (WEB / "src" / "data" / "summary.json").write_text(json.dumps(summary, indent=1, allow_nan=False))

    # --- hero image ---
    preview = config.DATA_DIR / "raw" / "bm" / latest / "VJ146A3_preview.png"
    if preview.exists():
        from PIL import Image
        img = Image.open(preview)
        img = img.resize((2560, int(img.height * 2560 / img.width)), Image.LANCZOS)
        img.convert("RGB").save(WEB / "public" / "hero.jpg", quality=82, optimize=True)
        print(f"[export] hero.jpg from {latest} preview")
    else:
        print(f"[export] WARNING no preview for {latest}; hero.jpg not refreshed")

    print(f"[export] {len(regions)} regions, {len(months)} months ({months[0]}..{latest}), "
          f"Java share of lights {summary['javaShareSOL']:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
