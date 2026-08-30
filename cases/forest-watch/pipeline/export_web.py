"""Stage 6 · export — web view-models (each ≤ 3 MB, NaN-safe; quality gate 4).

  web/public/data/adm1.geojson         provinces (simplified) with 2001-2024 loss series
  web/public/data/clusters_<prov>.json  clusters (centroid, ha, weeks, class, mill/concession
                                       ids) — points, not polygons, so a province stays < 2 MB
  web/public/data/weeks_<prov>.json     weekly ledger for the hero ignition animation
  web/public/data/mills.json            UML mills with catchment scores (concession outlines
                                        are NOT exported — they render as a live labelled
                                        reference overlay from config.CONCESSION_OVERLAYS)
  web/public/data/link_summary.json     linkage-class shares per province × quarter
  web/public/data/stats.json            gates + vintages (Hansen v1.12 · RADD vYYYYMMDD ·
                                        concession vintages · UML vintage)
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO export →", config.WEB_DATA)


if __name__ == "__main__":
    main()
