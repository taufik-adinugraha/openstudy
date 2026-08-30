"""Stage 6 · export — web view-models (each ≤ 3 MB, NaN-safe JSON; quality gate 4).

  web/public/data/adm2.geojson       simplified regency polygons (shared with Flagship A) + id
  web/public/data/adm3.geojson       simplified kecamatan polygons (~7,200; ≤ 3 MB after
                                     0.002° simplification, or PMTiles if it will not fit)
  web/public/data/ledger.json        per regency: BPS P0 series 2016-2025, model prediction,
                                     CV fold, residual, top-5 SHAP contributions
  web/public/data/estimates.json     per kecamatan: p0_hat, p10, p90, benchmark factor, year
  web/public/data/skill.json         gate results, Java/off-Java split, random-vs-spatial CV
  web/public/data/stats.json         copy of the validated stats for the brief slot

Vintage stamp: BPS release (March survey), Black Marble year, Open Buildings v3 (2023-05),
WorldCover 2021 — written into stats.json → rendered on every view (quality gate 5).
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO export →", config.WEB_DATA)


if __name__ == "__main__":
    main()
