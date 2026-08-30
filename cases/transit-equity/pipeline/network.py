"""Stage 3 · network — build the multimodal routing graph with r5py.

r5py.TransportNetwork(osm_pbf=<Jabodetabek clip>, gtfs=[transjakarta.zip, rail_*.zip]).
Requires a JDK 21 on PATH (r5py downloads the R5 jar, ~100 MB, on first use). Memory: set
R5PY max heap ≈ 6 GB (config.R5_MAX_MEMORY) — a Jabodetabek graph (~1 M OSM nodes, ~250
routes) builds in ~5-10 min on the 16 GB server and is cached as data/network/network.dat.

Why r5py and not OpenTripPlanner / custom Dijkstra: r5 computes frequency-aware (RAPTOR)
travel-time distributions over a departure window natively, which is exactly what the
60-minute access metric needs; a custom Dijkstra on OSM+stops would ignore headway waiting.

Outputs: data/network/ (r5 network cache) + data/network/build_report.json (node/edge counts,
GTFS validation warnings from gtfs-kit).
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO network →", config.NETWORK_DIR, "| heap:", config.R5_MAX_MEMORY)


if __name__ == "__main__":
    main()
