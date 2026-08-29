"""Flagship B constants. Spec: https://claude.ai/code/artifact/5f595e20-bfa6-49e5-b400-bf36ff9ab1a7 (§B2-B3)."""

from pathlib import Path

CASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = CASE_DIR / "data"

# CEPII BACI, HS92 revision. Etalab 2.0 license. Exact URL verified 2026-08-29:
# https://www.cepii.fr/DATA_DOWNLOAD/baci/data/BACI_HS92_V202601.zip (2.42 GB)
BACI_BASE = "https://www.cepii.fr/DATA_DOWNLOAD/baci"
BACI_RELEASE = "202601"       # data through 2024
YEARS = range(1995, 2025)

# Atlas-style sample filter (spec §B3 · filter)
MIN_POPULATION = 1_000_000
MIN_TRADE_USD = 1_000_000_000

# Complexity computed at HS4 (~1,200 products); HS6 kept for the nickel chain.
NICKEL_HS6 = ("2604", "7202", "7501", "7502", "7503", "7504",
              "7505", "7506", "7507", "7508")

# Product-space graph (spec §B3 · layout)
PHI_THRESHOLD = 0.55
LAYOUT_SEED = 360             # Indonesia's ISO numeric — fixed, documented

# Peer set for trajectory comparisons
PEERS = ("VNM", "THA", "MYS", "PHL", "IND")
IDN = "IDN"

DB = DATA_DIR / "trade.duckdb"
LAYOUT_JSON = DATA_DIR / "product_space_layout.json"
STATS_JSON = DATA_DIR / "stats.json"

# Gates (spec §B4)
GATE_SPEARMAN = 0.90          # G-B1 vs Harvard Atlas, every overlap year
ATLAS_IDN_2023 = (69, 133)    # Atlas: Indonesia rank 69 of 133 economies (2023 data)
GATE_IDN_RANK = (66, 72)      # G-B1 strict: rank within ±3 when the sample matches
GATE_PCT_TOLERANCE = 0.05     # G-B1 relaxed: percentile-from-top within 5 points
GATE_BPS_TOLERANCE = 0.05     # G-B3
