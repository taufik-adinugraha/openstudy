"""Case H constants — Forest & Commodity Watch. Spec: docs/spec-forest-watch.html (H2-H4).

Reframed "where GFW stops": alerts -> clusters -> linkage to planted-palm extent, mill
catchments, peat and primary forest.  Every stored linkage layer is CC BY 4.0 with no
country carve-out (verified live against the GFW Data API on 2026-08-30, see LICENCES).

Reality corrections applied on 2026-08-30 (spec said otherwise; reality wins):

  * HEADER CASE MATTERS.  The GFW API gateway only accepts the api-key header spelled
    exactly ``x-api-key``.  ``urllib.request`` title-cases custom headers to
    ``X-api-key`` and every authenticated call then fails with a *401-shaped* 403
    "Request is missing valid API key".  Use ``requests`` (which preserves the case you
    give it) or ``http.client``.  Dataset METADATA endpoints are public, so a key that
    is silently broken still "works" on /dataset/{id} -- do not use that as a key test;
    use :func:`gfw_key_ok`.
  * ``gfw_radd_alerts`` does not exist -> ``wur_radd_alerts``.
  * The Hansen GCS bucket is still anonymous, but ``umd_tree_cover_loss`` v1.13 publishes
    a ``year__tcd30_2000`` tile set that is *already* masked to >= 30 % canopy: 296 MB for
    Indonesia against ~5-6 GB for lossyear + treecover2000 + datamask.  We use it.
  * ``gfw_universal_mill_list_buffered_50_km`` exists but its raster tile set returns 403
    with a free key -> mill catchments are computed from the UML point table with a
    KD-tree, which also yields the *distance* and the *identity* of the nearest mill.
  * Oil-palm extent: ``gfw_oil_palm`` and ``rspo_oil_palm`` are unusable in Indonesia (see
    LICENCES) -> ``gfw_planted_forests`` (Spatial Database of Planted Trees v2, plain
    CC BY 4.0, no carve-out) supplies the palm class as a 10 m raster on the same grid.
"""

from __future__ import annotations

import os
import shutil
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
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# --- GFW Data API ---------------------------------------------------------------------
GFW_API = "https://data-api.globalforestwatch.org"
GFW_API_KEY = os.environ.get("GFW_API_KEY", "")
GFW_HEADERS = {"x-api-key": GFW_API_KEY}          # lowercase; see the module docstring
GFW_ATTRIB = 'Source: "RADD alerts". WUR, accessed through Global Nature Watch on {date}'

BBOX_IDN = (94.5, -11.5, 141.5, 6.5)
FOCUS_PROVINCES = ("Riau", "Kalimantan Tengah", "Papua")

# 10-degree GFW/Hansen tile grid. Every raster below lives on it, so a window in one layer
# maps to a window in every other by a pure integer scale (100000 px = 10 m, 40000 px = 30 m,
# ratio exactly 2.5).
TILES_IDN = ("00N_100E", "00N_110E", "00N_120E", "00N_130E", "00N_140E",
             "10N_090E", "10N_100E", "10N_110E", "10N_120E", "10N_130E", "10N_140E",
             "10S_110E", "10S_120E")
TILE_DEG = 10

