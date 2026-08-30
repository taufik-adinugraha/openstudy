"""Case J constants — Fire & Haze Early Warning.  Spec: docs/spec-fire-haze.html (J2-J4).

Predict where fire starts and where the smoke goes.  Ignition risk per 0.25 deg cell per day at
1-7 day lead, plus a kinematic trajectory model on ERA5 winds that says which receptors — Sumatra,
Kalimantan, and across the Strait, Singapore — are downwind.  2015 and 2019 are the anchor crises
and are held out of training entirely.

RECONNAISSANCE 2026-08-30.  Six things the spec's first draft assumed turned out to be wrong.
They are recorded here because each one changes the build:

  1. FIRMS ``area`` windows cap at FIVE days, not ten ("Expects [1..5]").
  2. VIIRS DOES cover 2015 — ``VIIRS_SNPP_SP`` starts 2012-01-20 — so both anchors use VIIRS and
     no MODIS splice is needed for them.  Measured on the Sumatra box for 2015-10-20: VIIRS 6,110
     detections against MODIS 1,354, a factor of 4.5.
  3. ** THE ``type`` FIELD IS ABSENT FROM EVERY NRT PRODUCT. **  NASA: "data distributed via the
     FIRMS download tool does not attribute the static sources/inferred hotspot 'type'".  A
     ``type == 0`` filter therefore SILENTLY NO-OPS on the near-real-time tail, so the live end of
     the series keeps the volcanoes and gas flares that the historical part drops — a step change
     at the SP/NRT seam that looks like a trend.  (Case E's ``ingest_firms.py`` has this bug.)
     The fix is a STATIC volcano/flare exclusion mask applied to every product, with ``type``
     used only to BUILD that mask from the SP archive.
  4. ``cems-fire-historical-v1`` is not on CDS (404).  It is on EWDS, a third Copernicus store.
  5. ADS and EWDS need NO new registration: the existing ECMWF PAT authenticates on all three
     stores.  A 403 reading "user didn't accept all required site policies" is a ONE-TIME BROWSER
     CLICK, not a credential problem.  Anonymous requests return 401, which is how we know the
     token is doing its job.
  6. The ground-truth problem is worse and differently shaped than assumed: OpenAQ has ZERO PM2.5
     locations in Riau and ZERO in all of Kalimantan.  The fire belt is unmonitored.  See
     RECEPTORS below for what replaces the assumed station list.

Other gotchas that cost time if rediscovered:
  * GFW API: the header must be exactly lowercase ``x-api-key``; ``urllib`` title-cases it and
    every authenticated call 403s.  ``/auth/apikey/{key}/validate`` returns 401 even for a good
    key — test with ``GET /datasets``.
  * CDS: a multi-variable single-levels request returns a ZIP of TWO NetCDFs split by
    ``stepType``.  Naive concatenation leaves precipitation and radiation as all-NaN columns that
    a model silently drops.  Join on the grid key and assert non-null.
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

FIRMS_MAP_KEY = os.environ.get("FIRMS_MAP_KEY", "")
GFW_API_KEY = os.environ.get("GFW_API_KEY", "")
OPENAQ_API_KEY = os.environ.get("OPENAQ_API_KEY", "")
EARTHDATA_TOKEN = os.environ.get("EARTHDATA_TOKEN", "")
# ONE ECMWF personal access token serves CDS, ADS and EWDS.  Only the per-store policy click
# differs.  CDS_API_KEY is reused verbatim against the other two hosts.
CDS_API_KEY = os.environ.get("CDS_API_KEY", "")
CDS_API_URL = os.environ.get("CDS_API_URL", "https://cds.climate.copernicus.eu/api")
ADS_API_URL = "https://ads.atmosphere.copernicus.eu/api"
EWDS_API_URL = "https://ewds.climate.copernicus.eu/api"
POLICY_CLICKS = (
    "ADS: 'Data protection and privacy statement (rev. 1)' + 'Terms of use of the Copernicus "
    "Atmosphere Data Store (rev. 1)'",
    "EWDS: 'Terms of use of the CEMS Early Warning Data Store (rev. 11)'",
    "Earthdata GES DISC EULA (only if the S5P GES DISC route is ever used)",
)

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ── Geography ─────────────────────────────────────────────────────────────────────────
BBOX_IDN = (94.5, -11.5, 141.5, 6.5)                 # west, south, east, north

# The fire/haze domain: Sumatra, Kalimantan, the Peninsula and Singapore.  Java and the east are
# outside it — they burn far less and are not the transboundary story — which keeps ERA5 and
# CHIRPS to roughly a third of a national box.
AOI = (95.0, -9.0, 119.0, 6.0)
ERA5_AREA = [6.0, 95.0, -9.0, 119.0]                 # CDS order: N, W, S, E, on the 0.25 grid
GRID_DEG = 0.25                                       # ERA5 native; the model grid, honestly

FOCUS_PROVINCES = ("Riau", "South Sumatra", "Jambi", "Central Kalimantan",
                   "West Kalimantan", "South Kalimantan")

# ── Receptors — rebuilt around what is actually MEASURED ──────────────────────────────
# The assumed station list did not survive contact with the API.  OpenAQ has ZERO PM2.5 locations
# in Riau and ZERO in all of Kalimantan: the fire belt is unmonitored in every open source we
# could find.  So the design is three-tier and says so on the page:
#
#   tier 1  Singapore NEA — the only long, clean, commercially-licensed ground record in the
#           region.  Hourly, five regions, 2016-03 -> present.  This carries the hard gate.
#   tier 2  the Indonesian OpenAQ units that DO exist, with their short coverage stated.
#   tier 3  CAMS EAC4 reanalysis PM2.5 (2003 -> 2025) as a SURROGATE at the unmonitored
#           receptors — explicitly a model, never called an observation, and reported in its own
#           row.  It is also the only reference that reaches the 2015 anchor, because the NEA API
#           returns nothing before 2016-03.
RECEPTORS = {
    "singapore": dict(lat=1.352, lon=103.820, country="SG", source="nea",
                      coverage="2016-03 -> present, hourly, 5 regions", tier=1),
    "palembang": dict(lat=-2.976, lon=104.775, country="ID", source="openaq",
                      openaq_ids=(6103913, 6103954), provider="AirGradient",
                      coverage="2025-10-22 -> live", tier=2),
    "west_sumatra": dict(lat=-0.608, lon=100.755, country="ID", source="openaq",
                         openaq_ids=(1894630,), provider="Clarity",
                         coverage="2023-11-28 -> live", tier=2,
                         note="labelled 'Jakarta' in OpenAQ but its coordinates are in West "
                              "Sumatra — a genuine Sumatra station hiding under a wrong name"),
    "medan": dict(lat=3.560, lon=98.659, country="ID", source="openaq",
                  openaq_ids=(5586536,), provider="USU", tier=2),
    "pekanbaru": dict(lat=0.507, lon=101.448, country="ID", source="cams_eac4", tier=3,
                      note="NO open ground sensor has ever existed here — surrogate only"),
    "palangkaraya": dict(lat=-2.209, lon=113.917, country="ID", source="cams_eac4", tier=3,
                         note="the worst-affected city in 2015 and 2019, and unmonitored"),
    "pontianak": dict(lat=-0.026, lon=109.343, country="ID", source="cams_eac4", tier=3),
}
RECEPTOR_KM = 75.0            # a parcel this close to a receptor counts as arrival
OPENAQ_BASE = "https://api.openaq.org/v3"
OPENAQ_IDN_COUNTRY_ID = 1     # NOT 41 — checked live
NEA_V1_URL = "https://api.data.gov.sg/v1/environment/pm25?date={date}"   # takes a date; history
NEA_V2_URL = "https://api-open.data.gov.sg/v2/real-time/api/pm25"        # real-time only
NEA_LICENCE = ("Singapore Open Data Licence v1.0 — commercial use explicitly permitted "
               '("whether commercially or non-commercially"); attribution + no-endorsement')
NEA_HISTORY_STARTS = "2016-03"   # 2015-10-20 returns zero items; 2013 returns HTTP 500
REJECTED_GROUND = {
    "Malaysia APIMS": "apims.doe.gov.my returns 404 on every path incl. root; api.data.gov.my "
                      "carries no air-quality dataset.  The only aggregator with APIMS is AQICN, "
                      'which forbids commercial use verbatim ("can not be used in paid '
                      'applications or services") -> REJECTED.  Malaysia is a genuine hole.',
    "Indonesia ISPU (KLHK)": "menlhk.go.id is NXDOMAIN after the 2024 ministry split; the live "
                             "host is ispu.kemenlh.go.id (72 stations) but it is server-rendered "
                             "with no API and no stated licence -> not used.",
    "OpenAQ Riau / Kalimantan": "found: 0 on bbox AND 25 km radius searches around Pekanbaru, "
                                "Palangkaraya and Banjarbaru.  There is nothing to fetch.",
}

# ── Time window ───────────────────────────────────────────────────────────────────────
START = "2012-01-20"                # VIIRS SNPP standard-processing archive begins here
MODIS_START = "2001-01-01"          # base-rate context only, own series, own charts
ANCHOR_YEARS = (2015, 2019)         # held out of training entirely — gate G-J5
FIRE_SEASON_MONTHS = (6, 7, 8, 9, 10, 11)

# ── FIRMS ─────────────────────────────────────────────────────────────────────────────
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api"
FIRMS_AREA_URL = FIRMS_BASE + "/area/csv/{key}/{src}/{w},{s},{e},{n}/{days}/{start}"
FIRMS_AVAIL_URL = FIRMS_BASE + "/data_availability/csv/{key}/ALL"
# CORRECTION 2026-08-30 (build): the quota endpoint is NOT under /api.  ``/api/mapserver/...``
# returns 400 "Invalid API call"; the live path is the bare host.  Verified returning
# {"transaction_limit": 5000, "current_transactions": 30, "transaction_interval": "10 minutes"}.
FIRMS_QUOTA_URL = "https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY={key}"
FIRMS_MAX_DAYS = 5                  # the API's own cap: "Expects [1..5]"
# The bulk country files are the right route for history: the directory listing 404s under the
# new PinPoint backend, but the files themselves are live at guessable URLs.  VIIRS SNPP
# 2012-2024 for Indonesia is ~250 MB and replaces ~950 paginated API calls.  2025/2026 are 404 —
# bulk lags ~18 months — so history comes from bulk and the recent tail from the API.
FIRMS_BULK_URL = ("https://firms.modaps.eosdis.nasa.gov/data/country/{family}/{year}/"
                  "{sensor}_{year}_{country}.csv")
FIRMS_BULK_FAMILY = "viirs-snpp"
FIRMS_BULK_LAST_YEAR = 2024
FIRMS_BULK_COUNTRIES = ("Indonesia", "Malaysia", "Singapore")
# HEAD-verified Indonesian file sizes.  The file size alone tells the haze story, which is why
# it is in the spec as a chart: 2015 = 66.1 MB, 2019 = 33.0 MB, 2016 (La Nina) = 8.8 MB.
FIRMS_BULK_MB = {2015: 66.1, 2016: 8.8, 2019: 33.0, 2020: 6.9, 2023: 19.1, 2024: 7.9}
FIRMS_SOURCES = {
    "viirs_snpp_sp":  "VIIRS_SNPP_SP",      # 2012-01-20 -> 2026-04-27
    "viirs_snpp_nrt": "VIIRS_SNPP_NRT",     # 2026-04-28 -> today
    "modis_sp":       "MODIS_SP",           # 2000-11-01 -> 2026-04-30, base-rate context only
}
# COLLECTION TRAP: the bulk MODIS country files are Collection 6.1 (version "6.2") while the
# API's MODIS_SP returns Collection 6.0 (version "6.03").  Same sensor, same day, different row
# counts.  DO NOT CONCATENATE THEM.  VIIRS is consistent (version "2") in both routes.
MODIS_BULK_COLLECTION = "6.1"
MODIS_API_COLLECTION = "6.0"

# Filters.  Measured on VIIRS_SNPP_SP over the full box, 2019-09-15..19 (35,229 detections):
#   type 0 = 99.11 %, 1 = 0.07 %, 2 = 0.41 %, 3 = 0.41 %
#   confidence: n 91.2 %, l 5.7 %, h ONLY 3.2 %
# So filtering to high confidence would throw away 97 % of the signal — never do that.  And the
# type share is small AT PEAK; in quiet months the constant volcano/flare floor is what dominates,
# which is exactly why it has to go.
FIRMS_TYPE_FIRE = 0                 # 1 = volcano, 2 = other static land source, 3 = offshore
DROP_CONF = ("l",)                  # VIIRS: drop low only
MIN_CONF_MODIS = 30                 # MODIS publishes 0-100 (observed 41..100)
TYPE_ABSENT_IN_NRT = True           # see reconnaissance note 3 — build a static mask instead

# Static exclusion mask.  Built once from the SP archive (where ``type`` exists) plus OSM
# volcano nodes, then applied to EVERY product including NRT.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_VOLCANO_QL = ('[out:json][timeout:120];node["natural"="volcano"]'
                       "({s},{w},{n},{e});out body;")   # 295 nodes in the IDN bbox, ODbL
VOLCANO_EXCLUDE_KM = 5.0
PERSISTENT_CELL_DEG = 0.02          # ~2 km — a flare stack is a point, not a landscape
PERSISTENT_MIN_DAYS = 120           # active this many days in EVERY year -> industrial

# ── ERA5 / ERA5-Land (CDS) ────────────────────────────────────────────────────────────
# Measured by submitting real jobs and reading file:size without downloading, on the AOI box.
ERA5_SL = "reanalysis-era5-single-levels"
ERA5_PL = "reanalysis-era5-pressure-levels"
ERA5_LAND = "reanalysis-era5-land"

# BUILD DECISION 1 — ONLY INSTANTANEOUS FIELDS ARE REQUESTED FROM ERA5.
# The spec's measured 1.05/1.66/1.42 GB per year is for the full hourly record; 15 years of that
# is ~58 GB against a 31 GB disk and an 8 GB data budget, so the request has to be cut somewhere
# and WHERE it is cut is a modelling decision, not a plumbing one.  The cut taken here:
#   * four synoptic hours a day (00/06/12/18 UTC — 06 UTC is 13:00 WIB, near peak fire danger,
#     00 UTC is 07:00 WIB, near the humidity maximum), so daily max-T / min-RH are sampled at the
#     right end of the diurnal cycle rather than averaged away;
#   * NO ACCUMULATED VARIABLES AT ALL.  total_precipitation, potential_evaporation and the
#     radiation fields accumulate over the preceding hour, so summing 4 of 24 hours would report
#     one sixth of the rain — a silent, systematic dry bias exactly where the model is most
#     sensitive.  Precipitation therefore comes from CHIRPS, which is the drought/SPI source
#     anyway, and ERA5 supplies only instantaneous state.  This also means the request never
#     triggers the stepType split described in ingest_era5.py; the join-and-assert code is kept
#     because the assertion is what proves it.
ERA5_SL_VARS = [
    "10m_u_component_of_wind", "10m_v_component_of_wind",
    "2m_temperature", "2m_dewpoint_temperature",
    "surface_pressure", "boundary_layer_height",
    # ERA5-Land is dropped (decision 2 below); soil water comes from single levels at 0.25 deg.
    "volumetric_soil_water_layer_1", "volumetric_soil_water_layer_2",
    "volumetric_soil_water_layer_3",
    # naming corrections found live — the obvious guesses are wrong:
    "leaf_area_index_high_vegetation", "leaf_area_index_low_vegetation",
]
ERA5_SL_HOURS = ["00:00", "06:00", "12:00", "18:00"]
# ...with ONE exception, requested on its own so it can have all 24 hours.  Precipitation is the
# single most important predictor in a fire model and it is accumulated, so it cannot be sampled.
# Asked for alone it costs 1.05/13 = 0.081 GB per year — a rounding error — and it is summed to a
# genuine daily total rather than reconstructed from a sixth of one.  This is why the "no
# accumulated fields" rule above is stated as a rule about the MULTI-VARIABLE request.
ERA5_TP_VARS = ["total_precipitation"]
ERA5_TP_HOURS = [f"{h:02d}:00" for h in range(24)]
ERA5_PL_VARS = ["u_component_of_wind", "v_component_of_wind", "vertical_velocity"]
ERA5_LEVELS = ["925", "850", "700"]
ERA5_PL_HOURS = ["00:00", "06:00", "12:00", "18:00"]
# Trajectories are only ever run in the burning months, so the pressure-level pull is restricted
# to them: Feb-Mar (Riau's first peak, the one the Singapore record actually sees in 2014 and
# 2016) and Jun-Nov (the main Sumatra/Kalimantan season).
ERA5_PL_MONTHS = (2, 3, 6, 7, 8, 9, 10, 11)
# BUILD DECISION 2 — ERA5-Land is NOT pulled.  It is 0.1 deg against a 0.25 deg model grid, so
# nine ERA5-Land cells are averaged into one model cell before the model ever sees them; the
# resolution is spent and 1.42 GB/year is not.  Soil water layers 1-3 on single levels carry the
# same physics on the grid we actually model.
ERA5_LAND_VARS: list[str] = []
ERA5_SIZE_GB_PER_YEAR = {"single_levels": 1.05, "pressure_levels": 1.66, "era5_land": 1.42}
ERA5_LAG_DAYS = 6                   # measured: all three end 2026-08-24 against 2026-08-30
MIN_NONNULL = 0.95                  # assert on every column (the split-file bug)
WET_DAY_MM = 1.0

# CADS job store — the three Copernicus hosts speak the same OGC-API-Processes dialect, so one
# tiny client covers CDS, ADS and EWDS.  Submitted job ids are written here so a poll is
# resumable across process restarts and a queued job is never resubmitted.
JOBS_JSON = DATA_DIR / "cads_jobs.json"
CADS_HOSTS = {"cds": CDS_API_URL, "ads": ADS_API_URL, "ewds": EWDS_API_URL}
# Live-verified 2026-08-30 by submitting real jobs.  These are the exact URLs the account owner
# must open in a browser and accept; nothing else in this case is blocked.
POLICY_URLS = {
    "ads": ["https://ads.atmosphere.copernicus.eu/licences/terms-of-use-ads",
            "https://ads.atmosphere.copernicus.eu/licences/ads-data-protection-privacy-statement"],
    "ewds": ["https://ewds.climate.copernicus.eu/licences/terms-of-use-cems"],
    "earthdata": ["https://urs.earthdata.nasa.gov/ (GES DISC EULA — only if the S5P GES DISC "
                  "route is ever used; not used in this build)"],
}

# ── CAMS (ADS) — the chemistry benchmark and the surrogate ground truth ───────────────
CAMS_FORECAST = "cams-global-atmospheric-composition-forecasts"   # 2015-01-01 -> today, 0-120 h
CAMS_EAC4 = "cams-global-reanalysis-eac4"                         # 2003-01-01 -> 2025-12-31
CAMS_GFAS = "cams-global-fire-emissions-gfas"                     # 2003-01-01 -> 2025-12-03
CAMS_VARS = ["particulate_matter_2.5um",
             "organic_matter_aerosol_optical_depth_550nm",
             "black_carbon_aerosol_optical_depth_550nm",
             "total_aerosol_optical_depth_550nm",
             "total_column_carbon_monoxide"]
# GFAS is the find that changes the transport model: it converts FRP to emissions AND publishes
# injection height / plume top / plume bottom.  Plume height is what decides whether smoke stays
# in the boundary layer or reaches the 850 hPa flow toward Singapore — i.e. it replaces the
# crudest parameterisation in this case with a published product.  It ends 2025-12-03, so it is
# a training and backtest layer, not an operational one, and the spec says so.
# MEASURED 2026-08-30.  Two corrections, both found by requesting variables one at a time:
#   * ``wildfire_flux_of_particulate_matter_d_2_5_um`` is NOT a valid GFAS variable name — ADS
#     answers 400 "Request has not produced a valid combination of values".
#   * A seven-variable request is ACCEPTED and then silently returns only four: the emission
#     fluxes and plume top come back, ``injection_height`` and ``altitude_of_plume_bottom`` do
#     not.  Asked for on their own they are there: ``injh`` 0-2,462 m and ``apb`` 0-6,830 m.
# So the request is narrowed to the four variables this case actually uses.  The emission fluxes
# are dropped because FRP comes from FIRMS and nothing downstream reads oc/bc — carrying them
# was what pushed the request into whatever limit silently truncates it.
CAMS_GFAS_VARS = ["injection_height", "altitude_of_plume_top",
                  "altitude_of_plume_bottom", "wildfire_radiative_power"]
CAMS_GFAS_SHORT = {"injh": "injection_height_m", "apt": "plume_top_m",
                   "apb": "plume_bottom_m", "frpfire": "frp_w_m2"}
CAMS_GFAS_ENDS = "2025-12-03"

# ── CEMS fire indices (EWDS) — the baseline to beat, and it is nearly free ────────────
# Full Canadian FWI set PLUS keetch_byram_drought_index, so KBDI is NOT computed here.
FWI_DATASET = "cems-fire-historical-v1"
FWI_DOI = "10.24381/cds.0e89c522"
FWI_VARS = ["fire_weather_index", "fine_fuel_moisture_code", "duff_moisture_code",
            "drought_code", "initial_spread_index", "build_up_index",
            "daily_severity_rating", "keetch_byram_drought_index"]
FWI_COVERAGE = "1940-01-03 -> 2026-08-28 (~2-day latency)"
FWI_SIZE_MB_PER_YEAR = 29           # all seven Canadian indices, AOI-subset, compressed
FWI_LICENCE = "CC BY 4.0"
# NO open medium-range FWI FORECAST exists: cems-fire-forecast 404s and only cems-fire-seasonal
# (monthly, ~3 months stale) is published.  GWIS renders a 1-10 day forecast but does not publish
# it for bulk download.  THAT GAP IS THE COMMERCIAL OPENING for this case.
FWI_FORECAST_EXISTS = False

# ── Rainfall (CHC) ────────────────────────────────────────────────────────────────────
# CHIRPS v2 production ENDS after December 2026 — build on v3 paths from the start.
CHIRPS_V3_BASE = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/"
CHIRPS_FINAL_LAG_DAYS = 30          # measured: newest final 2026-07-31
CHIRPS_PRELIM_LAG_DAYS = 5          # measured: newest prelim 2026-08-25
CHIRPS_USE = "prelim"               # a days-ahead model trained on FINAL and served on PRELIM
                                    # has train/serve skew; train and serve on the same product
# CHIRPS-GEFS v3: bias-corrected GEFS v12 precipitation FORECASTS, lead days 0-15, 0.05 deg,
# issued same-day ~08:20 UTC, public domain, no auth.  This is the days-ahead driver and the
# reason the operational path is not simply reanalysis-with-a-lag.
CHIRPS_GEFS_BASE = "https://data.chc.ucsb.edu/products/CHIRPS-GEFS/v3/"
CHIRPS_GEFS_LEADS = tuple(range(0, 16))
CHIRPS_LICENCE = ("public domain — CHC: \"Pete Peterson has waived all copyright ... CHIRPS data "
                  "is in the public domain\"")
# CHC's own README: a %CCD bug zeroed precipitation where IR data was missing, and "This was
# always a problem for Eastern Australia/Indonesia/Japan, where a gap between two geostationary
# satellites exists."  Fixed in 2015 — but validate against BMKG gauges before quoting a trend.
# Also: CHIRPS "daily" is a disaggregated pentad, not an independent daily observation.
CHIRPS_IDN_CAVEAT = True

# ── Drought indices ───────────────────────────────────────────────────────────────────
# SPI is computed from CHIRPS here, not taken ready-made, because only a self-computed index can
# be run on the CHIRPS-GEFS FORECAST and stay consistent with the operational path.  The
# ready-made products are used to validate ours, not to replace it.
SPI_SCALES = (1, 3, 6)
SPI_VALIDATE_AGAINST = {
    "cds_derived_drought": dict(dataset="derived-drought-historical-monthly",
                                doi="10.24381/9bea5e16", licence="CC BY 4.0",
                                coverage="1940 -> 2026-07, 0.25 deg, SPI and SPEI"),
    "gdo_chirps_spi3": ("https://drought.emergency.copernicus.eu/data/"
                        "Drought_Observatories_datasets/"
                        "GDO_CHIRPS_Standardized_Precipitation_Index_SPI3/"),   # CC BY 4.0
}
REJECTED_DROUGHT = {
    "SPEIbase": "ODbL 1.0, not CC BY 4.0 — share-alike, so a derived database must be released "
                "under ODbL.  Viral-licence risk on a commercial deliverable; also 20 months "
                "stale and SPEI-only.  Excluded.",
}

# ── Sentinel-5P aerosol ───────────────────────────────────────────────────────────────
# DLR EOC L3 daily UV Aerosol Index: anonymous STAC, anonymous COG download, HTTP 206 range reads
# confirmed, and the AOI falls inside ONE 512x512 tile at 0.97 MB/day — about 1.2 GB for the whole
# 2018-2026 archive, with same-day latency and no L2 swath handling at all.
S5P_DLR_STAC = "https://geoservice.dlr.de/eoc/ogc/stac/v1/"
S5P_DLR_COLLECTION = "S5P_TROPOMI_L3_P1D_AI_v2"
S5P_DLR_MB_PER_DAY = 0.97
# LICENCE CONFLICT — resolve before launch.  The collection's ``license`` string reads
# "CC-BY-4.0" but the URL in that same field AND the rel:license link both point at CC BY-NC 4.0.
# Four of DLR's 73 collections carry the NC link while siblings are clean, so it is not a global
# typo.  Written confirmation from DLR EOC is required before this layer is published.
S5P_DLR_LICENCE_CONFLICT = True
# CO has no DLR product.  s3://meeo-s5p is anonymous (AWS Open Data, eu-central-1, no
# requester-pays) and its COGT/ prefix holds reprojected COGs under the plain Copernicus notice.
S5P_CO_S3 = "s3://meeo-s5p/COGT/{proc}/L2__CO____/{y}/{m}/{d}/"
REJECTED_S5P = {
    "CDSE STAC": "indexes only ~the last 45 days despite advertising 2018 -> present; a 2019 "
                 "backtest silently returns zero items.  OData is complete but download needs a "
                 "free account.  No CDSE L3 exists (all 419 collections enumerated).",
    "S5P-PAL": "data.s5p-pal.com is NXDOMAIN (the live host is data-portal.s5p-pal.com); the L3 "
               "is 932 MB/day for AAI with no subsetting and self-describes as pre-operational "
               'with "long term availability not guaranteed" -> rejected.',
    "s5phub.copernicus.eu": "NXDOMAIN",
    "GES DISC": "has the collections and advertises subsetting, but the Earthdata token hit "
                "403 EULA Acceptance Failure — one browser click at the resolution_url fixes it "
                "(same pattern as the Black Marble note in .env.example).",
}

# ── Static layers: peat, land cover ───────────────────────────────────────────────────
# Peat PRESENCE is settled and already licensed by Case H (gfw_peatlands, CC BY 4.0).  DEPTH is
# the genuinely hard one, and the honest finding is that no open, commercially-licensed,
# national, high-resolution Indonesian peat-depth raster exists.
PEATGRIDS = dict(doi="10.5281/zenodo.12559239", licence="CC BY 4.0",
                 files=("global_peatThickness_v1.tif", "global_peatCstock_v1.tif"),
                 size_mb=(57.5, 57.8), res_m=1000,
                 cite="Widyastuti et al. 2025, CATENA",
                 note="Zenodo returns 403 to some egress IPs (anti-bot, not a gate)")
# BIG Satu Peta — KLHK Peta Fungsi Ekosistem Gambut at 1:50,000: 15,503 polygons in the AOI, with
# `peat_thick` as 0.5 m ordinal bins and `feg_peat` giving the regulatory <3 m / >3 m split.
# NO STATED LICENCE (CKAN: "License not specified") -> reference/validation only, never stored.
BIG_PEAT_MAPSERVER = ("https://kspservices.big.go.id/satupeta/rest/services/PUBLIK/"
                      "SUMBER_DAYA_ALAM_DAN_LINGKUNGAN/MapServer/48")
BIG_KHG_MAPSERVER = BIG_PEAT_MAPSERVER.rsplit("/", 1)[0] + "/37"   # peat hydrological units
PEAT_DEPTH_PRIMARY = "peatgrids"     # licence-clean; 1 km, and the resolution loss is STATED
WORLDCOVER_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
                  "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")   # anonymous, true COG
WORLDCOVER_LICENCE = "CC BY 4.0"
WORLDCOVER_AOI_BYTES = 817_112_053   # 38 tiles; 7 grid cells are all-ocean and absent
# Do NOT diff v100 (2020) against v200 (2021): different algorithm versions, so the difference is
# dominated by method change rather than by land-cover change.
# KLHK Penutupan Lahan 2024 at 1:250,000 — reachable, and richer than expected.  The path is
# /server/rest/services, not /arcgis/....  NAMAOBJ and FCODE are literally "-"; the class is the
# integer PL2024_ID (23 classes via /legend), and PL2023_ID_R in the SAME row gives a free,
# methodologically-consistent one-year change flag that WorldCover cannot provide.
KLHK_LANDCOVER = ("https://geoportal.planologi.kehutanan.go.id/server/rest/services/"
                  "Peta_Interaktif_2026/PL_AR_250K/MapServer/0")
KLHK_FIRE_HAZARD = ("https://geoportal.planologi.kehutanan.go.id/server/rest/services/"
                    "Peta_Interaktif_2026/RAWAN_KARHUTLA_AR_250K/MapServer")  # official benchmark
KLHK_USE_LIMIT = "not to be used as reference at scales finer than 1:250,000"
KLHK_LICENCE = None                  # no open licence granted -> attribute + legal review

# ── ENSO / IOD — every URL verified returning data ────────────────────────────────────
ENSO_URLS = {
    "oni": "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt",
    "nino_all": "https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices",
    # wksst9120, NOT wksst8110 — the latter still resolves but froze at 2021-01-27 when the base
    # period changed, which is exactly the kind of silent staleness that poisons a feature.
    "nino34_weekly": "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for",
    # Every BoM SOI URL is dead (404).  Long Paddock is better anyway: DAILY, ~2-day latency,
    # CC BY 4.0 (State of Queensland).
    "soi_daily": ("https://data.longpaddock.qld.gov.au/SeasonalClimateOutlook/"
                  "SouthernOscillationIndex/SOIDataFiles/DailySOI1933-1992Base.txt"),
    "dmi": "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data",
}
# DMI is the weak link: the HadISST series ends 2026-05, is stamped "Preliminary", and runs ~3
# months behind.  Usable as a historical feature, NOT as an operational one — an unresolved gap
# that the spec states rather than papers over.
DMI_OPERATIONAL = False

# ── Trajectory model ──────────────────────────────────────────────────────────────────
TRAJ_HOURS = 72
TRAJ_DT_MIN = 60
TRAJ_LEVELS = {"925": 0.5, "850": 0.4, "700": 0.1}   # fixed blend, NOT fitted (see transport.py)
TRAJ_ENSEMBLE = 50                  # parcels per release, jittered in height and release hour
# Injection height comes from GFAS where GFAS exists (2003 -> 2025-12-03); PLUME_RISE is the
# fallback for the operational tail, and its use is flagged per run.
PLUME_RISE = (300, 800, 1500)
DECAY_HALFLIFE_H = 36.0             # crude removal proxy; NOT deposition physics

# ── Model ─────────────────────────────────────────────────────────────────────────────
LEAD_DAYS = (1, 3, 7)
FEATURE_LAG_DAYS = 1                # every predictor available at t-1; enforced and asserted
TRAILING_WINDOWS = (7, 30, 365)
CV_SCHEME = "blocked-by-season"     # never random: adjacent cell-days are the same row twice

# ── Gates (thresholds fixed BEFORE any result was seen) ───────────────────────────────
GATES = ("G-J1", "G-J2", "G-J3", "G-J4", "G-J5")
# Measured composition at the 2019 peak: type!=0 removes 0.89 %, low confidence a further 5.7 %,
# for ~6.5 % total.  Away from the peak the constant volcano/flare floor is a much larger share,
# which is the point.  The gate floor is set below the peak figure so that a season-weighted
# removal share above it is the expected outcome and a near-zero share is a loud failure.
GATE_REMOVED_MIN_SHARE = 0.005
GATE_AUC = 0.80
GATE_BSS = 0.0                      # must beat per-cell day-of-year climatology AND the FWI
GATE_BEARING_DEG = 30.0
GATE_BEARING_SHARE = 0.70
GATE_RHO = 0.50                     # HARD at Singapore (tier 1); reported per receptor elsewhere
GATE_ANCHOR_DECILE = 0.90           # 2015 and 2019 must land in the top decile of severity

# ── Export ────────────────────────────────────────────────────────────────────────────
WEB_BUDGET_MB = 3.0
EXPORT_EPISODE_DAYS = 400           # precomputed trajectory sets: the signature must be instant

# ── Resource guards (shared 16 GB box, several other heavy jobs) ──────────────────────
MIN_FREE_DISK_GB = 10.0
DATA_BUDGET_GB = 8.0


def free_gb(path: Path | str = "/") -> float:
    return shutil.disk_usage(str(path)).free / 2**30


def disk_ok(need_gb: float = 0.5) -> bool:
    return free_gb() - need_gb >= MIN_FREE_DISK_GB


def gfw_key_ok() -> bool:
    """Exercise an AUTHENTICATED endpoint — /dataset/{id} is public and proves nothing."""
    import requests
    if not GFW_API_KEY:
        return False
    r = requests.get("https://data-api.globalforestwatch.org/datasets",
                     headers={"x-api-key": GFW_API_KEY}, timeout=60)
    return r.status_code == 200
