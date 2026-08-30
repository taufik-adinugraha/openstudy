"""Stage 2 · features — zonal features per regency (ADM2) and kecamatan (ADM3).

Method: every raster/vector input is reduced to a 100 m analysis grid first (DuckDB spatial
+ exactextract), then aggregated to both admin levels with the SAME code path, so the
downscale step applies a model trained on regency features to kecamatan features that were
built identically (no train/serve skew).

Feature families (all per capita or per km² where it matters):
  buildings   count density, footprint area share, median / p90 footprint size, share < 40 m²
              (small-roof share is the strongest single welfare proxy in the literature),
              nearest-neighbour compactness; Open Buildings confidence-weighted.
  lights      Black Marble annual radiance: mean, sum per capita, lit share, 2016→latest trend.
  population  WorldPop density, GHSL SMOD urban class shares.
  land cover  WorldCover shares: built-up, cropland, tree cover, bare, water, mangrove.
  built-up    GHS-BUILT-S total and non-residential surface per capita.
  roads       OSM road density by class; distance-to-primary-road (population-weighted).
  optional    Sentinel-2 annual NDVI / NDBI medians (80 m) when the STAC pull ran.

Outputs: data/features_adm2.parquet (519 rows × ~60 features × years 2016-2025 where inputs
are annual; static features repeated) and data/features_adm3.parquet (~7,200 rows).
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO features →", config.FEATURES_ADM2, config.FEATURES_ADM3)


if __name__ == "__main__":
    main()
