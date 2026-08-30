"""Case F constants — Poverty Mapping from Space. Spec: docs/spec-poverty-map.html (F2–F4).

Data path verified by reconnaissance 2026-08-30 (sizes are HEAD/listing results, not guesses).
Ground truth is BPS's official regency poverty rate — Susenas/DHS microdata are restricted,
so the model is trained at kabupaten/kota level and carried down to kecamatan by small-area
estimation, benchmarked so the official regency number is never contradicted.
Licence rule: every layer is CC BY / CC BY-IGO / CDLA-Permissive / ODbL-attribution; the
CC BY-NC layers found during scouting (Meta RWI, SMERU poverty map) are NOT used (README).
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

# Scope: SCOPE=java trains/serves Java only (fast path, ~11 GB); default is all Indonesia (~20 GB).
SCOPE = os.environ.get("SCOPE", "idn")
BBOX_IDN = (94.5, -11.5, 141.5, 6.5)       # west, south, east, north
BBOX_JAVA = (105.0, -9.0, 116.0, -5.0)
BBOX = BBOX_JAVA if SCOPE == "java" else BBOX_IDN
JAVA_PROVINCE_CODES = {"31", "32", "33", "34", "35", "36"}   # BPS 2-digit prefixes

# --- ground truth: BPS WebAPI (key in .env; the WAF needs a browser UA) -------------------
# Verified 2026-08-30 with the lab key: var 621 = "Persentase Penduduk Miskin (P0) Menurut
# Kabupaten/Kota", years 2016-2025 (th_id 116..125), 552 vertical rows (province headers are
# wrapped in <b>…</b> and must be dropped; 514 regencies; 548 data points for 2016).
# The data endpoint REQUIRES the th parameter. Kab/kota poverty = March Susenas, annual.
BPS_API = "https://webapi.bps.go.id/v1/api"
BPS_DOMAIN = "0000"
BPS_VARS = {621: "p0_pct", 622: "p1_gap", 623: "p2_severity", 624: "poverty_line_idr"}
BPS_YEARS = {y: y - 1900 for y in range(2016, 2026)}   # th_id: 2016 → 116 … 2025 → 125 (verified list)
BPS_DATA_URL = BPS_API + "/list/model/data/domain/{domain}/var/{var}/th/{th}"
BPS_YEAR_LIST_URL = BPS_API + "/list/model/th/domain/{domain}/var/{var}"

# --- boundaries -----------------------------------------------------------------------------
# HDX COD-AB Indonesia (source BPS, CC BY-IGO; boundaries 2020-04-08): ADM2 522, ADM3 7,069,
# ADM4 81,912, with P-codes that join directly to BPS codes (ID + BPS code). Used for BOTH
# levels so features and estimates share one topology. geoBoundaries gbOpen has no ADM3/ADM4
# for IDN (API checked); gbHumanitarian ADM3/ADM4 (CC BY 3.0 IGO, 2019) is the same lineage.
# Flagship A's geoBoundaries ADM2 (519, 2020) is reconciled by name for cross-case links.
COD_AB_GDB_URL = ("https://data.humdata.org/dataset/84a1d98a-790b-4d66-9d14-bbfa48500802/resource/"
                  "c740a308-0a63-46d6-ab15-b041e62eff58/download/idn_admin_boundaries.gdb.zip")   # 219 MB
COD_AB_XLSX_URL = ("https://data.humdata.org/dataset/84a1d98a-790b-4d66-9d14-bbfa48500802/resource/"
                   "ba60b4d3-fd55-4fcf-b1bd-adf7e4bdba90/download/idn_admin_boundaries.xlsx")     # 14 MB, P-code lookup
GB_HUMANITARIAN_ADM3_URL = ("https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/"
                            "gbHumanitarian/IDN/ADM3/geoBoundaries-IDN-ADM3.geojson")             # 189 MB, alternate
FLAGSHIP_ADM2_URL = ("https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/"
                     "IDN/ADM2/geoBoundaries-IDN-ADM2.geojson")                                    # 159 MB, CC BY 3.0 IGO
BOUNDARIES_ADM2 = DATA_DIR / "boundaries" / "adm2.parquet"
BOUNDARIES_ADM3 = DATA_DIR / "boundaries" / "adm3.parquet"
CROSSWALK = REPO_ROOT / "cases" / "nightlights-pulse" / "data" / "crosswalk" / "pemekaran_crosswalk.csv"

# --- buildings ------------------------------------------------------------------------------
# Google Open Buildings v3 (licence: CC BY 4.0 OR ODbL, user's choice → we take CC BY 4.0).
# S2 level-4 cell CSVs; cells computed with s2sphere over BBOX_IDN and joined to the public GCS
# listing 2026-08-30: 36 files / 15.8 GB gz for Indonesia (cells spill into MY/PH/PG),
# Java = 3 cells / 8.4 GB (2e7 alone 5.9 GB). The level-6 partition
# (v3/polygons_s2_level_6_gzip_no_header/) splits 2e7 into 16 files — use it on the 16 GB box.
OPEN_BUILDINGS_URL = ("https://storage.googleapis.com/open-buildings-data/v3/"
                      "polygons_s2_level_4_gzip/{token}_buildings.csv.gz")
OPEN_BUILDINGS_L6_PREFIX = "https://storage.googleapis.com/open-buildings-data/v3/polygons_s2_level_6_gzip_no_header/"
OPEN_BUILDINGS_CELLS_JAVA = ("2dd", "2e5", "2e7")
OPEN_BUILDINGS_CELLS_IDN = (
    "2c5", "2cd", "2cf", "2d1", "2d3", "2d5", "2d7", "2d9", "2db", "2dd", "2df", "2e1", "2e3",
    "2e5", "2e7", "2fd", "303", "305", "307", "319", "31b", "31d", "31f", "321", "323", "325",
    "327", "329", "32b", "32f", "681", "683", "685", "687", "69b", "69d",
)
OPEN_BUILDINGS_CELLS = OPEN_BUILDINGS_CELLS_JAVA if SCOPE == "java" else OPEN_BUILDINGS_CELLS_IDN
OB_MIN_CONFIDENCE = 0.70
OB_VINTAGE = "v3 (files dated 2023-06-23)"
# Microsoft GlobalML Building Footprints — CDLA-Permissive-2.0; Indonesia = 601 quadkey files,
# 4.74 GB gz (release 2026-08-13). Second footprint source for the building-count cross-check.
MS_FOOTPRINTS_LINKS = "https://bfppub.blob.core.windows.net/$web/2026-08-13/dataset-links.csv"
GRID_M = 100

# --- lights: reuse Flagship A's Black Marble annual composites (never re-download) ---------
BLACK_MARBLE_ANNUAL_DIR = REPO_ROOT / "cases" / "nightlights-pulse" / "data" / "raw" / "bm"
BLACK_MARBLE_ANNUAL_PATTERN = "{year}-01/*A4_radiance.tif"    # VNP46A4 (2012→) / VJ146A4 (2018→)

# --- population (annual, matches the BPS panel) --------------------------------------------
# WorldPop R2025A constrained 100 m, 2015-2030, CC BY 4.0 — verified 2020 (168 MB) and 2025 (169 MB).
WORLDPOP_URL = ("https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/{year}/IDN/v1/100m/"
                "constrained/idn_pop_{year}_CN_100m_R2025A_v1.tif")
WORLDPOP_2020_LEGACY_URL = ("https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/2020/"
                            "BSGM/IDN/idn_ppp_2020_constrained.tif")   # 103 MB, the file Case C/G use

# --- land cover / built-up / roads ----------------------------------------------------------
# ESA WorldCover 2021 v200 (CC BY 4.0): 81 tiles in 93-141E × 12S-6N = 1.2 GB; Java 8 tiles 234 MB.
# Bucket holds only v100/2020 and v200/2021 — no later year exists.
WORLDCOVER_S3 = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
WORLDCOVER_TILE = "ESA_WorldCover_10m_2021_v200_{lat}{lon}_Map.tif"     # 3°x3°, 8-60 MB each
WORLDCOVER_ATTRIB = ("© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) "
                     "processed by ESA WorldCover consortium")
# GHSL R2023A (CC BY 4.0), Mollweide tiles R9-R11 × C28-C33 cover Indonesia (18 tiles).
GHSL_FTP = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
GHSL_PRODUCTS = {
    "built_s":  "GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100/V1-0/tiles/GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100_V1_0_{tile}.zip",
    "nres":     "GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_NRES_E2020_GLOBE_R2023A_54009_100/V1-0/tiles/GHS_BUILT_S_NRES_E2020_GLOBE_R2023A_54009_100_V1_0_{tile}.zip",
    "smod":     "GHS_SMOD_GLOBE_R2023A/GHS_SMOD_E2020_GLOBE_R2023A_54009_1000/V2-0/tiles/GHS_SMOD_E2020_GLOBE_R2023A_54009_1000_V2_0_{tile}.zip",
}
GHSL_TILES_IDN = tuple(f"R{r}_C{c}" for r in (9, 10, 11) for c in range(28, 34))
OSM_PBF_URL = "https://download.geofabrik.de/asia/indonesia-latest.osm.pbf"   # 1.73 GB, daily, ODbL
OSM_JAVA_PBF_URL = "https://download.geofabrik.de/asia/indonesia/java-latest.osm.pbf"  # 896 MB
STAC_URL = "https://earth-search.aws.element84.com/v1"                   # sentinel-2-c1-l2a, anonymous
STAC_COLLECTION = "sentinel-2-c1-l2a"
S2_OVERVIEW_M = 80                                                        # read COG overviews only
S2_ATTRIB = "Contains modified Copernicus Sentinel data {year}"

# --- rejected / deferred layers (documented so nobody re-scouts them) ----------------------
REJECTED = {
    "Meta Relative Wealth Index (HDX)": "CC BY-NC 4.0 — non-commercial; not used (pending user verification)",
    "SMERU poverty map 2015": "CC BY-NC 4.0 — cited as literature only",
    "BPS Podes 2024 microdata": "Silastik: registration + PNBP fee — not open",
    "Major TOM embeddings": "CC BY-SA 4.0 share-alike — deferred",
    "Tessera embeddings": "CC0 but ~200 GB for Java — v2 candidate, not on a 16 GB box",
    "Open Buildings 2.5D Temporal": "CC BY 4.0 but ~0.5 TB/yr for Indonesia — v2 candidate",
}

# --- model ---------------------------------------------------------------------------------
TARGET = "p0_pct"
CV_SCHEME = "leave-one-province-out"       # 34 folds; spatial-block 200 km as sensitivity
TEMPORAL_HOLDOUT_YEARS = (2024, 2025)      # G-F3: train ≤ 2023, predict the last two releases
LGBM_PARAMS = {"n_estimators": 600, "learning_rate": 0.03, "num_leaves": 15,
               "min_child_samples": 10, "subsample": 0.8, "colsample_bytree": 0.8, "seed": 7}

# --- gates (spec F4) — thresholds set from the Indonesian literature ------------------------
# Putri et al. 2022 (East Java, 2020): Pearson 0.71 / Spearman 0.77 / RMSE 3.2 pp vs BPS;
# Sartirano et al. 2023: Spearman 0.75 across 513 regencies; Chi et al. 2022: spatial-CV R² 0.56.
GATE_R2_MIN = 0.50            # G-F1 leave-one-province-out R², latest year
GATE_SPEARMAN_MIN = 0.70      # G-F1 rank correlation vs BPS
GATE_RMSE_MAX_PP = 4.0        # G-F1 out-of-sample RMSE in percentage points
GATE_R2_OFFJAVA_MIN = 0.35    # G-F2 below this, off-Java estimates are labelled "indicative"
GATE_TEMPORAL_SPEARMAN = 0.65 # G-F3 rank correlation on the 2024-2025 temporal hold-out
GATE_BENCHMARK_TOL_PP = 0.1   # G-F4 regency totals reproduced after benchmarking

# --- outputs -------------------------------------------------------------------------------
FEATURES_ADM2 = DATA_DIR / "features_adm2.parquet"
FEATURES_ADM3 = DATA_DIR / "features_adm3.parquet"
MODEL_DIR = DATA_DIR / "model"
CV_PREDICTIONS = DATA_DIR / "cv_predictions.parquet"
ESTIMATES_ADM3 = DATA_DIR / "estimates_adm3.parquet"
STATS_JSON = DATA_DIR / "stats.json"
