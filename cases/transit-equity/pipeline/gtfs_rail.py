"""Stage 2 · rail — minimal hand-encoded GTFS for the rail modes that publish no feed.

No official GTFS exists for KRL Commuter Line (KAI Commuter), MRT Jakarta, LRT Jakarta or
LRT Jabodebek (checked Transitland / Mobility Database 2026-08-30 — see spec §G2). This stage
builds frequency-based GTFS zips from:
  stops      OSM railway=station nodes on each route relation (route=train|subway|light_rail)
  shapes     OSM route relation geometries
  headways   published peak/off-peak headways per line (config.RAIL_LINES, each with its
             source URL and the date it was read) → frequencies.txt, not stop_times
  run times  inter-station times from published end-to-end journey times, distributed by
             distance; transfers.txt for the integration points (Dukuh Atas, Manggarai,
             Tanah Abang, Cawang, Harjamukti).

Everything hand-encoded is labelled as such in the methodology; the case's honesty rule is
that rail travel times carry a ±15 % caveat until an official feed exists.

Outputs: data/gtfs/rail_krl.zip, rail_mrt.zip, rail_lrtjkt.zip, rail_lrtjabodebek.zip.
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO rail GTFS →", config.GTFS_DIR, "| lines:", list(config.RAIL_LINES))


if __name__ == "__main__":
    main()
