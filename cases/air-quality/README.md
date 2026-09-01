# Case E — Jabodetabek Air Quality Nowcast

**The operational case.** Every other case in the lab ships a reproducible
snapshot. This one runs: scheduled acquisition → feature build → model →
gates → dashboard, with vintage stamps that move. 24–72 h PM2.5 forecasts for
Greater Jakarta from ERA5 meteorology, NASA FIRMS fire hotspots and OpenAQ
ground sensors.

Identity: `data-case="airquality"` — washed sky cyan `#6FC7D6` on a dark
ground. The AQI colour ramp is **data-only** and never doubles as the accent.

Dashboard: <http://18.141.229.57:4328/airquality> (port 4328, base `/airquality`).

---

## Spec

There is no `docs/spec-air-quality.html`; this section is the contract.

### E1 · Question

*Will the air in Jakarta be bad tomorrow, and why?* A city agency, a school
network, a logistics operator or an occupational-health team needs a number
with a horizon and an error bar — not a current-conditions dial.

### E2 · Sources

| Source | What it gives | Access | Licence |
|---|---|---|---|
| **OpenAQ v3** | hourly ground PM2.5 per station (the target) | `X-API-Key`, key in repo `.env` | CC-BY 4.0 + per-provider terms |
| **ERA5 single levels** (Copernicus CDS) | hourly 10 m wind u/v, boundary-layer height, 2 m temperature + dewpoint, total precipitation, surface solar radiation | `cdsapi`, `~/.cdsapirc` on the server | CC-BY 4.0 (accepted 2026-08-30) |
| **NASA FIRMS VIIRS** | fire hotspots (`VIIRS_SNPP_SP` archive 2012→2026-04-27, `VIIRS_SNPP_NRT` after) | `FIRMS_MAP_KEY` | free & open |
| *Sentinel-5P TROPOMI* | aerosol / NO₂ / CO columns | **not ingested** — see Pending | — |

Geography: `AQ_BBOX = 106.3, −6.9, 107.2, −5.9` (Jabodetabek) for sensors and
meteorology; `FIRE_BBOX = 95, −9, 119, 6` (Sumatra + Java + south Kalimantan)
for the airshed that can send smoke to Jakarta.

Window: **2023-01-01 →** present. Jabodetabek's usable OpenAQ record
effectively begins in 2023 — the two US-Embassy monitors died in 2016 and the
low-cost network came up through 2022–23. Three fire seasons (a strong El Niño
year in 2023, weaker 2024 and 2025) give seasonal structure without spending
the 5 GB data budget.

### E3 · Pipeline DAG

```
ingest_ground.py  OpenAQ /locations + /sensors/{id}/hours   → stations.json, ground_hourly.parquet
ingest_era5.py    CDS reanalysis-era5-single-levels          → era5_hourly.parquet
ingest_firms.py   FIRMS area API, reduced on arrival         → fire_daily.parquet
                  static-source mask, built once from the      firms_static_mask.parquet
                  SP archive and applied to both products      fire_filter_audit.parquet
        ↓
features.py       lagged PM2.5 + meteorology + upwind fire   → features.parquet
model.py          direct GBM per horizon vs persistence      → model_eval / predictions / forecast
validate.py       gates G-E1…G-E6                            → stats.json
export_web.py     NaN-safe view models                       → web/public/data/*.json
        ↓  (review layer — nothing is refitted)
replicate.py      5 more baselines on the same scored rows,  → replication.json
                  day-block bootstrap, panel decomposition,
                  co-location, AQI ladder
article.py        + summary.json + published benchmarks      → web/src/data/article.json
```

Every ingest stage is resumable and idempotent: work is keyed by
sensor-month, ERA5 month, and FIRMS 5-day window, and an artefact that exists
is skipped (the current month / last 3 days are always refetched, because they
are still filling). Raw ERA5 NetCDF is deleted the moment it is aggregated;
raw FIRMS hotspots are reduced to sector-day counts in memory and never hit
disk. The FIRMS static-source mask is keyed by sampled window and cached, so
it is built once and not refetched. Each stage checks free disk first and
exits 0 below 10 GB.

Reproduce everything: `make all`. Refresh on a schedule: `make refresh`.

### E4 · Model

Direct multi-horizon gradient boosting (`HistGradientBoostingRegressor`), one
model per horizon in {1, 3, 6, 12, 24, 48, 72} h, trained on `log1p(PM2.5)`
because the distribution is strongly right-skewed and the episodes that matter
live in the tail. Recursive rollout was rejected: it compounds its own error
and hides it.

