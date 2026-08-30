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

# --------------------------------------------------------------------------
# Partners & imports extension — gates G-B5..G-B7.
# ALL THRESHOLDS BELOW WERE FIXED BEFORE ANY PARTNER/IMPORT NUMBER WAS READ.
# Repo rule: if the data contradicts the story, the story is rewritten.
# --------------------------------------------------------------------------

# G-B5 — export reconciliation. BACI's IDN export total must land within
# tolerance of the independently verified 2023 benchmark. The project's
# verified figure is UN Comtrade US$259.5B; BPS publishes US$258.82B for the
# same year. We reconcile against Comtrade and report the signed deviation.
BENCH_IDN_EXPORTS_2023 = 259.5e9      # UN Comtrade (project-verified), USD
BPS_IDN_EXPORTS_2023 = 258.82e9       # BPS Jan–Dec 2023, USD
GATE_G_B5_TOLERANCE = 0.05            # ±5 %

# G-B6 — import coverage. BPS publishes 2023 imports at US$221.89B, but that
# is a CIF valuation; BACI harmonizes every flow to FOB, so BACI imports are
# EXPECTED to sit below the BPS figure by roughly the freight-and-insurance
# margin. The gate therefore allows a wider, deliberately asymmetric band and
# the direction of the miss is itself reported.
BPS_IDN_IMPORTS_2023 = 221.89e9       # BPS Jan–Dec 2023, CIF, USD
GATE_G_B6_TOLERANCE = 0.10            # ±10 %, FOB-vs-CIF gap expected negative

# Published BPS annual totals (USD), added AFTER G-B5 failed on 2023 so the
# deviation could be shown as a multi-year pattern rather than one point.
# These are benchmarks, not thresholds — the G-B5 tolerance above is unchanged.
BPS_EXPORTS = {2022: 291.90e9, 2023: 258.82e9, 2024: 264.70e9}
BPS_IMPORTS = {2023: 221.89e9}

# G-B7 — the nickel capital-goods test. PRE-REGISTERED HYPOTHESIS:
# if the ~15× rise in processed-nickel exports was built by domestic smelters,
# the build-out must show up on the import side. Windows are fixed here:
# base = 2013–2015 (pre-build), peak = 2021–2024 (post-build).
G_B7_BASE_YEARS = (2013, 2014, 2015)
G_B7_PEAK_YEARS = (2021, 2022, 2023, 2024)
# H1 capital goods: HS84+HS85 imports grow >= +50 % base->peak.
G_B7_H1_CAPGOODS_GROWTH = 0.50
# H2 excess: that growth must beat TOTAL import growth by >= 10 points, so a
# generic import boom cannot pass the test on its own.
G_B7_H2_EXCESS_POINTS = 0.10
# H3 process inputs: at least one smelter input grows >= +100 % base->peak.
G_B7_H3_INPUT_GROWTH = 1.00
# Smelter input basket (HS92, prefix match), chosen for what a nickel/stainless
# smelter must actually buy abroad — Indonesia already has thermal coal and
# limestone domestically, so the tells are coke, electrodes, chrome and scrap.
G_B7_INPUTS = {
    "2704": "Coke & semi-coke",
    "8545": "Carbon & graphite electrodes",
    "2610": "Chromium ores",
    "7204": "Ferrous waste & scrap",
    "2701": "Coal",
    "2521": "Limestone flux",
    "2522": "Quicklime & hydraulic lime",
}
G_B7_CAPGOODS = ("84", "85")
# Verdict: PASS = H1+H2+H3, PARTIAL = exactly two, FAIL = one or none.

# Derived partner/import outputs (kept compact — no 330M-row DuckDB rebuild)
IDN_SLICE_DIR = DATA_DIR / "idn_slice"        # one parquet per year, resumable
PARTNER_STATS = DATA_DIR / "partner_stats.json"
