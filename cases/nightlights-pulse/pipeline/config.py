"""Flagship A constants. Spec: https://claude.ai/code/artifact/06da6f68-9ed2-4a61-b8ce-bac9086856d3 (§A2-A3).

Lights source per decision D12 (2026-08-29): NASA Black Marble primary.
EOG limited programmatic access to paid subscribers on 2026-06-01; EOG VNL
survives only as a once-a-year MANUAL cross-check download.
"""

import os
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = CASE_DIR / "data"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_env() -> None:
    """Load repo-root .env into os.environ (no override), so `make` targets
    run without manual sourcing."""
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


_load_env()

# BPS's WAF blocks default curl/requests fingerprints — always send this.
BPS_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}

# NASA Black Marble monthly composites via LAADS DAAC (fully open, incl.
# commercial). VNP46A3 (Suomi-NPP, 2012 -> production ends 2026-11) spliced
# with VJ146A3 (NOAA-20, 2018-01 -> now), inter-calibrated on the overlap.
# Both products verified under archive set 5200 (probed 2026-08-29).
LAADS_BASE = "https://ladsweb.modaps.eosdis.nasa.gov/archive/allData/5200"
PRODUCTS = ("VNP46A3", "VJ146A3")
SPLICE_START = "2018-01"   # first VJ146A3 month (verified granule A2018001)

# Black Marble land tiles covering Indonesia (10x10 deg linear grid):
# lon 95E-141E -> h27..h32; lat 6.5N-11.5S -> v08..v10.
# Ocean-only tiles don't exist in the archive and are skipped on 404.
TILES = tuple(f"h{h:02d}v{v:02d}" for h in range(27, 33) for v in range(8, 11))

# Indonesia bbox (WGS84): clip immediately on ingest, store only the clip.
BBOX = (94.5, -11.5, 141.5, 6.5)  # west, south, east, north

# Load-bearing rule (spec §A2): one frozen boundary vintage for the whole
# series; other years' BPS codes map through the pemekaran crosswalk.
# Source: geoBoundaries gbOpen IDN ADM2 (519 units, 2020 vintage, CC BY 3.0 IGO
# — commercial OK; chosen over GADM, whose license is non-commercial).
BOUNDARY_VINTAGE = "geoBoundaries-2020"
BOUNDARIES = DATA_DIR / "boundaries" / "geoBoundaries-IDN-ADM2.geojson"
REGION_ID = "shapeID"
REGION_NAME = "shapeName"
CROSSWALK = DATA_DIR / "crosswalk" / "pemekaran_crosswalk.csv"  # shapeID <-> BPS code, week-2 task

# Masking (spec §A3 · mask): the composite's own QA layers.
MIN_NUM_OBS = 2          # observation-count threshold per cell
# Flare buffers: VNF flare survey sites (ORNL DAAC doi:10.3334/ORNLDAAC/1874).
# Spec said 5 km, but the measured radial glow profile (flares.py analysis,
# 2026-08-30) shows ~84% of a flare's excess light inside 3 km while 5 km
# swallows whole towns (Kota Sorong −72%, Bontang −71% of SOL) — so 3 km is
# the primary radius; 5 km and 1.5 km are kept as sensitivity columns.
FLARE_BUFFER_KM = 3.0
# TODO week 2: pin exact HDF5 dataset names (NearNadir_Composite_Snow_Free etc.)

LEDGER = DATA_DIR / "ledger.parquet"   # regency × month sum-of-lights ledger
STATS_JSON = DATA_DIR / "stats.json"   # feeds the insight brief