Features are **issue-time only** — lagged PM2.5 (1–48 h) and its rolling mean,
spread and differences; boundary-layer height, wind speed and direction,
ventilation index (wind × BLH), temperature, RH, precipitation and its 24 h
sum, solar radiation; upwind fire counts and FRP in a 3-sector arc around the
wind-from bearing, in three distance rings (0–100, 100–400, 400–1200 km),
summed over 1 and 3 days and lagged one day so an afternoon VIIRS overpass can
never inform that morning's forecast — with volcanoes, flares and other static
heat masked out of *both* FIRMS products, not just the one that labels them
(decision 7b); and local-time diurnal / seasonal terms.

Uncertainty is quantile regression (10th/90th), not a residual assumption, so
the band is allowed to be asymmetric — which for pollution it always is.

Evaluation is a **single time-based split**, placed at the 75th percentile of
*observed hours* rather than of the calendar. Splitting the calendar would have
handed the test set whichever months the sensor network happened to be dead in
— a lottery, not a held-out sample. Nothing after the cut is seen in training.
Every published number comes from the held-out future, against two baselines:
persistence (carry the last observation forward) and diurnal climatology fitted
on the training period only.

Per horizon, features that are entirely missing or constant in that horizon's
training slice are dropped and logged; station identity enters as a declared
categorical, never as its 7-digit registry number.

### E5 · Gates (fixed before the first model run)

| Gate | Threshold |
|---|---|
| **G-E1** 24 h skill | model MAE beats persistence by **≥ 15%** |
| **G-E2** skill everywhere | model beats persistence (skill > 0) at **every** horizon |
| **G-E3** episode recall | at 24 h, recall **≥ 0.50** at precision **≥ 0.40** for hours with observed PM2.5 ≥ 55.5 µg/m³ (US EPA "Unhealthy"); reported *insufficient* below 30 episode hours |
| **G-E4** network coverage | **≥ 3** Jabodetabek stations at **≥ 80%** hourly completeness over the trailing 90 days |
| **G-E5** uncertainty | 80% prediction interval covers **72–88%** of observations |
| **G-E6** physical drivers | the 24 h model's top-8 permutation importances include ≥ 1 mixing term (BLH / ventilation) **and** ≥ 1 wind term |

G-E4 is expected to fail and is published red, not softened. See below.

### E5b · Results — rerun 2026-08-30 after the static-source mask fix

Trained on 122,644 station-hours (72,171 observed, 10 stations); tested on the
36,538 rows after the cut at **2025-04-07** (24,061 observed, 8 stations). The
split, the feature count (49) and the row counts are unchanged from the first
run — the only thing that moved is the fire feature block, now built from a
hotspot series with volcanoes and flares removed from *both* products
(decision 7b). Numbers in brackets are the pre-fix run, kept for comparison.

| Horizon | Model MAE | Persistence MAE | Skill (MAE) | Skill (RMSE) | PI80 coverage |
|---|---|---|---|---|---|
| 1 h | 7.57 [7.58] | 7.91 | +4.4% [+4.1%] | +5.6% | 74% |
| 3 h | 12.44 [12.45] | 14.39 | +13.6% [+13.5%] | +13.4% | 68% |
| 6 h | 14.66 [14.62] | 18.67 | +21.5% [+21.7%] | +21.7% | 65% |
| 12 h | 15.46 [15.48] | 22.45 | +31.1% [+31.1%] | +30.8% | 63% |
| **24 h** | **16.04** [15.99] | **19.49** | **+17.7%** [+17.9%] | **+20.8%** | 63% |
| 48 h | 16.73 [16.76] | 21.01 | +20.4% [+20.2%] | +22.9% | 60% |
| 72 h | 17.56 [17.38] | 21.32 | +17.6% [+18.5%] | +21.1% | 57% |

**2 of 6 gates pass** — the same two as before the fix. No gate changed state,
and nothing was tuned to keep one green.

- **G-E1 PASS** — 24 h MAE 16.04 vs persistence 19.49 µg/m³, +17.7% (threshold
  +15%). +20.8% on RMSE. Was +17.9% before the fix: cleaning the fire series
  cost 0.2 points of headline skill, which is the price of the number being
  true.
