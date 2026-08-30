"""Stage 3 · network — the multimodal routing graph (r5py 1.1.7 over OSM + GTFS).

Why r5py and not OTP / a hand-rolled Dijkstra: R5 is frequency-aware (RAPTOR over a departure
window), which is exactly what a 60-minute access metric needs — a Dijkstra over OSM + stops
would ignore headway waiting, the dominant term for a 20-minute Mikrotrans.

RESOURCE DEVIATION FROM THE SPEC (README): the spec pins a 10 G JVM heap. Three other jobs
share this 16 GB box, so the heap is 2.5 G (config.R5_MAX_MEMORY) and the OSM input is the
tag-filtered routing subset, not the full clip. `configure()` must run before r5py is
imported anywhere — r5py reads `--max-memory` from sys.argv at import time.

Outputs: data/network/build_report.json (stop/route counts, GTFS warnings).
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import config
import ingest
import util
from util import log


def configure() -> None:
    """Pin the JVM heap before r5py is imported (r5py parses sys.argv at import)."""
    if "r5py" in sys.modules:
        raise RuntimeError("configure() must run before r5py is imported")
    sys.argv = [sys.argv[0], "--max-memory", config.R5_MAX_MEMORY]
    cfg = Path.home() / ".config" / "r5py.yml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f"max-memory: {config.R5_MAX_MEMORY}\n"
                   f"temporary-directory: {config.DATA_DIR / 'tmp'}\n")
    (config.DATA_DIR / "tmp").mkdir(parents=True, exist_ok=True)


def gtfs_paths(with_rail: bool = True) -> list[str]:
    paths = sorted(p for p in config.GTFS_DIR.glob("*.zip")
                   if with_rail or "rail" not in p.name)
    return [str(p) for p in paths]


def build():
    """Build the TransportNetwork. ~5-10 min; the caller keeps it for the whole run."""
    configure()
    util.guard_ram()
    import r5py
    log("building R5 network with heap", config.R5_MAX_MEMORY)
    tn = r5py.TransportNetwork(str(ingest.OSM_ROUTING), gtfs_paths())
    log("network built")
    return tn


def gtfs_summary() -> dict:
    """Counts straight from the feeds — the honest inventory for the methodology chapter."""
    import pandas as pd
    out = {}
    for z in sorted(config.GTFS_DIR.glob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            def rd(n):
                return pd.read_csv(zf.open(n)) if n in zf.namelist() else pd.DataFrame()
            routes, stops, trips, freq = rd("routes.txt"), rd("stops.txt"), rd("trips.txt"), rd("frequencies.txt")
            out[z.name] = {
                "routes": int(len(routes)), "stops": int(len(stops)), "trips": int(len(trips)),
                "frequencies_rows": int(len(freq)),
                "route_types": {str(k): int(v) for k, v in
                                routes.get("route_type", pd.Series(dtype=int)).value_counts().items()},
                "headways_min": sorted({round(h / 60, 1) for h in freq.get("headway_secs", [])}) or None,
            }
    return out


def main() -> None:
    util.guard_disk()
    rep = {"gtfs": gtfs_summary(),
           "osm_routing_pbf_bytes": ingest.OSM_ROUTING.stat().st_size,
           "heap": config.R5_MAX_MEMORY}
    tn = build()
    try:
        rep["r5_transit_layer_stops"] = int(tn.transit_layer.stop_id_for_index.size())
    except Exception as e:
        rep["r5_transit_layer_stops"] = f"unavailable ({type(e).__name__})"
    config.NETWORK_DIR.mkdir(parents=True, exist_ok=True)
    (config.NETWORK_DIR / "build_report.json").write_text(json.dumps(rep, indent=2))
    log(json.dumps(rep["gtfs"], indent=2))


if __name__ == "__main__":
    main()
