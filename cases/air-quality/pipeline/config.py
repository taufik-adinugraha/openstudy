"""Case E constants — Jabodetabek Air Quality Nowcast.

Spec lives in ../README.md (§Spec). No docs/spec-air-quality.html exists for
this case; the README is the contract.

Sources and their licences (mirrored in the dashboard methodology footer):
  ERA5 hourly single levels  Copernicus/ECMWF, CC-BY-4.0 (licence accepted 2026-08-30)
  NASA FIRMS VIIRS hotspots  NASA, public domain / free & open
  OpenAQ v3 ground sensors   OpenAQ, CC-BY-4.0 (per-provider terms apply)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = CASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
WEB_DATA = CASE_DIR / "web" / "public" / "data"      # fetched by the browser
WEB_SRC_DATA = CASE_DIR / "web" / "src" / "data"     # imported at build time (summary only)
REPO_ROOT = Path(__file__).resolve().parents[3]


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

# ── Geography ────────────────────────────────────────────────────────────
# Jabodetabek = Jakarta + Bogor, Depok, Tangerang, Bekasi. Ground sensors and
# ERA5 meteorology are pulled for this box.
AQ_BBOX = (106.3, -6.9, 107.2, -5.9)   # west, south, east, north

# ERA5 request area is CDS-ordered: North, West, South, East, snapped to the
# 0.25 deg grid so the returned array is a clean 5 x 4 block.
ERA5_AREA = [-5.75, 106.25, -6.75, 107.25]

# The airshed that can send smoke to Jakarta: Sumatra (peat), all of Java,
# southern Kalimantan. FIRMS hotspots are counted inside this box only.
FIRE_BBOX = (95.0, -9.0, 119.0, 6.0)

# Fire "upwind sectors" are defined relative to Jakarta's centroid.
JKT_LAT, JKT_LON = -6.2, 106.85

# ── Time window ──────────────────────────────────────────────────────────
# 2023-01 start: OpenAQ's usable Jabodetabek record effectively begins in
# 2023 (the two US-Embassy posts died in 2016; the low-cost network came up
# through 2022-2023). Three full fire seasons (2023 was a strong El Nino
# burning year, 2024 and 2025 weaker) is enough seasonal structure without
# blowing the 5 GB data budget.
START = "2023-01-01"

# ── Endpoints ────────────────────────────────────────────────────────────
OPENAQ_BASE = "https://api.openaq.org/v3"
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api"
# VIIRS_SNPP_SP = standard-processing archive (goes back to 2012);
# VIIRS_SNPP_NRT = near-real-time (last ~2 months). The archive lags NRT by
# roughly 2-3 months, so the pipeline reads SP where it exists and NRT after.
FIRMS_ARCHIVE_SRC = "VIIRS_SNPP_SP"
FIRMS_NRT_SRC = "VIIRS_SNPP_NRT"

ERA5_DATASET = "reanalysis-era5-single-levels"
ERA5_VARS = [
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "boundary_layer_height",
    "2m_temperature",
    "2m_dewpoint_temperature",
    "total_precipitation",
    "surface_solar_radiation_downwards",
]

# ── Resource guards (shared 16 GB box, four other heavy jobs) ────────────
MIN_FREE_DISK_MB = 10_000


def disk_free_mb(path: Path | None = None) -> int:
    return shutil.disk_usage(path or CASE_DIR).free // (1024 * 1024)


def guard_disk(log) -> bool:
    """Return False (and log) when free disk is under the floor. Every stage
    is resumable, so the caller just exits 0."""
    free = disk_free_mb()
    if free < MIN_FREE_DISK_MB:
        log(f"DISK GUARD: only {free} MB free (floor {MIN_FREE_DISK_MB} MB) — exiting cleanly, rerun later")
        return False
    return True


# ── Forecast contract ────────────────────────────────────────────────────
HORIZONS = (1, 3, 6, 12, 24, 48, 72)   # hours ahead
EPISODE_THRESHOLD = 55.5               # ug/m3 — US EPA PM2.5 "Unhealthy" breakpoint
TEST_FRACTION = 0.25                   # final 25% of the timeline, held out by TIME