- **G-E2 PASS** — positive skill at all seven horizons; weakest is +4.4% at 1 h,
  where persistence is naturally hardest to beat.
- **G-E3 FAIL** — episode recall 0.489 against a 0.500 threshold, at precision
  0.534, over 6,084 episode hours. Persistence gets 0.476 / 0.475, so the model
  is better but not by the margin the gate demanded. It failed at 0.499 before
  the fix and fails at 0.489 after; the threshold was fixed in advance and has
  not been moved either time.
- **G-E4 FAIL** — 0 stations reach 80% completeness over the trailing 90 days.
  Expected; it is the case's central finding, not an accident. Untouched by the
  fire fix.
- **G-E5 FAIL** — the 80% prediction interval covers 62.8%, not 72–88%. The
  quantile models are over-confident out of sample. Honest reading: the point
  forecast is usable, the published interval is too narrow and should not be
  relied on for planning until it is recalibrated.
- **G-E6 FAIL, and it now fails on both clauses.** The top-8 permutation
  importances at 24 h are `station_idx`, `pm25`, `doy_cos`, `pm25_lag24`,
  `pm25_roll6`, `pm25_lag48`, `fire_region_3d`, `doy` — no boundary-layer or
  ventilation term, and no wind term either. Before the fix `wind_from_sin` sat
  8th; the de-contaminated fire signal displaced it, entering the top-8 at 7th
  where it had not appeared at all. So the fix made fire a genuinely stronger
  driver and simultaneously pushed the gate further from passing. Both halves
  are published. The model still leans hardest on station identity and the
  sensor's own recent history — a real result about a sparse, heterogeneous
  network, and the dashboard's chapter 03 headline is generated from it rather
  than asserted ahead of it.

**What this adds up to.** The forecast beats persistence at every horizon a client
would ask about, and it is honest about the two places it is not yet trustworthy:
the width of its error bars, and its ability to call an episode before it happens.

### E5c · What the adversarial review changed

`pipeline/replicate.py` re-derives the headline on the *same scored rows* — no
model was refitted — against five further trivial baselines, with a day-block
bootstrap. `pipeline/article.py` folds that and `summary.json` into
`web/src/data/article.json`, which the review page at **`/airquality/article`**
reads. `make review` runs both. Findings that changed what the dashboard says:

1. **Persistence is not the strongest trivial baseline at 24 h.** A trailing
   24-hour mean reaches MAE 18.15 against persistence's 19.49, and against it the
   model scores **+11.6% [+9.4, +13.7]** — an interval lying entirely below
   G-E1's registered 15% threshold. G-E1 stays recorded as passed against the
   baseline it was written against, because thresholds are not moved after the
   fact; the fairer comparison is now published next to it on the dashboard.
2. **The skill curve is largely the baseline's diurnal phase.** Hourly PM2.5
   autocorrelation troughs at 0.27 (12 h) and rebounds to 0.44 (24 h), so the
   "+31.1% at 12 h" peak marks where persistence is worst, not where the model is
   best. Against the best trivial rule at each lead, skill is flat near 11–12%
   from 6 to 24 h.
3. **The held-out panel dissolves.** 93.8% of the 23,232 scored 24 h hours fall
   before 2026; the panel goes 6 stations → 1; no station spans the window. Four
   stations that have all since stopped reporting carry 84.6% of the evaluation.
   One station (Bogor Selatan) has **zero** training hours yet 18.6% of the
   evaluation — an accidental unseen-station test, and it scores +5.6% against
   +21.3% for stations the model trained on.
4. **The forecast cannot make the calls it exists for.** 247 held-out hours
   reached "very unhealthy" (≥125.5 µg/m³) and 20 reached "hazardous"; the model
   predicted **zero** of either. Its highest 24 h prediction anywhere is
   99.1 µg/m³ against an observed maximum of 338.
5. **The error floor is the instrument.** Two OpenAQ registrations 43 m apart
   agree to MAE 6.85 µg/m³ over 7,297 shared hours — against the model's 7.57 at
   a *one-hour* lead. Consumer-grade stations read 39% above the two
   reference-grade ones (52.8 vs 38.0 µg/m³); the reference subset matches
   published Jakarta means, the pooled panel mean does not.
6. **The network finding is the result, not the caveat.** 24 registrations are
   16 distinct addresses and 11 instruments that ever reported; 13 never
   returned an hour. The two co-located instruments diverged to 9.9 vs
   63.0 µg/m³ before one went silent — so the surviving sensor is unverifiable,
   and the case now says so.

