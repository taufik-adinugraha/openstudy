"""Stage 1 · ingest — raw inputs; alerts national, deep-dive layers per focus province.

  Hansen GFC     v1.12 (loss 2001-2024, CC BY 4.0 "even commercially"): lossyear (~26-83 MB/tile),
                 treecover2000 (~280-320 MB/tile), datamask (~22 MB/tile) for the 14 tiles
                 covering Indonesian land — ~5-6 GB total, anonymous HTTPS from
                 storage.googleapis.com/earthenginepartners-hansen. v1.13 (loss through 2025)
                 exists in the GFW API; pin its GCS path when published.
  RADD alerts    GFW Data API dataset wur_radd_alerts (CC BY 4.0, weekly), raster tile set
                 grid 10/100000 (10 m), pixel_meaning date_conf. Needs the FREE GFW API key
                 (config.GFW_API_KEY; sign-up → token → apikey, expires yearly). Download
                 returns 307 → presigned S3 URL; range requests work. National pull ≈ 1 GB
                 (per-tile 53-100 MB, verified).
  GLAD-L alerts  umd_glad_landsat_alerts (CC BY 4.0, daily, 30 m), same path — the optical
                 second opinion for gate G-H3.
  palm extent    Descals et al. 2024 v1.2 (Zenodo 13379129, CC BY 4.0): 2021 extent 10 m
                 industrial/smallholder (156 MB) + planting year 1990-2021 (147 MB).
  mills          Universal Mill List via GFW Data API (gfw_universal_mill_list v202508,
                 CC BY 4.0, 1,231 Indonesian mills, 239 RSPO-certified).
  peat/primary   gfw_peatlands (CC BY 4.0) + Margono primary forest 2000 (CC BY 4.0, 60 MB).
  boundaries     HDX COD-AB gdb (CC BY-IGO): ADM1 34 / ADM2 522, P-coded.
  NOT stored     Concession boundaries: GFW's IDN vectors are view-only and the ministry's
                 ArcGIS/WMS carry no licence text → live reference overlay at view time only
                 (config.CONCESSION_OVERLAYS; decision pending user verification).

`--alerts-only` (weekly cron) re-pulls just the latest RADD/GLAD versions.
Outputs: data/raw/<source>/<version>/ + manifest.json (sizes, sha256, fetch date).
"""

from __future__ import annotations

import sys

import config


def main(alerts_only: bool = False) -> None:
    config.RAW.mkdir(parents=True, exist_ok=True)
    print("TODO ingest →", config.RAW, "| alerts-only:", alerts_only)
    print("  Hansen tiles:", len(config.HANSEN_TILES_IDN), "| alert tiles:", len(config.ALERT_TILES_IDN))
    print("  GFW key set :", bool(config.GFW_API_KEY))


if __name__ == "__main__":
    main(alerts_only="--alerts-only" in sys.argv)
