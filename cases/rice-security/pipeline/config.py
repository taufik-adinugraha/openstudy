"""Case I constants — Rice & Food Security.  Spec: docs/spec-rice-security.html (I2-I4).

Map paddy phenology from Sentinel-1 SAR, estimate planted and harvested area, predict when the
harvest lands, and benchmark all of it against BPS's KSA figures.  Radar because optical fails:
Java's rice grows through the monsoon, under cloud, and the crop calendar is exactly what an
optical time series cannot see.

REALITY NOTES recorded during reconnaissance (2026-08-30).  Where the spec and reality disagree,
reality wins and the note stays here so the next person does not rediscover it:

  * geoBoundaries gbOpen has NO ADM3 for Indonesia (the API returns 404).  ADM1 is 34 units,
    ODbL 1.0, 2017 vintage (OSM-derived); ADM2 is 519 units, CC BY 3.0 IGO, 2020 vintage, sourced
    from BPS via OCHA.  Kecamatan geometry therefore has to come from HDX COD-AB.
  * HDX COD-AB Indonesia (verified live 2026-08-30 via the CKAN API): licence CC BY-IGO, last
    modified 2026-06-23, gdb 208.6 MiB / shp 474.6 MiB / geojson 435.1 MiB / xlsx 13.8 MiB.
    Case H found the .gdb exposes no layer whose name contains ``adm1`` under the current
    ``pyogrio`` and fell back to geoBoundaries; the SHP zip is the workaround for the levels
    geoBoundaries does not publish.
  * The official Indonesian rice-field map ("Lahan Baku Sawah", ATR/BPN Decree 686/2019) is
    catalogued on the national SDI portal, but EVERY record returns a null licence field, and the
    national/regional service host ``geoservices.bappenas.go.id`` did not resolve on 2026-08-30.
    Same shape as Case H's concession problem: unusable as a stored layer, at most a view-time
    reference overlay.  Note the SDI's HTML pages 403 while its CKAN API answers normally with a
    browser User-Agent — the API is the way in.
  * BPS: national domain ``0000`` serves all 514 regencies (no per-domain crawl); a browser
    User-Agent is required (the WAF blocks curl UAs); the "App ID" is the API key.
  * CDS: a multi-variable request comes back as TWO NetCDFs split by ``stepType``.  Naive
    concatenation leaves precipitation and solar radiation as all-NaN columns that a model then
    silently drops.  Join on the grid key and assert non-null.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = CASE_DIR / "data"
RAW = DATA_DIR / "raw"
REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_DATA = CASE_DIR / "web" / "public" / "data"      # fetched by the browser
WEB_SRC_DATA = CASE_DIR / "web" / "src" / "data"     # imported at build time (summary only)
MANIFEST = DATA_DIR / "manifest.json"
STATS_JSON = DATA_DIR / "stats.json"


def _load_env() -> None:
    """Load repo-root .env into os.environ (no override)."""
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

BPS_API_KEY = os.environ.get("BPS_API_KEY", "")
EARTHDATA_TOKEN = os.environ.get("EARTHDATA_TOKEN", "")
CDS_API_KEY = os.environ.get("CDS_API_KEY", "")
CDS_API_URL = os.environ.get("CDS_API_URL", "https://cds.climate.copernicus.eu/api")
CDSE_USER = os.environ.get("CDSE_USER", "")          # Copernicus Data Space, if that route wins
CDSE_PASSWORD = os.environ.get("CDSE_PASSWORD", "")

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ── Scope — the honest part ───────────────────────────────────────────────────────────
# A full-Indonesia Sentinel-1 VV+VH time series is terabytes per year.  It is not a demo, and
# claiming otherwise would fail quality gate 3 before a single pixel was processed.  So:
#   SCOPE_PROVINCES  the reporting scope — Java's rice bowl, roughly half of national production
#   SCOPE_DEEP       the kabupaten processed at full temporal density, chosen to show BOTH an
#                    irrigated multi-crop calendar and a rainfed single-crop one
#   national         BPS statistics only, labelled as official statistics, never as our measurement
SCOPE_PROVINCES = ("Jawa Barat", "Jawa Tengah", "Jawa Timur")
# The deep scope is chosen by WHERE THE BENCHMARK IS MONTHLY.  West and East Java publish monthly
# KSA harvested area per kabupaten across the whole KSA era; Central Java publishes annual only.
# Five of the six below therefore have a 96-month benchmark; Grobogan is the deliberate rainfed
# contrast and is validated annually, which is stated wherever it appears.
# 2025 harvested area (BPS, ha) is given because it is why these six: they are the largest.
SCOPE_DEEP = {
    "Indramayu": dict(province="Jawa Barat", ha_2025=239_498, system="irrigated"),
    "Karawang":  dict(province="Jawa Barat", ha_2025=202_293, system="irrigated",
                      note="the most urbanising of the trio — the land-conversion story"),
    "Subang":    dict(province="Jawa Barat", ha_2025=184_319, system="irrigated"),
    "Bojonegoro": dict(province="Jawa Timur", ha_2025=160_748, system="irrigated"),
    "Lamongan":  dict(province="Jawa Timur", ha_2025=152_564, system="irrigated"),
    "Grobogan":  dict(province="Jawa Tengah", system="largely rainfed",
                      note="the contrast case — and the one whose benchmark is ANNUAL ONLY"),
}
BBOX_JAVA = (105.0, -8.9, 114.7, -5.7)               # west, south, east, north
ERA5_AREA = [-5.5, 105.0, -9.0, 115.0]               # CDS order: N, W, S, E, snapped to 0.25

# ── Analysis units — three, and they are not interchangeable ──────────────────────────
CELL_M = 100                 # detection cell; multi-looking within it beats down speckle
MAP_LEVEL = "ADM3"           # kecamatan: fine enough that the harvest wave reads as a wave
REPORT_LEVEL = "ADM2"        # kabupaten: the level at which BPS publishes KSA, so the level at
                             # which a gate can actually be falsified
PROBE_STEP_M = 2000          # probe lattice for the signature interaction; see export_web.py

# ── Time window ───────────────────────────────────────────────────────────────────────
# Sentinel-1A has flown since 2014; S1B failed in Dec 2021 and S1C/S1D came up later, so revisit
# density changes mid-record (measured over Java, Jun-Aug 2026: S1D 156 scenes / S1A 52 / S1C 9).
# That is a sampling change, not a change in the crop, and it is flagged rather than corrected.
START = "2018-01-01"         # BPS series (KSA regime)
SAR_START = "2022-07-01"     # four wet seasons: 2022/23 .. 2025/26.  See the size arithmetic in
                             # the spec — this window is what fits the disk and transfer budget.
HOLDOUT_SEASON = "2025/26"   # held out entirely — gate G-I5
CAL_YEARS = (2022, 2023, 2024)
KSA_FIRST_YEAR = 2018        # BPS replaced the eye-estimate method with KSA from this reference
                             # year; the two regimes are NOT comparable and are stored separately

# ── Boundaries ────────────────────────────────────────────────────────────────────────
# GADM is REJECTED: non-commercial licence, and this is a commercial demo.
GB_ADM1_URL = ("https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/"
               "IDN/ADM1/geoBoundaries-IDN-ADM1.geojson")      # 34 units, ODbL 1.0, 2017
GB_ADM2_URL = ("https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/"
               "IDN/ADM2/geoBoundaries-IDN-ADM2.geojson")      # 519 units, CC BY 3.0 IGO, 2020
# gbOpen has no IDN ADM3 (404).  Kecamatan comes from COD-AB; the SHP zip avoids the .gdb layer
# naming problem Case H hit under pyogrio.
COD_AB_GDB_URL = ("https://data.humdata.org/dataset/84a1d98a-790b-4d66-9d14-bbfa48500802/"
                  "resource/c740a308-0a63-46d6-ab15-b041e62eff58/download/"
                  "idn_admin_boundaries.gdb.zip")              # 208.6 MiB
COD_AB_SHP_URL = ("https://data.humdata.org/dataset/84a1d98a-790b-4d66-9d14-bbfa48500802/"
                  "resource/50b9aafa-47c5-483e-a361-826320bf75d5/download/"
                  "idn_admin_boundaries.shp.zip")              # 474.6 MiB
COD_AB_LICENCE = "CC BY-IGO (BPS via OCHA COD-AB), last modified 2026-06-23"

# ── BPS WebAPI — and the assumption that did NOT survive contact ──────────────────────
#
# ** RICE IS NOT LIKE THE POVERTY VARIABLES. **  The house gotcha "national domain 0000 serves
# all 514 regencies, so there is no per-domain crawl" is TRUE for poverty and FALSE here.  On
# domain 0000 every rice table's vervar group is "38 Provinsi" — 38 provinces plus 9999
# INDONESIA, and no regencies at all.  Kabupaten-level rice exists ONLY on the provincial
# domains, with a DIFFERENT var id and a different table shape per province.  There is no uniform
# crawl; the map below is the crawl.
#
# Two undocumented API features found live and worth using:
#   * ``th`` accepts a range (``th/124:125``) or a list (``th/122;125``), capped at TWO years per
#     call — the error message says so outright.  Halves the request count.
#   * ``keyword`` works on the var / publication / pressrelease / statictable models, e.g.
#     ``list/model/var/domain/0000/keyword/padi`` — far cheaper than paging 176 pages.
BPS_BASE = "https://webapi.bps.go.id/v1/api"
BPS_DOMAIN_NATIONAL = "0000"
BPS_LIST_URL = BPS_BASE + "/list/model/{model}/lang/ind/domain/{domain}/key/{key}/"
BPS_DATA_URL = BPS_BASE + "/list/model/data/lang/ind/domain/{domain}/var/{var}/key/{key}/"
BPS_SEARCH_TERMS = ("padi", "luas panen", "produksi", "produktivitas", "gabah", "beras")
BPS_VARS_JSON = DATA_DIR / "bps_vars.json"    # resolved ids are written here, never hard-coded
BPS_MAX_YEARS_PER_CALL = 2
BPS_SLEEP_S = 0.8            # no documented rate limit; be a good citizen anyway

# National / provincial tables (domain 0000, subject 53 "Tanaman Pangan", subcsa 557).
BPS_NATIONAL_VARS = {
    1498: dict(title="Luas Panen, Produksi dan Produktivitas Padi Menurut Provinsi",
               period="annual", years=(2018, 2024), th_ids=(118, 124),
               turvar={1191: "Luas Panen (ha)", 1192: "Produktivitas (ku/ha)",
                       1193: "Produksi (ton)"},
               note="DISCONTINUED — replaced by the monthly tables from March 2025"),
    2504: dict(title="Luas Panen Padi Menurut Provinsi (Bulanan)", unit="hektar",
               period="monthly", years=(2025, 2026), th_ids=(125, 126),
               note="turvar is '0'; the MONTH lives in turtahun 1-12, and 13 = 'Tahunan'"),
    2506: dict(title="Produksi Padi Menurut Provinsi (Bulanan)", unit="ton",
               period="monthly", years=(2025, 2026)),
    2345: dict(title="Produksi Tanaman Pangan Nasional", unit="ton",
               period="monthly", years=(2018, 2026), vervar={1: "Padi", 2: "Jagung"},
               note="national only, but MONTHLY and complete across the whole KSA era"),
    295:  dict(title="Rata-rata Harga Beras di Tingkat Perdagangan Besar (Grosir)",
               unit="Rp/kg", period="monthly", years=(2017, 2026),
               note="the food-security narrative: wholesale rice rose ~9 % from Jan 2025 to "
                    "Jul 2026 DESPITE the record 2025 harvest"),
    1034: dict(title="Harga gabah tingkat petani vs HPP", period="monthly", years=(2015, 2024)),
}
# Pre-KSA Statistik Pertanian series (vars 21/22/23/179, 1993-2015).  Stored as a SEPARATE regime
# and never spliced — see the break below.
BPS_PRE_KSA_VARS = {21: "Luas Panen", 22: "Produktivitas", 23: "Produksi",
                    179: "Luas Lahan Sawah"}
BPS_PRE_KSA_TURVAR_PADI = 16

# ** THE FIND THAT MAKES GATE G-I2 POSSIBLE. **  Monthly KSA harvested area at KABUPATEN level
# exists for West and East Java across the whole KSA era: 65 regencies x 96 months (2018-2025) =
# 6,240 benchmark observations.  Central Java publishes annual only.  Verified live, and the
# kabupaten rows sum EXACTLY to the provincial row, which in turn matches the national table.
BPS_PROVINCE_TABLES = {
    "Jawa Barat":  dict(domain="3200", area_var=935, prod_var=937, beras_var=938,
                        period="monthly", years=(2018, 2025), units=27),
    "Jawa Timur":  dict(domain="3500", area_var=578, prod_var=579, beras_var=580,
                        period="monthly", years=(2018, 2025), units=38),
    "Jawa Tengah": dict(domain="3300", area_var=463, alt_var=465,
                        period="annual", years=(2018, 2025), units=35),
    "Banten":      dict(domain="3600", area_var=593, period="annual", years=(2021, 2025)),
    "DI Yogyakarta": dict(domain="3400", area_var=589, period="monthly", years=(2023, 2024),
                          note="months live in turvar here, not turtahun"),
}
# The KSA phase classification — vegetatif awal/akhir, generatif, persiapan lahan, puso — is the
# closest thing to a ground-truth label set for a satellite phenology model.  It is published for
# DKI JAKARTA ONLY (domain 3100, vars 1006/1017/1018/1019/1020/1022, 2018-2021), and DKI has
# almost no rice.  Worth a direct data request to BPS; not available through the API elsewhere.
BPS_KSA_PHASE_VARS = {1006: "Fase Vegetatif Awal", 1017: "Fase Vegetatif Akhir",
                      1018: "Fase Generatif", 1019: "Persiapan Lahan",
                      1020: "Gagal Panen/Puso", 1022: "Ditanami Tanaman Lain"}
BPS_KSA_PHASE_DOMAIN = "3100"

# Publications and press releases (both models work, both keyword-searchable).
BPS_PUBLICATION_KEYWORD = "Luas Panen dan Produksi Padi"
BPS_BRS_KEYWORD = "padi"
BPS_BRS_CADENCE = ("monthly, landing on the 1st-3rd carrying the reference month ~2 months back "
                   "PLUS three months ahead as 'angka potensi' — a forward-looking official "
                   "figure derived from KSA standing crop, and a direct comparator for our own "
                   "forecast")

# Known data-quality defects, all verified — the pipeline asserts against each.
BPS_KNOWN_DEFECTS = {
    "kota_batu_2025": "domain 3500 var 578: the Tahunan cell reads 51,279.0 ha while the twelve "
                      "monthly cells sum to 512.8 ha — an exact 100x decimal slip.  The only one "
                      "found in ~600 regency-years scanned.  Provincial totals unaffected.",
    "stale_tahunan": "var 2345 for 2026 has a Tahunan cell equal to the Jan-Apr sum, never "
                     "refreshed.  ALWAYS recompute annual from the monthly cells.",
    "blank_not_missing": "~40 regency-months absent in East Java 2020-21, all tiny urban kota and "
                         "Madura in dry months — near-certainly true zeros, not gaps.",
    "bali_codes": "Bali numbers its regencies 1-10 sequentially rather than by BPS code; needs a "
                  "name crosswalk.  Java uses real 4-digit BPS codes.",
    "papua_domains": "the four new Papua provinces (9200/9500/9600/9700) return 404 as domains — "
                     "they appear as vervar in national tables but have no provincial domain.",
}

# THE METHODOLOGY BREAK, quantified from BPS's own numbers.  2016 and 2017 are a MORATORIUM with
# no data at all, so the break is a two-year hole with a 2.74 Mha (-19.4 %) step-down across it.
BPS_BREAK = dict(last_sp_year=2015, last_sp_ha=14_116_638,
                 moratorium=(2016, 2017),
                 first_ksa_year=2018, first_ksa_ha=11_377_934,
                 ksa_series_mha={2018: 11.38, 2019: 10.68, 2020: 10.66, 2021: 10.41,
                                 2022: 10.45, 2023: 10.21, 2024: 10.05, 2025: 11.32})
# The 2025 jump — +1,274,851 ha (+12.7 %) in one year, with Java supplying about half of it
# (Jawa Barat +279,938 / +19.0 %, Jawa Timur +224,361 / +13.9 %, Jawa Tengah +120,217 / +7.7 %) —
# is the single most stress-testable official claim in the series, and chapter 03 tests it.
BPS_2025_JUMP_HA = 1_274_851

# LICENCE — the nuance that needs sign-off.  BPS's DATA terms permit commercial use explicitly
# ("using the data for both commercial and non-commercial purposes") subject to citation and no
# implied endorsement.  The API DEVELOPER terms are narrower: "BPS berkomitmen pada akses gratis
# dan terbuka ke API kami untuk tujuan non-komersial", and forbid selling/sublicensing API access.
# Practical reading: the numbers are fine commercially with citation; what is forbidden is
# reselling the pipe.  Ingest to our own store, cite BPS, never expose the API.
BPS_LICENCE_DATA = "commercial use permitted with citation (bps.go.id/en/term-of-use)"
BPS_LICENCE_API = "developer terms say non-commercial; do not resell or proxy the API"

# ── Sentinel-1 — routes, all verified live 2026-08-30 ─────────────────────────────────
# THE SIZE ARITHMETIC, because it is what decides the design:
#   Java needs 28 IW GRD scenes per 12-day cycle (ASF count = 28 and CDSE OData $count = 28 for
#   the same window — two independent catalogues agreeing exactly), across 12 relative orbits.
#   A GRD scene is 836-847 MB (measured: VV 443.8 MB + VH 356.4 MB).  Two years of Java GRD is
#   1,672 scenes = ~1.4 TB.  The SAME two years as analysis-ready OPERA RTC bursts is ~290 GB —
#   5x smaller AND already terrain-corrected.  That ratio is the whole argument for the route
#   below, and the reason "just download the GRD" is not a plan.
#
#   "rtc"    PRIMARY.  ASF OPERA L2 RTC-S1 v1.0: 30 m gamma0 on the Copernicus GLO-30 DEM, one
#            granule per S1 burst, separate _VV.tif / _VH.tif COGs, 6-14 MB each.  Free and open
#            (CMR UseConstraints: FreeAndOpenData true, EOSDIS Data Use Policy) and the existing
#            EARTHDATA_TOKEN is the credential.  Terrain correction, calibration and speckle
#            geometry all leave our scope.  Measured revisit at real Java rice locations over two
#            years: Central Java 124 acquisition dates (tracks T127+T076, ~6-day effective),
#            West Java 56/yr, East Java 44/yr.
#   "hub"    10 m CLOSE-UP for ONE focus regency.  CDSE Sentinel Hub S1GRD with
#            backCoeff=GAMMA0_TERRAIN + orthorectify=TRUE (Copernicus DEM) returns RTC gamma0
#            server-side, no egress.  Free tier is 10,000 processing units/month; all-Java at
#            10 m is ~3,300 PU per date (3 dates/month — useless), but one regency the size of
#            Indramayu is ~52 PU per date, so a full two-year 12-day series is ~3,200 PU — inside
#            one month's free quota.  Costs one free CDSE registration.
#   "grd"    FALLBACK with no account at all: s3://sentinel-s1-l1c is genuinely anonymous
#            (LIST 200, ranged GET 206, "No AWS account required") and the measurement TIFFs are
#            true COGs, so an AOI can be window-read without touching the 1.4 TB.  Price: we do
#            our own radiometric terrain correction.
S1_ROUTE = "rtc"
S1_POLARISATIONS = ("VV", "VH")
S1_MODE = "IW"
S1_ORBIT_SEPARATE = True     # NOT optional: backscatter depends on incidence angle, so mixing
                             # relative orbits produces a sawtooth that mimics phenology
SAR_CHUNK_GB = 2.0           # disk headroom demanded before the next burst

# OPERA RTC via ASF / NASA CMR.  Anonymous download is 401 -> 302 to urs.earthdata.nasa.gov;
# the EDL bearer token is the credential.  GOTCHA: on LP DAAC an INVALID token produces the same
# 302-to-login as no token, and a bare HEAD can return 303 with a working presigned URL even
# unauthenticated — so a broken token silently writes an HTML login page into a .tif.  Every
# download asserts HTTP 200 AND the GeoTIFF magic bytes.
OPERA_RTC_SHORTNAME = "OPERA_L2_RTC-S1_V1"
OPERA_RTC_STATIC_SHORTNAME = "OPERA_L2_RTC-S1-STATIC_V1"   # local incidence, layover/shadow
OPERA_RTC_DOI = "10.5067/SNWG/OPERA_L2_RTC-S1_V1"
CMR_SEARCH_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
ASF_SEARCH_URL = "https://api.daac.asf.alaska.edu/services/search/param"
# ASF HyP3 stays as the escape hatch if OPERA coverage has a hole: free, 8,000 credits/month,
# RTC_GAMMA costs 5 credits at 30 m / 15 at 20 m / 60 at 10 m (cost table pulled live).
HYP3_API = "https://hyp3-api.asf.alaska.edu"

# CDSE (route "hub"): catalogue is anonymous, DOWNLOAD is not.
CDSE_STAC = "https://catalogue.dataspace.copernicus.eu/stac"
CDSE_TOKEN_URL = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
                  "protocol/openid-connect/token")
CDSE_QUOTA = ("free tier: 12 TB / 30 days, 4 concurrent connections, 2000 req/min, "
              "Sentinel Hub 10,000 PU/month, openEO 10,000 credits/month, tokens live 10 min")

# Anonymous fallback (route "grd").  NOTE the unpadded month/day prefixes — /8/12/, not /08/12/ —
# a zero-padded key silently returns nothing.
S1_AWS_BUCKET = "sentinel-s1-l1c"
S1_AWS_PREFIX = "GRD/{y}/{m}/{d}/IW/DV/{scene}/measurement/iw-{pol}.tiff"
DEM_AWS_BUCKET = "copernicus-dem-30m"       # anonymous, 13.7 MB per 1-degree tile, ~44 for Java

S1_LICENCE = ("Copernicus / EC Legal Notice: free access with rights of reproduction, "
              "distribution, communication to the public, adaptation, modification and "
              "combination — no non-commercial clause.  Attribution: "
              '"Contains modified Copernicus Sentinel data [year]".')

# ── Backscatter processing ────────────────────────────────────────────────────────────
STEP_DAYS = 6                # resampling grid; finer than the 12-day repeat because overlapping
                             # orbits do better than 12 days over much of Java
MAX_GAP_DAYS = 24            # an event whose defining dates fall in a longer gap is NOT dated
SG_WINDOW = 7                # Savitzky-Golay: preserves peak position, which a moving average
SG_ORDER = 2                 # destroys — and peak position is what this case measures

# ── Phenology detection thresholds ────────────────────────────────────────────────────
FLOOD_DB = -17.0             # VV below this = specular reflection off sheet water
RISE_DB = 4.0                # required VH rise after the minimum: separates rice from ponds
RISE_WINDOW_DAYS = 45
HEAD_TO_HARVEST_DAYS = 30
MIN_CYCLE_DAYS = 85          # a full transplant-to-harvest cycle cannot be shorter than this

# ── Rice mask (a PRIOR, not a label — see ingest_aux.py) ──────────────────────────────
MASK_BUFFER_M = 300
# Both primaries are CC BY 4.0 on Zenodo and both were licence-checked at the DEPOSIT, not from
# the paper: Open-SEA-Rice-10's article renders CC BY-NC-ND while its Zenodo record is CC BY 4.0,
# so the deposit is what is cited for rights.
RICE_MASK = {
    "open_sea_rice_10": dict(
        doi="10.5281/zenodo.14627003", licence="CC BY 4.0", year=2021, res_m=10,
        file="GEE_SEA_Rice_Ci10_20250110.zip", size_mb=521.8,
        note="classes 1/2/3 = single/double/triple crop — so it is BOTH the extent prior AND "
             "the independent cropping-intensity benchmark for gate G-I3.  Reported Indonesia "
             "accuracy OA 98.8 %, F1 0.851, R2 0.85 against national statistics, with a "
             "consistent ~6,460 km2 UNDERESTIMATE that is carried as a stated bias."),
    "nesea_rice_10": dict(
        doi="10.5281/zenodo.5645344", licence="CC BY 4.0", year=2019, res_m=10,
        files=("2017_2019Y_5_10S_105_110E.zip", "2017_2019Y_5_10S_110_115E.zip"),
        size_mb=34.8, note="two tiles cover Java; tiny, and the cheapest possible cross-check"),
}
RICE_MASK_PRIMARY = "open_sea_rice_10"
# Ground-truth polygons for the confusion matrix (NOT for training the detector):
WORLDCEREAL_RDM = ("https://ewoc-rdm-api.iiasa.ac.at/collections/"
                   "2023_idn_vitocampaign_poly_110")   # 336 polys W+C Java 2023, 89 rice
WORLDCEREAL_RDM_LICENCE_UNVERIFIED = True   # the RDM permits CC BY-NC per collection and this
                                            # one exposes no licence field — confirm before use
REJECTED_MASKS = {
    "IRRI Asia lowland rice extent": "CC BY-NC-SA 4.0 — non-commercial, excluded",
    "RIICE / sarmap CRISP": "site 503/500; CRISP is a commercial service — excluded",
    "Zhao/Zhang 30 m S+SE Asia rice": "raster stops at 5.63 N, never crosses the equator — "
                                      "does not cover Java",
    "ESA WorldCereal v100 irrigation": "measured over Semarang/Demak/Grobogan in the wet paddy "
                                       "season: tc-maize-main irrigation 0.09 %, wintercereals "
                                       "0.19 %.  WorldCereal has NO rice class and its irrigation "
                                       "layer received no quantitative validation (~35 % below "
                                       "statistics).  Not a paddy layer; do not pitch it as one.",
    "Lahan Baku Sawah (ATR/BPN 686/2019)": "the BIG service is live and anonymous with 1,242,551 "
                                           "sawah polygons, but NO licence is stated anywhere on "
                                           "it -> validation-only overlay, never stored or "
                                           "redistributed (the Case H concession precedent)",
}
# Official Indonesian rice-field map — reference overlay only, nothing stored (see above).
BIG_LBS_MAPSERVER = ("https://kspservices.big.go.id/satupeta/rest/services/PUBLIK/"
                     "SUMBER_DAYA_ALAM_DAN_LINGKUNGAN/MapServer/36")

# ── Optical cross-check (the "why radar" chapter is measured, not asserted) ───────────
# Element84 Earth Search is fully anonymous, no registration.  NOTE the Collection-1 bucket is
# e84-earth-search-sentinel-data, NOT sentinel-cogs (both live; the older collection still
# receives 2026 Java data).  62 MGRS tiles intersect the Java bbox.
S2_STAC = "https://earth-search.aws.element84.com/v1"
S2_COLLECTION = "sentinel-2-c1-l2a"
S2_CLOUD_MAX = 30            # the usable-scene count per month IS chapter 01's evidence

# ── MODIS phenology — cross-check ONLY, and with an expiry date ──────────────────────
# LP DAAC retired MODIS from e4ftl01.cr.usgs.gov on 2025-06-30 (it now 404s); the Earthdata Cloud
# pool is the live route.  Java is exactly two tiles and their seam runs through Central Java
# near Semarang (~110.8 E), so mosaicking is unavoidable unless AppEEARS does the subsetting.
MODIS_TILES = ("h28v09", "h29v09")           # v10 is wrong: v09 covers 0-10 S, Java ends at 8.8 S
MODIS_POOL = "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/{product}/{ur}/{ur}.hdf"
APPEEARS_API = "https://appeears.earthdatacloud.nasa.gov/api"
MODIS_LICENCE = "CC0 (NASA-led mission data); attribute anyway, imply no NASA endorsement"
# MCD12Q2 is stuck at 2024 AND caps at two cycles per year — structurally unable to represent
# Java's triple-cropped irrigated sawah.  Cross-check only; we derive our own transition dates.
# Terra/Aqua begin shutting down late 2026/early 2027 and are already drifting from their design
# overpass times, which injects an illumination bias that MIMICS a phenological trend.
MCD12Q2_MAX_CYCLES = 2
VIIRS_SUCCESSOR = ("VNP13A1", "VNP22Q2")     # 500 m but an 8-day step; the durable series

# ── Climate ───────────────────────────────────────────────────────────────────────────
ONSET_RULE = dict(accum_mm=40, over_days=10, no_dry_spell_days=10, dry_spell_mm=5)
ERA5_SL = "reanalysis-era5-single-levels"
ERA5_VARS = ["2m_temperature", "total_precipitation",
             "surface_solar_radiation_downwards", "volumetric_soil_water_layer_1"]
MIN_NONNULL = 0.95           # assert on every accumulated column (the split-NetCDF bug)

# CHIRPS.  ** v2 PRODUCTION ENDS AFTER DECEMBER 2026 ** (CHC's own README, verbatim) — pinning v2
# pins a product that stops updating mid-engagement, so v3 is the path and v2 is the fallback.
CHIRPS_V3_BASE = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/"
CHIRPS_V2_BASE = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/"
# THE TRICK THAT MAKES THIS CHEAP.  There is no Asia subset and no server-side subsetting at all
# (THREDDS/OPeNDAP are dead — plain nginx serving static files).  BUT the cogs/ tree is properly
# tiled 512x512, every response carries accept-ranges, and JAVA'S PIXEL WINDOW FALLS WHOLLY
# INSIDE ONE TILE (#41 of 60; global grid 7200x2000 at 0.05 deg, Java is x 5700-5920, y
# 1100-1180).  Verified end to end with a real Range request returning HTTP 206.  That is
# ~125 KiB/day against 3 MiB for the whole global file — a 64x saving.
CHIRPS_COG_TILE = 41
CHIRPS_TILE_GRID = (512, 512)
CHIRPS_GLOBAL_SHAPE = (2000, 7200)          # rows, cols at p05
# The .tif.gz products are gzip streams and are NOT randomly accessible: taking that route means
# downloading 9.6 GiB of global daily files to extract ~210 MiB of Java.  Use the COGs.
CHIRPS_ARCHIVE_SPINE = "global_pentad/cogs/"   # complete, current, ~220 MiB for 2018-2026
CHIRPS_OPERATIONAL = "prelim/global_daily/fixed/tifs/p05/"   # 5-day latency; 'fixed' sums exactly
                                                             # to the pentad totals, 'tifs' does not
CHIRPS_FINAL_LAG_DAYS = 30       # final products post in one batch around the 13th-15th
CHIRPS_PRELIM_LAG_DAYS = 5
CHIRPS_COG_GAPS = ("2026-04", "2026-05")     # cogs/p05/2026 is missing April and May entirely
                                             # (verified 404 while the .tif.gz returns 200)
# CHC waives copyright but does so informally: one named individual, no CC deed link, no version
# string, and a UC Regents "all rights reserved" footer on the same page.  Describe it honestly.
CHIRPS_LICENCE = ("a CC0-style public-domain dedication — unversioned and unlinked "
                  '("Pete Peterson has waived all copyright ... CHIRPS data is in the public '
                  'domain as registered with Creative Commons")')
# CHC README: a %CCD bug set precipitation to ZERO where IR data was missing, and "This was always
# a problem for Eastern Australia/Indonesia/Japan, where a gap between two geostationary
# satellites exists."  Reprocessed in 2015 so our window is unaffected — but Java sits in a known
# inter-satellite IR gap, so zero-rainfall runs are sanity-checked before any threshold is built
# on them.  Also: CHIRPS "daily" is a disaggregated pentad, not an independent daily observation.
CHIRPS_IDN_CAVEAT = True

# ── Context sources — used, but never as the benchmark ───────────────────────────────
# FPMA (FAO price module) is live, anonymous and genuinely useful: the Indonesian retail rice
# series is monthly 2008-02 -> 2026-06 with nominal, CPI-deflated and USD values per point, and
# its own source is BPS.  International benchmarks (Thai 5% broken etc.) support an import-parity
# panel.  Treat the endpoint as unofficial — it was reverse-engineered from the site bundle.
FPMA_BASE = "https://fpma.fao.org/giews/v4/global/price_module/api/v1/"
FPMA_IDN_RICE_UUID = "e8830e09-cde2-46ae-8fd7-916a1982b0e1"
FAOSTAT_BULK_ASIA = ("https://bulks-faostat.fao.org/production/"
                     "Production_Crops_Livestock_E_Asia.zip")        # 4.49 MiB, Indonesia inside
FAOSTAT_AREA_CODE = 101
# FAOSTAT's REST API is now gated (HTTP 401, developer portal since April 2026) — use the bulks.
# LICENCE FLAG: FAO dropped CC BY-NC-SA 3.0 IGO for CC BY 4.0 but bolted on "Datasets shall not
# be used for or in conjunction with the promotion of a commercial enterprise and/or its
# product(s) or services".  A consulting demo is exactly the edge case that clause was written
# for.  FAO data is therefore CONTEXT ONLY here, and the call is escalated rather than assumed.
FAO_LICENCE_FLAG = True
REJECTED_STATS = {
    "ASEAN AFSIS": "aptfsis.org is live but the only rights statement anywhere is a bare "
                   "'Copyright 2017 ... Rights Reserved' footer — no terms page, no reuse grant. "
                   "That is worse than a restrictive licence: there is no affirmative permission. "
                   "It is also a derived, revision-lagged copy of BPS (2023 shows 53,733.52 kt "
                   "where BPS now says 53,980,993 t).  Go to BPS directly.",
    "Kementan BDSP": "bdsp2.pertanian.go.id works and has an undocumented JSON query layer, but "
                     "its numbers ARE BPS's to the rounding (2025: 11,320,986.21 ha vs BPS "
                     "11,320,986.23).  Not an independent source — a cross-check at best.",
    "FAO GIEWS country brief": "PDF/HTML only, no JSON or CSV anywhere, and the Indonesia brief "
                               "was seven months stale on 2026-08-30.  Context, not data.",
    "FAO ASIS": "flat licence contradiction — CC BY 4.0 on the GIEWS access page versus "
                "license_id 'CC-BY-NC-SA-4.0' in FAO's own CKAN catalogue for the same datasets. "
                "WMTS tiles are anonymous but the GeoTIFF pixels 403.  Not usable until FAO "
                "confirms in writing.",
    "Bapanas panelharga": "site under maintenance on 2026-08-30; the API key embedded in its own "
                          "frontend returns 401.  No licence stated.",
    "PIHPS / hargapangan.id": "hargapangan.id returns HTTP 522; the BI-hosted grid endpoint 302s "
                              "without a session and its Excel export is generated CLIENT-SIDE, "
                              "so there is no server endpoint to script.",
    "KATAM Terpadu-SC": "product confirmed to exist (5-day standing-crop monitoring) but every "
                        "litbang/BSIP/BRMP host failed DNS resolution after the reorganisation. "
                        "Machine-readable endpoint unverified.",
}

# ── Model ─────────────────────────────────────────────────────────────────────────────
LEAD_WEEKS = 6               # lead over the official monthly release — the actual product

# ── Gates (thresholds fixed BEFORE any result was seen) ───────────────────────────────
GATES = ("G-I1", "G-I2", "G-I3", "G-I4", "G-I5")
GATE_PROV_PCT = 10.0         # provincial annual harvested area vs BPS KSA
GATE_KAB_R2 = 0.75
GATE_KAB_MAPE = 20.0
GATE_TIMING_WEEKS = 2.0      # median absolute error of peak-harvest week
GATE_TIMING_BIAS_WEEKS = 3.0
GATE_CI_IRRIGATED = (1.5, 2.5)
GATE_CI_RAINFED = 1.5
GATE_MASK_AGREE = 0.70

# ── Export ────────────────────────────────────────────────────────────────────────────
WEB_BUDGET_MB = 3.0

# ── Resource guards (shared 16 GB box, several other heavy jobs) ──────────────────────
MIN_FREE_DISK_GB = 10.0
DATA_BUDGET_GB = 8.0


def free_gb(path: Path | str = "/") -> float:
    return shutil.disk_usage(str(path)).free / 2**30


def disk_ok(need_gb: float = 0.5) -> bool:
    return free_gb() - need_gb >= MIN_FREE_DISK_GB