# --- raster tile sets, all pulled through /dataset/{ds}/{ver}/download/geotiff ----------
# size_mb = HEAD-verified Indonesian total on 2026-08-30.
RASTERS = {
    "radd": dict(dataset="wur_radd_alerts", version="v20260823", grid="10/100000",
                 pixel_meaning="date_conf", size_mb=696, dtype="uint16",
                 licence="CC BY 4.0",
                 cite='Reiche et al. 2021. Source: "RADD alerts". WUR, accessed through '
                      "Global Nature Watch"),
    "glad": dict(dataset="umd_glad_landsat_alerts", version="v20260829", grid="10/40000",
                 pixel_meaning="date_conf", size_mb=107, dtype="uint16",
                 licence="CC BY 4.0",
                 cite='Hansen et al. 2016. Source: "GLAD-L alerts". GLAD/UMD, accessed '
                      "through Global Nature Watch"),
    "tcl30": dict(dataset="umd_tree_cover_loss", version="v1.13", grid="10/40000",
                  pixel_meaning="year__tcd30_2000", size_mb=296, dtype="uint8",
                  licence="CC BY 4.0",
                  cite="Hansen et al. 2013, High-Resolution Global Maps of 21st-Century "
                       "Forest Cover Change; accessed through Global Nature Watch"),
    # NOTE: the `simpleType` band only separates "Planted forest" from "Tree crops" (verified
    # empirically: the Indonesian tiles carry values 0/1/2 only).  `simpleName` is the band that
    # names the crop, so it is the one that can say "oil palm".  It is published at 30 m.
    "palm": dict(dataset="gfw_planted_forests", version="v20231128", grid="10/40000",
                 pixel_meaning="simpleName", size_mb=260, dtype="uint8",
                 licence="CC BY 4.0",
                 cite="Richter et al. 2024, Spatial Database of Planted Trees (SDPT) v2, "
                      "WRI; accessed through Global Nature Watch"),
    "peat": dict(dataset="gfw_peatlands", version="v20230315", grid="10/40000",
                 pixel_meaning="is", size_mb=42, dtype="uint8", licence="CC BY 4.0",
                 cite="Global peatland extent (Miettinen et al. 2016 for Indonesia); "
                      "accessed through Global Nature Watch"),
    "primary": dict(dataset="umd_regional_primary_forest_2001", version="v201901",
                    grid="10/40000", pixel_meaning="is", size_mb=117, dtype="uint8",
                    licence="CC BY 4.0",
                    cite="Turubanova et al. 2018 / Margono et al. 2014 primary forest 2001; "
                         "accessed through Global Nature Watch"),
}
# Order matters: the alert layer first so a truncated run still has the headline input.
INGEST_ORDER = ("radd", "tcl30", "palm", "peat", "primary", "glad")

GFW_DOWNLOAD_URL = (GFW_API + "/dataset/{dataset}/{version}/download/geotiff"
                    "?grid={grid}&tile_id={tile}&pixel_meaning={pixel_meaning}")
GFW_QUERY_URL = GFW_API + "/dataset/{dataset}/{version}/query/json"
GFW_DATASET_URL = GFW_API + "/dataset/{dataset}"

# Universal Mill List — point table via the query endpoint (the buffered raster 403s).
MILLS = dict(dataset="gfw_universal_mill_list", version="v202508", licence="CC BY 4.0",
             cite="WRI, Rainforest Alliance, Proforest, Daemeter, Trase, Earthworm, Auriga, "
                  "CIFOR, Transitions, J. Benedict, R. Heilmayr, K. Carlson, "
                  '"Universal Mill List"; accessed through Global Nature Watch')
MILL_FIELDS = ("uml_id", "group_name", "parent_com", "mill_name", "rspo_statu", "rspo_type",
               "latitude", "longitude", "country", "province", "district", "confidence")

# --- boundaries: HDX COD-AB (BPS, CC BY-IGO) -------------------------------------------
COD_AB_GDB_URL = ("https://data.humdata.org/dataset/84a1d98a-790b-4d66-9d14-bbfa48500802/"
                  "resource/c740a308-0a63-46d6-ab15-b041e62eff58/download/"
                  "idn_admin_boundaries.gdb.zip")                                # 219 MB
GB_ADM1_URL = ("https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/"
               "IDN/ADM1/geoBoundaries-IDN-ADM1.geojson")                        # fallback

# --- alert decoding --------------------------------------------------------------------
# date_conf = confidence digit * 10000 + days since 2014-12-31.  2 = low, 3 = high
# (4 = several systems agree, integrated layer only).  0 = no alert.
ALERT_EPOCH = "2014-12-31"
ALERT_CONF_HIGH = 3
MIN_CLUSTER_HA = 0.5
MIN_CLUSTER_PX = 50                # 0.5 ha at ~10 m
GLAD_AGREEMENT_DAYS = 60
RADD_START = "2020-01-01"          # RADD south-east Asia coverage starts here
BLOCK_PX = 10000                   # 10 m block edge -> 100 blocks per 10-degree tile
MILL_RADIUS_KM = 50                # FFB catchment radius (Trase convention)
PALM_ADJACENT_KM = 1.0
# SDPT simpleType raster: the palm classes.  Calibrated on the server against the vector
# table (pipeline/ingest.py --calibrate-palm writes data/raw/palm_classes.json).
PALM_CLASS_FILE = RAW / "palm_classes.json"
PALM_CLASSES_DEFAULT: tuple[int, ...] = ()   # empty until calibrated — never guess a class

