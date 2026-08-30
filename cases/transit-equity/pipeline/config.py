"""Case G constants — Transit Access & Urban Equity. Spec: docs/spec-transit-equity.html (G2–G4).

Data path verified by reconnaissance 2026-08-30. Routing engine: r5py 1.1.x (frequency-aware
RAPTOR over OSM + GTFS; dual GPL-3.0-or-later / MIT; JDK 21) — see network.py for why not
OTP / custom Dijkstra. Licence rule as for every case: CC BY / CC BY-IGO / ODbL-attribution /
CC0 only; the Meta RWI (CC BY-NC) found during scouting is not used.
"""

import os
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = CASE_DIR / "data"
RAW = DATA_DIR / "raw"
REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_DATA = CASE_DIR / "web" / "public" / "data"


def _load_env() -> None:
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}

# Jabodetabek working bbox (west, south, east, north): DKI + Bogor + Depok + Tangerang + Bekasi
BBOX = (106.30, -6.95, 107.30, -5.85)
# BPS kabupaten/kota codes making up the region (13 units + Kepulauan Seribu); COD-AB P-code = "ID" + code
JABODETABEK_BPS = ("3101", "3171", "3172", "3173", "3174", "3175",          # DKI Jakarta
                   "3201", "3271", "3276",                                  # Bogor kab, Bogor kota, Depok
                   "3216", "3275",                                          # Bekasi kab, Bekasi kota
                   "3603", "3671", "3674")                                  # Tangerang kab, kota, Tangsel

# --- transit feeds -------------------------------------------------------------------------
# Official TransJakarta feed (HEAD 2026-08-30: 200, 2,497,332 bytes, Last-Modified 2026-07-27).
# Contents verified: 240 routes (all route_type 3, incl. 98 "JAK.*" Mikrotrans), 700 trips,
# 8,091 stops, frequencies.txt covers every trip (headways 6/10/15/20/30/60 min), shapes.txt,
# calendar 2004→2027-12-31, no feed_info.txt. Transitland f-transjakarta~id / Mobility Database
# mdb-1909 mirror it (81 archived versions). Licence: NOT STATED anywhere — attribution +
# written confirmation is a pending decision (README).
TRANSJAKARTA_GTFS_URL = "https://gtfs.transjakarta.co.id/files/file_gtfs.zip"
TRANSITLAND_FEED = "https://www.transit.land/feeds/f-transjakarta~id"
MOBILITY_DB_FEED = "https://mobilitydatabase.org/feeds/gtfs/mdb-1909"
# Bonus feed: Bogor angkot routes, CC0 (Mobility Database mdb-1229), 841 KB, 2026-08-14.
BOGOR_ANGKOT_GTFS_URL = "http://michielbdejong.com/angkots-gtfs.zip"
GTFS_DIR = DATA_DIR / "gtfs"

