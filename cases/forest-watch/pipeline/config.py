"""Case H constants — Forest & Commodity Watch. Spec: docs/spec-forest-watch.html (H2–H4).

Reframed "where GFW stops": alerts → clusters → linkage to palm extent, mills, peat and
primary forest (all CC BY 4.0). Data path verified by reconnaissance 2026-08-30. The GFW data
lake on S3 is requester-pays (anonymous HEAD → 403); tiles come through the GFW Data API with
a FREE API key (sign-up → token → apikey; key expires after one year).
Concession boundaries are the licence problem of this case: GFW's Indonesian concession
vectors are "view-only" / CC BY "excluding Indonesia", and the ministry's ArcGIS/WMS services
carry no licence text → used only as a live reference overlay, never stored or redistributed
(pending user verification; README).
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

BBOX_IDN = (94.5, -11.5, 141.5, 6.5)
# Alert layers run nationally (RADD Indonesia ≈ 1 GB); the deep-dive chapters use three
# provinces. COD-AB ADM1 is the 2020 BPS vintage (34 provinces) → "Papua" is the pre-2022
# province; stated on the page.
FOCUS_PROVINCES = ("Riau", "Kalimantan Tengah", "Papua")

# --- boundaries: HDX COD-AB (BPS, CC BY-IGO) — ADM1 34 / ADM2 522; P-codes join to BPS ------
# (geoBoundaries gbOpen ADM1 is OSM-derived ODbL share-alike, so COD-AB is preferred here.)
COD_AB_GDB_URL = ("https://data.humdata.org/dataset/84a1d98a-790b-4d66-9d14-bbfa48500802/resource/"
                  "c740a308-0a63-46d6-ab15-b041e62eff58/download/idn_admin_boundaries.gdb.zip")   # 219 MB
GB_ADM2_URL = ("https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/"
               "IDN/ADM2/geoBoundaries-IDN-ADM2.geojson")                                          # 159 MB, CC BY 3.0 IGO (Flagship A vintage)

# --- Hansen Global Forest Change v1.12 (loss 2001-2024), CC BY 4.0 "even commercially" -------
# Anonymous HTTPS. Verified 2026-08-30: 14 tiles intersect Indonesian land; per tile lossyear
# ≈ 26-83 MB, treecover2000 ≈ 280-320 MB, datamask ≈ 22-24 MB, gain ≈ 29 MB → ~5-6 GB for all
# layers nationally (lossyear alone ≈ 0.9 GB). v1.13 (loss through 2025) exists in the GFW API;
# its GCS path was not yet visible at scouting — pin when it appears.
HANSEN_VERSION = "GFC-2024-v1.12"
HANSEN_BASE = f"https://storage.googleapis.com/earthenginepartners-hansen/{HANSEN_VERSION}/"
HANSEN_FILE = "Hansen_" + HANSEN_VERSION + "_{layer}_{tile}.tif"
HANSEN_LAYERS = ("lossyear", "treecover2000", "datamask")
HANSEN_TILES_IDN = ("10N_090E", "10N_100E", "10N_110E", "10N_120E", "10N_130E", "10N_140E",
                    "00N_090E", "00N_100E", "00N_110E", "00N_120E", "00N_130E", "00N_140E",
                    "10S_110E", "10S_120E")
CANOPY_THRESHOLD = 30          # % tree cover 2000 — GFW's default forest definition
LOSSYEAR_OFFSET = 2000         # lossyear 1..24 → 2001..2024

# --- alerts via the GFW Data API ----------------------------------------------------------
# Key flow: POST /auth/sign-up → POST /auth/token → POST /auth/apikey {alias, email, organization}
# → x-api-key header; download returns 307 → presigned S3 URL (HTTP range requests work).
GFW_API = "https://data-api.globalforestwatch.org"
GFW_API_KEY = os.environ.get("GFW_API_KEY", "")          # NEW .env key — add to .env.example (root, not touched here)
GFW_HEADERS = {"x-api-key": GFW_API_KEY}
GFW_ATTRIB = "Source: 'RADD alerts'. WUR, accessed through Global Nature Watch on {date}"
GFW_DATASETS = {
    # sizes = verified per-tile GeoTIFF sizes for Indonesian tiles (date_conf)
    "radd": {"dataset": "wur_radd_alerts", "grid": "10/100000", "pixel_meaning": "date_conf",
             "licence": "CC BY 4.0", "cadence": "weekly", "start": "2020-01-01",
             "tile_mb": (53, 100), "version_at_scouting": "v20260823"},
    "glad_l": {"dataset": "umd_glad_landsat_alerts", "grid": "10/40000", "pixel_meaning": "date_conf",
               "licence": "CC BY 4.0", "cadence": "daily", "start": "2015-01-01",
               "tile_mb": (40, 60), "version_at_scouting": "v20260829"},
    "integrated": {"dataset": "gfw_integrated_alerts", "grid": "10/100000", "pixel_meaning": "date_conf",
                   "licence": "CC BY 4.0", "cadence": "daily", "start": "2015-01-01",
                   "tile_mb": (80, 90), "version_at_scouting": "v20260830"},
    "tcl": {"dataset": "umd_tree_cover_loss", "licence": "CC BY 4.0", "version_at_scouting": "v1.13"},   # query only (G-H1)
    "tcl_country": {"dataset": "gadm__tcl__iso_change", "version_at_scouting": "v20260424"},          # precomputed totals
}
GFW_DOWNLOAD_URL = GFW_API + "/dataset/{dataset}/{version}/download/geotiff?grid={grid}&tile_id={tile}&pixel_meaning={pixel_meaning}"
GFW_QUERY_URL = GFW_API + "/dataset/{dataset}/{version}/query/json"          # POST {"sql": ..., "geometry": ...}
GFW_GEOSTORE_ADMIN = GFW_API + "/geostore/admin/IDN"                          # → f98f505878dcee72a2e92e7510a07d6f
GFW_VERSIONS_URL = GFW_API + "/dataset/{dataset}"
# Alert tiles: national pull is affordable (12 land tiles ≈ 0.7-1.2 GB for RADD); the focus
# map keeps the tiles per province for the windowed reads.
ALERT_TILES_IDN = ("10N_090E", "10N_100E", "10N_110E", "10N_120E", "10N_130E", "10N_140E",
                   "00N_090E", "00N_100E", "00N_110E", "00N_120E", "00N_130E", "00N_140E", "10S_120E")
ALERT_TILES_FOCUS = {
    "Riau": ("00N_100E", "10N_100E"),
    "Kalimantan Tengah": ("00N_110E", "10N_110E"),
    "Papua": ("00N_130E", "00N_140E", "10N_130E", "10N_140E"),
}
# date_conf encoding (GFW): leading digit = confidence (2 low, 3 high; 4 = multiple systems in
# the integrated layer), followed by days since 2014-12-31; 0 = no alert.
ALERT_EPOCH = "2014-12-31"
ALERT_CONF_HIGH = 3
MIN_CLUSTER_HA = 0.5
CLUSTER_WINDOW_WEEKS = 4
GLAD_AGREEMENT_DAYS = 60

# --- published totals for the gates (GFW API gadm__tcl__iso_change v20260424; KLHK) --------
GFW_IDN_TCL_HA = {2023: 1_395_285, 2024: 1_120_264}            # tree cover loss ≥ 30 % canopy
GFW_IDN_PRIMARY_LOSS_HA = {2023: 292_374, 2024: 258_812}       # humid primary forest loss (−11 %)
KLHK_DEFORESTATION_HA = {2023: {"gross": 133_800}, 2024: {"gross": 216_200, "net": 175_400}}   # kehutanan.go.id
AURIGA_2024_HA = {"Indonesia": 261_575, "Kalimantan Tengah": 33_389, "Riau": 20_812}          # simontini.id

# --- commodity layers (all CC BY 4.0) -------------------------------------------------------
PALM_EXTENT = {  # Descals et al. 2024 v1.2 — 2021 extent 10 m (industrial / smallholder) + planting year 1990-2021 (30 m)
    "zenodo": "https://zenodo.org/records/13379129",
    "extent_mb": 156.5, "yop_mb": 146.5, "grid_shp_kb": 82, "licence": "CC BY 4.0",
    "fallback_2019": "https://zenodo.org/records/4473715",     # Descals et al. 2021, 101 MB, CC BY 4.0
}
MILLS_UML = {  # Universal Mill List via GFW Data API — v202508, refreshed every 6 months, CC BY 4.0
    "dataset": "gfw_universal_mill_list", "version": "latest",
    "download": GFW_API + "/dataset/gfw_universal_mill_list/latest/download/csv",   # key required
    "fields": ("uml_id", "group_name", "parent_com", "mill_name", "rspo_statu", "rspo_type",
               "latitude", "longitude", "country", "province", "district", "confidence"),
    "idn_count": 1231, "idn_rspo_certified": 239, "licence": "CC BY 4.0",
}
PEAT = {"dataset": "gfw_peatlands", "licence": "CC BY 4.0"}                    # global composite incl. Miettinen 2016
PRIMARY_FOREST_2000 = {"url": "https://gfw2-data.s3.amazonaws.com/forest_cover/zip/idn_primary.tif.aux.zip",
                       "mb": 60.1, "licence": "CC BY 4.0", "source": "Margono et al. 2014"}
TRASE_SPATIAL = {"url": "https://trase.earth/open-data/datasets/spatial-metrics-indonesia-palm-oil-oil-palm-ha",
                 "licence": "CC BY 4.0", "note": "planted area 2004-2024 by province/kabupaten (aggregates)"}
TRASE_SUPPLY_CHAIN = {"doi": "10.48650/X83N-7M36", "years": "2013-2022", "licence": "CC BY 4.0"}

# Reference-only overlays (no licence text; NOT stored, NOT redistributed — live WMS/REST at view time)
CONCESSION_OVERLAYS = {
    "kemenhut_pbph": ("https://geoportal.planologi.kehutanan.go.id/server/rest/services/"
                      "Peta_Interaktif_2026/PBPH_AR_50K/MapServer"),           # 541 features, queryable, © Kemenhut
    "sigap_wms": "https://sigap.kehutanan.go.id/sigap-forge-geoserver-2026/sigap/wms",
    "gfw_hub_oil_palm_2010": "https://gis-gfw.wri.org/arcgis/rest/services/commodities/asia/MapServer/2",  # MoF 2010 via GFW, licence unstated
}
REJECTED = {
    "Nusantara Atlas / TheTreeMap": "terms: non-commercial → excluded",
    "RSPO GeoRSPO": "no open licence; Indonesia excluded from the shapefile → excluded",
    "GFW concession vectors (IDN)": "CC BY 4.0 'excluding Indonesia (view-only)' → overlay only",
    "Sentinel-1 RTC": "no anonymous global source (Planetary Computer needs an account) → out of scope",
}
MILL_RADIUS_KM = 50            # FFB catchment radius (Trase convention)
PALM_ADJACENT_KM = 1.0

# --- Sentinel-2 chips (Earth Search STAC, anonymous COGs on sentinel-cogs.s3.us-west-2) -----
STAC_URL = "https://earth-search.aws.element84.com/v1"
STAC_COLLECTION = "sentinel-2-l2a"
S2_ATTRIB = "Contains modified Copernicus Sentinel data {year}"
N_CHIPS = 20                   # per focus province
CHIP_WINDOW_DAYS = 90
CHIP_MAX_CLOUD = 40
CHIP_SIZE_M = 2000

# --- gates (spec H4) -----------------------------------------------------------------------
GATE_LOSS_TOL_PCT = 5          # G-H1 our Hansen ha vs GFW API query, per province × year 2015-2024
GATE_ALERT_TOL_PCT = 10        # G-H2 our RADD count/ha vs GFW API aggregation, last 12 months
GATE_GLAD_AGREEMENT = 0.60     # G-H3 share of high-conf RADD clusters ≥ 5 ha confirmed by GLAD-L
GATE_LINK_MIN_SHARE = 0.25     # G-H4 sanity: palm+mill-linked share of alert ha in Riau (literature: ~⅓ direct to palm)

# --- outputs -------------------------------------------------------------------------------
ALERTS_DIR = DATA_DIR / "alerts"
LINKED = DATA_DIR / "linked.parquet"
CHIPS_DIR = CASE_DIR / "web" / "public" / "chips"
STATS_JSON = DATA_DIR / "stats.json"