# --- published totals for the gates ----------------------------------------------------
GFW_IDN_TCL_HA = {2023: 1_395_285, 2024: 1_120_264}
GFW_IDN_PRIMARY_LOSS_HA = {2023: 292_374, 2024: 258_812}
KLHK_DEFORESTATION_HA = {2023: {"gross": 133_800},
                         2024: {"gross": 216_200, "net": 175_400}}
AURIGA_2024_HA = {"Indonesia": 261_575, "Kalimantan Tengah": 33_389, "Riau": 20_812}

GATE_LOSS_TOL_PCT = 5
GATE_ALERT_TOL_PCT = 10
GATE_GLAD_AGREEMENT = 0.60
GATE_LINK_MIN_SHARE = 0.25

# --- licence ledger (verified live 2026-08-30 against /dataset/{id} metadata) -----------
# Recorded verbatim from the API so the methodology footer can quote it.
LICENCES = {
    "wur_radd_alerts": "CC BY 4.0",
    "umd_glad_landsat_alerts": "CC BY 4.0",
    "umd_tree_cover_loss": "CC BY 4.0",
    "gfw_planted_forests": "CC BY 4.0",
    "gfw_peatlands": "CC BY 4.0",
    "umd_regional_primary_forest_2001": "CC BY 4.0",
    "gfw_universal_mill_list": "CC BY 4.0",
    "hdx_cod_ab_idn": "CC BY-IGO (BPS via OCHA COD-AB)",
}
REJECTED = {
    "gfw_oil_palm": "API licence field: 'CC BY 4.0 (excluding Indonesia)' -> "
                    "not usable as a stored Indonesian layer; reference overlay only",
    "rspo_oil_palm": "API licence field: RSPO 'Disclaimer for Map Publication' "
                     "(not an open licence) -> excluded",
    "idn_wood_fiber": "API licence field: 'View Only, Not Downloadable.' -> excluded",
    "gfw_wood_fiber": "'CC BY 4.0 (excluding Indonesia)' -> excluded",
    "gfw_mining_concessions": "'CC BY 4.0 (excluding Indonesia)' -> excluded",
    "gfw_plantations / gfw_logging / gfw_pre_2000_plantations": "no licence field -> excluded",
    "Nusantara Atlas / TheTreeMap": "terms: non-commercial -> excluded",
    "Planetary Computer Sentinel-1 RTC": "account required -> out of scope",
}
CONCESSION_OVERLAYS = {
    "kemenhut_pbph": ("https://geoportal.planologi.kehutanan.go.id/server/rest/services/"
                      "Peta_Interaktif_2026/PBPH_AR_50K/MapServer"),
    "sigap_wms": "https://sigap.kehutanan.go.id/sigap-forge-geoserver-2026/sigap/wms",
}

# --- resource guards -------------------------------------------------------------------
MIN_FREE_GB = 10.0                 # hard floor; four other jobs share the 16 GB / 48 GB box
DATA_BUDGET_GB = 8.0


def free_gb(path: Path | str = "/") -> float:
    return shutil.disk_usage(str(path)).free / 2**30


def disk_ok(need_gb: float = 0.5) -> bool:
    """True when the run may continue.  Every loop calls this before the next chunk."""
    return free_gb() - need_gb >= MIN_FREE_GB


def gfw_key_ok() -> bool:
    """Exercise an AUTHENTICATED endpoint — /dataset/{id} is public and proves nothing."""
    import requests
    if not GFW_API_KEY:
        return False
    r = requests.post(GFW_QUERY_URL.format(**MILLS), headers=GFW_HEADERS,
                      json={"sql": "SELECT count(*) n FROM data"}, timeout=90)
    return r.status_code == 200


# --- outputs ---------------------------------------------------------------------------
ALERTS_DIR = DATA_DIR / "alerts"
CLUSTERS = DATA_DIR / "clusters.parquet"
LINKED = DATA_DIR / "linked.parquet"
MILLS_SCORED = DATA_DIR / "mills_scored.parquet"
LOSS_TABLE = DATA_DIR / "loss_province_year.parquet"
BOUNDARIES = DATA_DIR / "adm1.parquet"
MILLS_PARQUET = DATA_DIR / "mills.parquet"
MANIFEST = DATA_DIR / "manifest.json"
STATS_JSON = DATA_DIR / "stats.json"
CHIPS_DIR = CASE_DIR / "web" / "public" / "chips"