Applied to the dashboard: the hero no longer calls persistence "the only baseline
that matters"; chapter 01 reports exceedance on a daily basis (94.3% of complete
days) rather than an hourly one against a 24-hour guideline, and states the
reference-grade subset mean; chapter 02 publishes the fairer baseline and its
interval; chapter 05 publishes the honest denominators and the co-location
divergence; chapter 06 names the backtest station and its death date, and adds
the AQI-ceiling failure the gates were never written to catch.

**What the fix changed in the data.** The static-source mask holds 3,908 cells
(708 flagged directly, the rest buffer) and removes **5.94%** of otherwise-
qualifying NRT detections against 3.63% of archive ones. The cleanest read is
April 2026, the one month the seam falls inside: on the SP side 34.1% of
detections are static (30.2% caught by the label, 3.9% more by the mask); on
the NRT side 35.8%, all of it caught by the mask, where before the fix it was
**0.0%**. That ~34-point discontinuity inside a single calendar month was the
bug, and it is now 1.7 points. In the 60 days after the seam the mask removes
33.1% of detections against 3.9% in the 60 days before — May and June are the
low fire season, so before the fix roughly a third of the "fires" the model saw
in the recent tail were Semeru, Merapi, Sarawak gas flares and offshore
platforms.

### E6 · Dashboard anatomy

Hero (the 72 h forecast unrolling as a breathing haze band with a widening
uncertainty cone) → 01 the air today → 02 the forecast → 03 what drives it →
04 the airshed and the fire season → 05 the one-live-sensor finding →
06 validation against persistence → 07 the review, with a door to
`/airquality/article` → methodology footer.

No WebGL anywhere: every canvas is 2-D with its own hit-testing. This is a
deliberate choice, not a fallback — see Decisions.

---

## Operations

Everything heavy runs on the dev server (`~/demo-lab/cases/air-quality`), never
on a laptop. Units are transient `systemd-run` jobs; check any of them with
`journalctl -u <unit> -f`.

| Unit | What it does | Resume |
|---|---|---|
| `aq-ground` | OpenAQ inventory + hourly pull | `uv run python pipeline/ingest_ground.py` |
| `aq-fire` | FIRMS hotspots → sector-day aggregates | `uv run python pipeline/ingest_firms.py` |
| `aq-firemask` | static-source mask from the SP archive (built once, then cached) | `uv run python pipeline/ingest_firms.py --mask-only` |
| `aq-era5-0/1/2` | ERA5 backfill, 3 shards holding places in the CDS queue | `uv run python pipeline/ingest_era5.py --shard N --nshards 3` |
| `aq-finish` | waits for the shards, gap-fills, then features → model → validate → export | `bash pipeline/finish.sh` (or `make finish`) |

Sharding exists because CDS queues each request server-side: a single month can
sit "accepted" for several minutes before it even starts running, so one worker
spends most of its life idle. Three shards are three idle sockets, not three
busy CPUs. Rerunning any stage is safe — completed work is skipped.

Dashboard service: `demo-airquality.service` → `astro dev --port 4328 --host
0.0.0.0`, enabled at boot. It serves `web/public/data/*.json` directly, so a
pipeline rerun refreshes the page without a rebuild.

Resource discipline: each stage checks free disk and exits 0 below 10 GB; the
whole case's `data/` footprint is about 30 MB because raw ERA5 NetCDF is deleted
after aggregation and raw FIRMS hotspots are reduced in memory and never
written.

---

## Decisions pending user verification

1. **Ground truth is OpenAQ after all.** The brief said the user had no
   OpenAQ key; `OPENAQ_API_KEY` was in fact present in the repo `.env` and
   verified working against `/v3/locations` and `/v3/sensors/{id}/hours`.
   No new registration was needed and none was made. WAQI/aqicn's `demo`
   token was probed and is hard-wired to Shanghai; AirNow and the Jakarta
   Satu portal both need registrations the user does not have. **The user
   needs no further key for this case.**

2. **The network is one live sensor, and that is published as a finding.**
   24 PM2.5 stations exist in the Jabodetabek bbox; 2 have reported in the
   last 14 days, 15 are stale, 7 have never returned a reading. The forecast
   is therefore scoped to the live station and its history; no spatial
   interpolation is claimed. G-E4 is defined against this reality and allowed
   to fail visibly.

