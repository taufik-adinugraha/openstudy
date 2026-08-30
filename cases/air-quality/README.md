# Case E — Jabodetabek Air Quality Nowcast

**The operational case.** Every other case in the lab ships a reproducible
snapshot. This one runs: scheduled acquisition → feature build → model →
gates → dashboard, with vintage stamps that move. 24–72 h PM2.5 forecasts for
Greater Jakarta from ERA5 meteorology, NASA FIRMS fire hotspots and OpenAQ
ground sensors.

Identity: `data-case="airquality"` — washed sky cyan `#6FC7D6` on a dark
ground. The AQI colour ramp is **data-only** and never doubles as the accent.

Dashboard: <http://52.77.253.154:4328/airquality> (port 4328, base `/airquality`).

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
        ↓
features.py       lagged PM2.5 + meteorology + upwind fire   → features.parquet
model.py          direct GBM per horizon vs persistence      → model_eval / predictions / forecast
validate.py       gates G-E1…G-E6                            → stats.json
export_web.py     NaN-safe view models                       → web/public/data/*.json
```

Every ingest stage is resumable and idempotent: work is keyed by
sensor-month, ERA5 month, and FIRMS 5-day window, and an artefact that exists
is skipped (the current month / last 3 days are always refetched, because they
are still filling). Raw ERA5 NetCDF is deleted the moment it is aggregated;
raw FIRMS hotspots are reduced to sector-day counts in memory and never hit
disk. Each stage checks free disk first and exits 0 below 10 GB.

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
never inform that morning's forecast; and local-time diurnal / seasonal terms.

Uncertainty is quantile regression (10th/90th), not a residual assumption, so
the band is allowed to be asymmetric — which for pollution it always is.

Evaluation is a **single time-based split** at 75% of the timeline. Every
number published is from the held-out future, against two baselines:
persistence (carry the last observation forward) and diurnal climatology
fitted on the training period only.

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

### E6 · Dashboard anatomy

Hero (the 72 h forecast unrolling as a breathing haze band with a widening
uncertainty cone) → 01 the air today → 02 the forecast → 03 what drives it →
04 the airshed and the fire season → 05 the one-live-sensor finding →
06 validation against persistence → methodology footer.

No WebGL anywhere: every canvas is 2-D with its own hit-testing. This is a
deliberate choice, not a fallback — see Decisions.

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

7. **Sentinel-5P TROPOMI is not ingested.** Every genuinely open route
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