# Rail has no GTFS (Transitland operators search + Mobility Database catalogue, 2026-08-30:
# only o-qqgu-pttransportasijakarta exists for Jakarta) → hand-encoded frequency GTFS from
# published headways + OSM route relations (bbox holds 129 route=train, 8 light_rail, 4 subway
# relations). Headways below were read on 2026-08-30 from the cited pages; re-read at build.
RAIL_LINES = {
    "mrt_north_south": {"operator": "MRT Jakarta", "osm_route": "subway", "stations": 13,
                        "headway_peak_min": 5, "headway_off_min": 10, "end_to_end_min": 30,
                        "source": "https://jakartamrt.co.id/id/jadwal-keberangkatan"},
    "lrt_jabodebek":   {"operator": "KAI · LRT Jabodebek", "osm_route": "light_rail",
                        "headway_peak_min": 4.5, "headway_off_min": 6.5, "service": "05:53-23:11",
                        "source": "https://lrtjabodebek.kai.id/jadwal-keberangkatan"},
    "lrt_jakarta":     {"operator": "LRT Jakarta", "osm_route": "light_rail", "stations": 6,
                        "headway_peak_min": 10, "headway_off_min": 10,
                        "source": "https://www.lrtjakarta.co.id/ (403 to fetchers; read in browser)"},
    "krl_bogor":       {"operator": "KAI Commuter", "osm_route": "train",
                        "headway_peak_min": 5, "headway_off_min": 10,
                        "source": "https://kci.id/perjalanan-krl/jadwal-kereta (GAPEKA 2025 lookup)"},
    "krl_cikarang":    {"operator": "KAI Commuter", "osm_route": "train",
                        "headway_peak_min": 8, "headway_off_min": 15, "source": "kci.id"},
    "krl_rangkasbitung": {"operator": "KAI Commuter", "osm_route": "train",
                          "headway_peak_min": 12, "headway_off_min": 20, "source": "kci.id"},
    "krl_tangerang":   {"operator": "KAI Commuter", "osm_route": "train",
                        "headway_peak_min": 20, "headway_off_min": 30, "source": "kci.id"},
    "krl_tanjung_priok": {"operator": "KAI Commuter", "osm_route": "train",
                          "headway_peak_min": 30, "headway_off_min": 60, "source": "kci.id"},
}
TRANSFER_HUBS = ("Dukuh Atas", "Manggarai", "Tanah Abang", "Cawang", "Harjamukti", "Sudirman", "Jakarta Kota")
RAIL_TT_CAVEAT_PCT = 15         # displayed wherever a rail time appears, until an official feed exists

# --- streets: OSM (ODbL) -------------------------------------------------------------------
OSM_JAVA_PBF_URL = "https://download.geofabrik.de/asia/indonesia/java-latest.osm.pbf"  # 896 MB, daily
OSM_CLIP = RAW / "osm" / "jabodetabek.osm.pbf"                                            # ~120-150 MB after osmium extract
OVERPASS_URL = "https://overpass-api.de/api/interpreter"                                 # rail relation lookups only

# --- people, jobs, facilities, polygons, wealth -------------------------------------------
# GHSL tile R10_C29 verified to span 99.5-110.1 E / 0-8.1 S → one tile covers all Jabodetabek.
WORLDPOP_URL = ("https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/IDN/v1/100m/"
                "constrained/idn_pop_2025_CN_100m_R2025A_v1.tif")                        # 169 MB, CC BY 4.0
GHSL_FTP = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
GHSL_POP_TILE = ("GHS_POP_GLOBE_R2023A/GHS_POP_E2025_GLOBE_R2023A_54009_100/V1-0/tiles/"
                 "GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0_R10_C29.zip")                # 22.7 MB
GHSL_NRES_TILE = ("GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_NRES_E2020_GLOBE_R2023A_54009_100/V1-0/tiles/"
                  "GHS_BUILT_S_NRES_E2020_GLOBE_R2023A_54009_100_V1_0_R10_C29.zip")      # 1.74 MB — jobs proxy
HEALTHSITES_HDX = "https://data.humdata.org/dataset/indonesia-healthsites"              # ODbL, GeoJSON 18 MB, 2026-08-28
HDX_MOH_HOSPITALS = "https://data.humdata.org/dataset/indonesia-health-facilities"       # CC BY, hospitals 2018, 123 KB
HDX_HOT_HEALTH = "https://data.humdata.org/dataset/hotosm_idn_health_facilities"         # ODbL, 2026-05-05
OSM_HEALTH_TAGS = {"amenity": ("hospital", "clinic", "doctors")}
OSM_JOB_TAGS = {"office": "*", "shop": "*", "landuse": ("industrial", "commercial", "retail")}
COD_AB_GDB_URL = ("https://data.humdata.org/dataset/84a1d98a-790b-4d66-9d14-bbfa48500802/resource/"
                  "c740a308-0a63-46d6-ab15-b041e62eff58/download/idn_admin_boundaries.gdb.zip")   # 219 MB, CC BY-IGO, ADM4 81,912
