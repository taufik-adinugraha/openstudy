"""Stage 1 · ingest — pull every raw input, clipped to Indonesia (or Java when SCOPE=java).

Inputs (all public; URLs and licences in config.py, verified 2026-08-30):
  boundaries   geoBoundaries gbOpen IDN ADM2 (519 regencies, 2020 vintage, CC BY 3.0 IGO) — same
               frozen vintage as Flagship A, so the pemekaran crosswalk is shared;
               HDX COD-AB IDN ADM3 (kecamatan) for the downscale targets.
  ground truth BPS WebAPI dynamic tables var 621 (P0 %), 622 (P1), 623 (P2), 624 (poverty line)
               by kabupaten/kota, years 2016-2025 (one request per var × year; 'th' is required).
  buildings    Google Open Buildings v3 polygons, S2 level-4 cells covering Indonesia
               (36 files, 15.8 GB gz; Java = 3 cells, 8.4 GB) — streamed cell by cell, reduced to
               per-cell building counts / area / size histograms on a 100 m grid, then deleted.
  lights       NASA Black Marble annual composites already on the dev server at
               cases/nightlights-pulse/data/raw/bm/YYYY-01/ (reuse; never re-download).
  population   WorldPop 2020 constrained 100 m IDN (103 MB).
  land cover   ESA WorldCover 2021 v200 3°x3° tiles over Indonesia (~60 tiles, ~2 GB).
  built-up     GHSL GHS-BUILT-S E2020 + NRES 100 m (JRC tiles over Indonesia).
  roads        Geofabrik indonesia-latest.osm.pbf (1.73 GB) → highway=* lines only.
  optional     Sentinel-2 L2A annual spectral indices at 80 m from Earth Search STAC COG overviews.

Outputs: data/raw/<source>/ (immutable, vintage-stamped) and data/raw/manifest.json listing
every file with size + sha256 so the rebuild is reproducible (quality gate 1).

Heavy: ~20 GB download, mostly Open Buildings; run once, keep the reduced grids only.
"""

from __future__ import annotations

import config


def main() -> None:
    config.RAW.mkdir(parents=True, exist_ok=True)
    print("TODO ingest → ", config.RAW)
    print("  boundaries :", config.BOUNDARIES_ADM2_URL)
    print("  BPS vars   :", config.BPS_VARS)
    print("  OB cells   :", len(config.OPEN_BUILDINGS_CELLS_IDN), "Indonesia /", len(config.OPEN_BUILDINGS_CELLS_JAVA), "Java")
    print("  lights     :", config.BLACK_MARBLE_ANNUAL_DIR)


if __name__ == "__main__":
    main()
