"""Case D constants — Indonesia in the Global Narrative.
Decision D20 (user, 2026-08-30): NO BigQuery — GDELT's open feeds only
(DOC 2.0 API + raw 15-minute CSVs), pipeline runs on the dev server.
Endpoints/limits pinned from the 2026-08-30 reconnaissance."""

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

# ── GDELT DOC 2.0 API — free, keyless, rate-limited (~1 req / 10–15 s) ──────────
DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
DOCAPI_CACHE = RAW / "docapi"           # one JSON per (query, mode, window) — reruns cost 0 calls
DOCAPI_MIN_SPACING_S = 12.0             # polite spacing between live requests
DOCAPI_TIMEOUT_S = 180
DOCAPI_MAX_RETRIES = 6
CURVES = DATA_DIR / "docapi_curves.parquet"   # long table: qid, mode, series, date, value, norm
WINDOW_START = "20170101000000"          # DOC API archive starts 2017-01-01 exactly (D22)

# Query battery. qid → (query string, [modes]). `sourcecountry:` is the PUBLISHER's
# country (FIPS "ID" = Indonesia); the keyword is the story's subject.
THEME_QUERIES = {
    "nickel":     'Indonesia nickel',
    "palm_oil":   'Indonesia ("palm oil" OR sawit)',
    "ikn":        'Indonesia (Nusantara OR IKN) capital',
    "election":   'Indonesia election',
    "flood":      'Indonesia (flood OR floods OR flooding OR banjir)',
    "earthquake": 'Indonesia (earthquake OR tsunami OR volcano OR eruption)',
    "protest":    'Indonesia (protest OR protests OR protesters OR demonstration)',
    "covid":      'Indonesia (COVID OR coronavirus OR pandemic)',
    "terror":     'Indonesia (terrorism OR terrorist OR bombing)',
    "papua":      'Indonesia Papua',
    "coal":       'Indonesia (coal OR mining)',
    "forest":     'Indonesia (deforestation OR haze OR "forest fires" OR wildfires)',
    "cyber":      'Indonesia (ransomware OR cyberattack OR hackers OR "data breach")',
    "tourism":    'Indonesia (tourism OR tourists OR Bali)',
    "football":   'Indonesia (football OR soccer OR stadium)',
    "asean":      'Indonesia ASEAN',
    "g20":        'Indonesia G20',
    "ev_battery": 'Indonesia (battery OR "electric vehicle" OR EV)',
    "economy":    'Indonesia (rupiah OR inflation OR "central bank" OR GDP)',
    "china":      'Indonesia China',
}
QUERIES: dict[str, tuple[str, list[str]]] = {
    "indonesia":          ("Indonesia", ["TimelineVol", "TimelineVolRaw", "TimelineTone",
                                         "TimelineSourceCountry", "TimelineLang"]),
    "indonesia_foreign":  ("Indonesia -sourcecountry:ID", ["TimelineVol", "TimelineTone"]),
    "indonesia_domestic": ("Indonesia sourcecountry:ID", ["TimelineVol", "TimelineTone"]),
    **{f"theme_{k}": (q, ["TimelineVol"]) for k, q in THEME_QUERIES.items()},
}
# themes that also get a tone curve (the stories where tone is the story)
TONE_THEMES = ("nickel", "palm_oil", "election", "protest", "football", "g20")
for _k in TONE_THEMES:
    QUERIES[f"theme_{_k}"][1].append("TimelineTone")

# ── GDELT raw 15-minute feed (fully open bulk) ──────────────────────────────────
MASTERFILE = "http://data.gdeltproject.org/gdeltv2/masterfilelist.txt"          # 301 → https
MASTERFILE_TRANS = "http://data.gdeltproject.org/gdeltv2/masterfilelist-translation.txt"
LASTUPDATE = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
EVENTS_DIR = DATA_DIR / "events"         # events_<feed>_<YYYYMM>.parquet, one per month
EVENTS_STATS = EVENTS_DIR / "daily_stats.csv"   # day, feed, files, rows, kept, bytes (denominator)
EVENTS_WORKERS = 6                       # concurrent downloads (user cap)
EVENTS_START_YEAR = 2017                 # D22: one shared window 2017→now