3. **Window starts 2023-01-01**, not 2–3 years back from an arbitrary date —
   chosen to match where the ground record actually becomes usable.

4. **No WebGL, by design.** Rather than shipping MapLibre plus a 2-D fallback,
   every visual on this page is a bespoke 2-D canvas or SVG with its own hit
   testing. The user's browser has WebGL disabled, so the fallback would have
   been the real product; building one thing well beats building two, and the
   airshed rose and station map are not basemap-shaped anyway.

5. **`VIIRS_SNPP_SP` + `VIIRS_SNPP_NRT`,** with the seam read live from the
   FIRMS `data_availability` endpoint rather than hard-coded, so it advances
   on its own. FIRMS caps archive windows at 5 days (`Expects [1..5]`),
   not the 10 the docs imply.

6. **ERA5 latency is a stated limit, not a hidden one.** ERA5(T) publishes
   with roughly 5 days' delay, so the operational issue time is bounded by
   meteorology, not by the sensor. The page states both timestamps.

7. **Two source quirks worth knowing before anyone reuses this code.**
   (a) The new CDS answers a multi-variable ERA5 request with **two** NetCDFs
   split by `stepType` — instantaneous fields in one, accumulations (total
   precipitation, surface solar radiation) in the other. Concatenating them
   leaves every variable 50% NaN and silently loses the accumulations at the
   first de-duplication; they must be joined on `(time, lat, lon)`. Caught here
   only because the model reported precipitation as an empty feature.
   (b) **FIRMS `type` exists only in the archive, and filtering on it alone is
   a bug.** Hotspots need to be separated from static heat before they mean
   "fire": Indonesia has ~130 active volcanoes plus substantial gas flaring,
   and VIIRS detects all of it. VIIRS does label them — `type` 1 volcano,
   2 other static land source, 3 offshore — but **no NRT product emits the
   column at all** (verified against the live API: `VIIRS_SNPP_SP` returns
   `type`, `VIIRS_SNPP_NRT` does not). A `type == 0` filter written as
   "if the column exists" therefore cleans 2023→2026-04 and leaves
   2026-04→today dirty, which puts a **false step change at the SP/NRT seam**
   in a series the model consumes as a feature.

   The fix is a **static-source mask**, built by `build_static_mask()` and
   cached in `data/firms_static_mask.parquet`. Static sources do not move, so
   their locations are learned where FIRMS does label them: one 5-day window
   per calendar month across the *whole* SP archive (2012-01-20 → the live
   seam, 172 samples) is swept for `type ∈ {1,2,3}`, gridded to 0.01°
   (~1.1 km; a VIIRS pixel is 375 m at nadir and ~750 m at scan edge), and a
   cell is kept when it appears in **≥ 2 distinct sampled months** — one-off
   mislabels do not earn a permanent exclusion. Each kept cell is buffered by
   its 8 neighbours, because the same flare lands in an adjacent cell often
   enough that an unbuffered mask leaks it back in. The mask is then applied
   to **every row of both products**, in addition to the `type == 0` filter
   wherever the column does exist.

   Per-window filter counts are written to `data/fire_filter_audit.parquet`
   and summarised into `stats.json` → `fire_filter`, so the share removed from
   each product is published on the dashboard rather than asserted. The
   samples are cached per window (`data/static_mask_parts/`), so a normal run
   never refetches the mask; `make firemask` builds it alone, and the ingest
   refuses to reduce real windows against a half-built mask.

   Because the reduction changed, the aggregate cache is versioned
   (`data/fire_parts_v2/`): aggregates computed under the pre-mask rule are
   not comparable and are never mixed back in.

8. **Sentinel-5P TROPOMI is not ingested.** Every genuinely open route
   (Copernicus Data Space, AWS open data, NASA GES DISC subsetting) costs more
   build time than the remaining budget allowed, and the ground record — one
   station — cannot validate a column product anyway. Listed as pending rather
   than faked.

## Attribution

Contains modified Copernicus Climate Change Service information (ERA5, 2026);
neither the European Commission nor ECMWF is responsible for any use of the
Copernicus information. Fire data from NASA FIRMS (VIIRS S-NPP), courtesy of
NASA/GSFC LANCE and the University of Maryland. Ground measurements from
OpenAQ and their originating providers (Clarity, AirGradient, US Department of
State AirNow, Vital Strategies).
