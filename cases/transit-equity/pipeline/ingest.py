"""Stage 1 · ingest — every raw input for Jabodetabek (bbox in config.py).

  GTFS          TransJakarta official feed https://gtfs.transjakarta.co.id/files/file_gtfs.zip
                (2.5 MB, Last-Modified 2026-07-27; 240 routes incl. 98 Mikrotrans, full
                frequencies.txt; licence unstated → attribution + written confirmation
                pending, see README decisions). 81 archived versions on Transitland.
                Bonus: Bogor angkot GTFS (CC0, Mobility Database mdb-1229).
  OSM           Geofabrik asia/indonesia/java-latest.osm.pbf (896 MB, daily) → osmium extract
                to the Jabodetabek bbox (~120 MB) — streets, footways, rail geometries, POIs.
  population    WorldPop 2020 constrained 100 m IDN (103 MB) clipped; GHSL GHS-POP E2020 100 m
                tile as cross-check.
  jobs proxy    GHSL GHS-BUILT-S NRES E2020 100 m (non-residential built-up surface) — the
                only open, spatially explicit employment proxy; OSM office/shop/industrial POI
                density as the second proxy (both disclosed as proxies).
  facilities    OSM amenity=hospital|clinic|doctors + healthsites.io export (ODbL); Kemenkes
                referral-hospital list if a downloadable open list is confirmed.
  polygons      HDX COD-AB IDN ADM4 (kelurahan/desa, CC BY-IGO, P-coded) filtered to the 14
                Jabodetabek kabupaten/kota (~1,500 units); DKI's 267 kelurahan via Jakarta
                Satu for the cross-check.
  wealth        Case F's benchmarked kecamatan poverty estimates (own product) + BPS regency
                P0 (14 units, official but coarse). Meta's RWI was scouted and REJECTED:
                CC BY-NC 4.0, incompatible with a commercial demo (README decisions).

Outputs: data/raw/<source>/ + manifest.json (sizes, sha256, fetch date).
"""

from __future__ import annotations

import config


def main() -> None:
    config.RAW.mkdir(parents=True, exist_ok=True)
    print("TODO ingest →", config.RAW)
    print("  GTFS :", config.TRANSJAKARTA_GTFS_URL)
    print("  OSM  :", config.OSM_JAVA_PBF_URL, "→ clip", config.BBOX)


if __name__ == "__main__":
    main()