# Indonesia filters — NOTE the coding split in GDELT events:
#   ActionGeo_CountryCode uses FIPS ("ID"); Actor1/2CountryCode uses CAMEO/ISO3 ("IDN").
FIPS_ID = "ID"
ISO3_ID = "IDN"

# Export CSV layout (61 tab-separated columns, no header). 0-based indices of what we keep.
EXPORT_COLS = {
    "event_id": 0, "day": 1, "actor1_code": 5, "actor1_name": 6, "actor1_country": 7,
    "actor1_type": 12, "actor2_code": 15, "actor2_name": 16, "actor2_country": 17,
    "actor2_type": 22, "is_root": 25, "event_code": 26, "event_root": 28, "quad_class": 29,
    "goldstein": 30, "num_mentions": 31, "num_sources": 32, "num_articles": 33,
    "avg_tone": 34, "action_country": 53, "action_adm1": 54, "action_lat": 56,
    "action_lon": 57, "action_name": 52, "date_added": 59, "source_url": 60,
}
COL_ACTOR1_COUNTRY, COL_ACTOR2_COUNTRY, COL_ACTION_COUNTRY = 7, 17, 53
N_EXPORT_COLS = 61

# Ledger: one parquet of daily Indonesia signals (attention, tone, themes, events)
LEDGER = DATA_DIR / "narrative_daily.parquet"
STATS = DATA_DIR / "stats.json"

# ── Validation anchors (gate G-D2) ─────────────────────────────────────────────
# Each anchor: expected signatures the pipeline must reproduce UNPROMPTED, judged
# against a trailing baseline. Signature keys:
#   attention  — API normalized volume in [day, day+window) vs trailing 28-day median
#   tone_drop  — API tone in the window at least TONE_DROP below trailing 28-day mean
#   protest    — event-layer protest count (EventRootCode 14) vs trailing baseline
#   report_only — computed and published, but not a pass/fail claim (spec: G20 tone)
ANCHORS = {
    "2018-09-28": {"label": "Palu earthquake & tsunami", "expect": ["attention", "tone_drop"], "window": 4},
    "2019-05-22": {"label": "post-election riots, Jakarta", "expect": ["attention", "protest", "tone_drop"], "window": 3},
    "2020-03-02": {"label": "first COVID-19 cases announced", "expect": ["attention"], "window": 3},
    "2022-10-01": {"label": "Kanjuruhan stadium disaster", "expect": ["tone_drop", "attention"], "window": 3,
                   "quarter_min_tone": True},
    "2022-11-15": {"label": "G20 Bali summit", "expect": ["attention"], "report_only": ["tone_rise"], "window": 3},
    "2024-02-14": {"label": "presidential election", "expect": ["attention"], "window": 3},
    "2024-06-20": {"label": "PDNS ransomware attack", "expect": ["attention", "theme_cyber"], "window": 8},
    "2025-08-28": {"label": "Aug–Sep 2025 protests", "expect": ["attention", "protest", "tone_drop"], "window": 7},
}
BASELINE_DAYS = 28            # trailing window for baselines (ends the day before the anchor)
ATTENTION_RATIO = 1.5         # window peak / baseline median must reach this
PROTEST_RATIO = 3.0           # event-layer protest count ratio
TONE_DROP = 1.0               # tone points below trailing mean
G_D3_MIN_RHO = 0.6            # Spearman between event-layer share and API volume

# Eras for chapter 1 — narrative regimes; the data decides the numbers.
ERAS = [
    ("2017-01-01", "2019-12-31", "Pre-pandemic"),
    ("2020-01-01", "2021-12-31", "Pandemic"),
    ("2022-01-01", "2023-12-31", "G20 & ASEAN chair"),
    ("2024-01-01", "2024-12-31", "Election year"),
    ("2025-01-01", "2026-12-31", "Prabowo era & 2025 protests"),
]

BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
