"""Stage 8 · export — web view-models (each ≤ 3 MB, NaN-safe; quality gate 4).

  web/public/data/adm4.geojson        kelurahan polygons simplified (~1,600) with ids
  web/public/data/access.json         per kelurahan × scenario × cutoff: the access metrics
  web/public/data/isochrones/<id>.json  per-origin reachable-hex lists at 30/45/60 (from the
                                      matrix; served as small files, fetched on click)
  web/public/data/network.json        route geometries (TransJakarta corridors, rail lines) for
                                      the hero and the network chapter
  web/public/data/equity.json         Lorenz/Gini/Palma, access-vs-wealth scatter, lists
  web/public/data/stats.json          gates + vintage stamps (GTFS Last-Modified, OSM date,
                                      WorldPop 2020, GHSL 2020, RWI vintage)
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO export →", config.WEB_DATA)


if __name__ == "__main__":
    main()
