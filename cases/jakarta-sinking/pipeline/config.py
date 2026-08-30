"""Case C constants — Jakarta Is Sinking. Spec: docs/spec-jakarta-sinking.html (C2–C4).
Decisions: D17 deposited field first + own LiCSBAS gate; D18 subsidence + flood exposure;
D19 GLO-30 elevation now, DEMNAS upgrade later."""

import os
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = CASE_DIR / "data"
RAW = DATA_DIR / "raw"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_env() -> None:
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

# Jakarta working bbox (west, south, east, north) — Jabodetabek core
BBOX = (106.55, -6.55, 107.15, -5.95)

# --- deposited velocity field (D17): Ohenhen et al. 2026, CC BY 4.0 ---
ZENODO_VLM_URL = "https://zenodo.org/records/15786357/files/Java_VLM_EW.csv?download=1"
# The CSV is ~sorted by longitude; the Jakarta slice sits roughly in this byte window.
# We over-fetch and filter by bbox, so the window only needs to be generous.
ZENODO_VLM_RANGE = (26_000_000, 76_000_000)

# --- GNSS ground truth: Susilo et al. 2023, CC BY 4.0 ---
ZENODO_GNSS_API = "https://zenodo.org/api/records/7775016"
GNSS_JAKARTA = {"CJKT": -6.4, "CTGR": -2.9, "CBTU": -0.5}   # published vertical mm/yr

# --- elevation (D19): Copernicus GLO-30, public S3 ---
GLO30_TILES = [
    "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_S07_00_E106_00_DEM/Copernicus_DSM_COG_10_S07_00_E106_00_DEM.tif",
    "https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_S07_00_E107_00_DEM/Copernicus_DSM_COG_10_S07_00_E107_00_DEM.tif",
]

# --- population ---
WORLDPOP_URL = ("https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/2020/"
                "BSGM/IDN/idn_ppp_2020_constrained.tif")
GHSL_URL = ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/"
            "GHS_POP_E2020_GLOBE_R2023A_54009_100/V1-0/tiles/"
            "GHS_POP_E2020_GLOBE_R2023A_54009_100_V1_0_R10_C29.zip")
# built-up surface (m² per 100 m cell), same tile — the "built-up area" exposure variable
GHSL_BUILT_URL = ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/"
                  "GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100/V1-0/tiles/"
                  "GHS_BUILT_S_E2020_GLOBE_R2023A_54009_100_V1_0_R10_C29.zip")
OFFICIAL_DKI_POP_2020 = 10_562_088   # BPS Sensus Penduduk 2020, DKI Jakarta

# --- admin units + observed floods (Jakarta Satu ArcGIS REST; no explicit licence — attribute) ---
JAKSATU = "https://jakartasatu.jakarta.go.id/server/rest/services"
KELURAHAN_LAYER = f"{JAKSATU}/Batas_Administrasi_Update/Batas_Administrasi_DKI_Jakarta_Update_View/FeatureServer/3"
FLOOD_HISTORY_LAYER = f"{JAKSATU}/BPBD/Histori_Banjir_BPBD_Time_Aware/FeatureServer/0"
UNOSAT_2020_URL = "https://unosat-maps.web.cern.ch/unosat-maps/ID/FL20200101IDN/FL20200101IDN_SHP.zip"
EOS_2020_URL = ("https://sentinel-asia.org/EO/2020/article20200101ID/"
                "EOS_ARIA-SG_20200102_FPM_Indonesia_Floods_v1.5_SHP.zip")

# --- own InSAR run (gate G-C3): LiCSAR frame, clipped ---
LICSAR_FRAME_ASC = "098A_09673_121312"
LICSAR_FRAME_DESC = "047D_09652_111009"
LICSBAS_CLIP = "106.6/107.1/-6.45/-6.0"
LICSBAS_WINDOW = ("20170101", "20241231")

# --- gates (spec C4) ---
GATE_HOTSPOT_RANGE = (2.0, 6.0)      # cm/yr, NW-coast hotspots, deposited field
GATE_GNSS_TOL_MM = 5.0               # mm/yr
GATE_INSAR_CORR = 0.70
GATE_INSAR_HOTSPOT_CM = 1.0
GATE_FLOOD_SPEARMAN = 0.5            # G-C5 (spec's original plausibility check, renumbered)
# G-C4 exposure sanity: WorldPop total within ±15 % of the census, GHSL within ±25 % of WorldPop,
# 267 kelurahan, ≥ 90 % of mainland kelurahan with ≥ 30 % InSAR coverage
GATE_POP_TOL = 0.15
GATE_GHSL_TOL = 0.25
GATE_KELURAHAN_N = 267
GATE_COVERAGE_MIN = 0.30
GATE_COVERAGE_SHARE = 0.90

BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