JAKSATU_KELURAHAN = ("https://jakartasatu.jakarta.go.id/server/rest/services/Batas_Administrasi_Update/"
                     "Batas_Administrasi_DKI_Jakarta_Update_View/FeatureServer/3")        # 267, cross-check
# Equity axis: Case F's benchmarked kecamatan poverty estimates (own product) + BPS regency P0.
CASE_F_ESTIMATES = REPO_ROOT / "cases" / "poverty-map" / "data" / "estimates_adm3.parquet"
BPS_P0_VAR = 621
REJECTED = {"Meta Relative Wealth Index (HDX)": "CC BY-NC 4.0 — non-commercial; not used",
            "Kemenkes facility registries": "no anonymous download; layanandata/SATUSEHAT need registration",
            "Nusantara/GeoRSPO etc.": "n/a for this case"}

# --- routing -------------------------------------------------------------------------------
R5_MAX_MEMORY = "10G"                        # r5py default is 80 % of RAM; pin it on the 16 GB box
DEPARTURE_DATE = "2026-09-02"                # a Wednesday inside the GTFS calendar
DEPARTURE_WINDOW = ("07:00", "09:00")
CUTOFFS_MIN = (30, 45, 60)
MAX_TRIP_MIN = 90
HEX_M = 500
SCENARIOS = {"all": ("WALK", "TRANSIT"), "no_rail": ("WALK", "BUS"), "walk": ("WALK",)}
GRAVITY_HALF_WEIGHT_MIN = 45
FREQUENT_HEADWAY_MIN = 15                    # for the ITDP "People Near Transit" replication (G-G4)

# --- validation sample (spec G4) -----------------------------------------------------------
# brtdata.org (Jakarta): 13 corridors, 251 km, average commercial speed 19 km/h; Corridor 1
# Blok M-Kota 15.48 km → ~49 min at 19 km/h. MRT official end-to-end < 30 min.
VALIDATION_OD = [
    {"name": "TJ Corridor 1 Blok M → Kota", "mode": "bus", "km": 15.48, "published_min": 49,
     "source": "brtdata.org avg commercial speed 19 km/h × corridor length"},
    {"name": "MRT Lebak Bulus → Bundaran HI", "mode": "subway", "published_min": 30,
     "source": "jakartamrt.co.id (official < 30 min)"},
    {"name": "KRL Bogor → Jakarta Kota", "mode": "train", "published_min": None,
     "source": "kci.id GAPEKA 2025 timetable lookup (read at build)"},
    {"name": "LRT Jabodebek Harjamukti → Dukuh Atas", "mode": "light_rail", "published_min": None,
     "source": "lrtjabodebek.kai.id (read at build)"},
]
GATE_TT_TOL_PCT = 15          # G-G1
GATE_TT_TOL_MIN = 8           # G-G1 (whichever tolerance is larger)
GATE_GOOGLE_MAD_MIN = 10      # G-G2 MAD vs Google Routes transit, 50 OD pairs, on-the-fly only
GOOGLE_ROUTES_FREE_CALLS = 10_000   # Essentials tier free calls / month (pricing page, 2026)
GATE_SNAP_M = 200             # G-G3
GATE_SNAP_SHARE = 0.98        # G-G3
ITDP_PNT_2016 = {"Jakarta": 0.44, "Greater Jakarta": 0.16}   # G-G4 order-of-magnitude anchor (2016)

# --- outputs -------------------------------------------------------------------------------
NETWORK_DIR = DATA_DIR / "network"
MATRIX = DATA_DIR / "matrix.parquet"
ACCESS_ADM4 = DATA_DIR / "access_adm4.parquet"
EQUITY_JSON = DATA_DIR / "equity.json"
STATS_JSON = DATA_DIR / "stats.json"
