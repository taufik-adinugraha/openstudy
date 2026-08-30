"""Case D constants — Indonesia in the Global Narrative.
Decision D20 (user, 2026-08-30): NO BigQuery — GDELT's open feeds only
(DOC 2.0 API + raw 15-minute CSVs), pipeline runs on the dev server.
Exact endpoints/limits to be pinned from the scout report before build."""

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

# GDELT DOC 2.0 API — free, keyless. Timeline modes give normalized volume/tone.
DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# GDELT raw 15-minute feed (fully open bulk):
MASTERFILE = "http://data.gdeltproject.org/gdeltv2/masterfilelist.txt"
MASTERFILE_TRANS = "http://data.gdeltproject.org/gdeltv2/masterfilelist-translation.txt"

# Indonesia filters — NOTE the coding split in GDELT events:
#   ActionGeo_CountryCode uses FIPS ("ID"); Actor1/2CountryCode uses CAMEO/ISO3 ("IDN").
FIPS_ID = "ID"
ISO3_ID = "IDN"

# Ledger: one parquet of daily Indonesia signals (attention, tone, themes, events)
LEDGER = DATA_DIR / "narrative_daily.parquet"

# Validation anchors (gate G-D2): dated events whose signature must appear.
# To be finalized from the scout's verified list.
ANCHORS = {
    "2019-05-22": "post-election riots (protest/conflict spike, tone drop)",
    "2020-03-02": "first COVID cases announced (attention spike)",
    "2022-10-01": "Kanjuruhan stadium disaster (sharp negative tone)",
    "2022-11-15": "G20 Bali summit (attention + positive tone)",
    "2024-02-14": "presidential election (attention spike)",
    "2024-06-20": "PDNS ransomware attack (tech/governance themes)",
}

BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
