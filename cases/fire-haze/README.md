# Case J — Fire & Haze Early Warning (Phase 3)

Where fire starts, and where the smoke goes. An ignition-risk surface per 0.25° cell per day at
1–7 day lead, scored against the operational Canadian Fire Weather Index rather than against a
coin flip; and a trajectory model on ERA5 winds, released at CAMS GFAS injection heights, that
names the receptors downwind — Sumatra, Kalimantan, and across the Strait, Singapore. The 2015
and 2019 crises are the anchors and are **held out of training entirely**.

Spec (governing document): `docs/spec-fire-haze.html`.
Dashboard: port **4334**, base **`/haze`** (`demo-haze.service`, not yet deployed).

Status: **BUILT (2026-08-30).** Every stage is implemented and has run against live sources.
The ERA5 backfill is **queue-bound, not code-bound** — see "The one thing that is slow" below —
so the record length grows every time `make era5` is re-run and every downstream stage rebuilds
from whatever has landed. `make rebuild` is the entry point.

## Run

```sh
uv sync
make rebuild     # fires → static → indices → era5 → cams → ground → features → risk → transport → validate → export
make refresh     # daily: NRT hotspots → risk → transport → export (idempotent by acquisition date)
```

Uses `FIRMS_MAP_KEY`, `CDS_API_KEY` and `OPENAQ_API_KEY` from the repo-root `.env`.
**No new registration is needed anywhere in this case**, and one ECMWF personal access token
authenticates against all three Copernicus stores — CDS, ADS and EWDS.

### Policy acceptances — status at build time

| Store | Status | Verified how |
|---|---|---|
| CDS (ERA5) | **accepted** | jobs run |
| ADS (CAMS forecasts, EAC4, GFAS) | **accepted 2026-08-30, mid-build** | real submissions to all three collections returned `201 accepted` with live job ids |
| EWDS (CEMS fire indices) | **accepted 2026-08-30, mid-build** | `cems-fire-historical-v1` returned `201 accepted` |
| Earthdata GES DISC | **not needed** | the S5P GES DISC route was rejected on licence and volume grounds before it was reached; nothing in this build touches it |

Early in the build both ADS and EWDS returned
`403 · user didn't accept all required site policies`, naming
`https://ads.atmosphere.copernicus.eu/licences/terms-of-use-ads`,
`https://ads.atmosphere.copernicus.eu/licences/ads-data-protection-privacy-statement` and
`https://ewds.climate.copernicus.eu/licences/terms-of-use-cems`. Those were accepted during the
build and the CEMS-FWI baseline and the CAMS/GFAS layers are now live rather than pending. The
policy branch is **kept in the code** — `util.Cads.policy_blocked` still detects it and every
stage degrades to a named PENDING rather than dying — because the acceptance is per account and
this pipeline has to build on a fresh one.

**A caveat worth carrying:** EWDS's `/profiles/v1/account/licences` lists only `cc-by` and still
accepts these submissions, so that listing is **not authoritative**. The only reliable test is a
real submit, which is what `pipeline/ingest_indices.py --fwi-only` does.

### The one thing that is slow

ERA5 is the long pole, and the reason is measured rather than assumed. CDS refuses multi-year
requests — `cost limits exceeded / Your request is too large` — at a ceiling between **16,368 and
17,856 fields**, and **the cost is computed before the `area` subset is applied**, so asking for a
small box buys nothing. One year per request is the ceiling. It also caps *queued requests per
dataset* at about two, and `single-levels` serves both the state variables and precipitation. So
the backfill is a 45-job serial queue at roughly ten minutes a job, not a parallel pull.

Everything downstream is built for that: `ingest_era5.py` records job ids in
`data/cads_jobs.json` so a restart never loses a queue position, requests are ordered
**sl → pl → tp, anchors first, then most-recent-first**, and `features.py` globs
`era5_parts/{sl,tp}_*.parquet` rather than requiring a consolidated file. The case therefore
*runs* on a partial drain and simply reports a shorter record; re-running `make era5` then
`make features risk transport validate export` lengthens it.

## Six premises that did not survive reconnaissance

Recorded here because each one changes the build, and because the third is a live bug in another
case.

1. **The FIRMS `area` window caps at five days, not ten** (`Expects [1..5]`).
2. **VIIRS covers 2015.** `VIIRS_SNPP_SP` starts 2012-01-20, so both anchors use VIIRS and no
   MODIS splice is needed for them. Measured on the Sumatra box for 2015-10-20: **VIIRS 6,110
   detections against MODIS 1,354** — a factor of 4.5.
3. **The `type` field is absent from every NRT product.** NASA states it: *"data distributed via
   the FIRMS download tool does not attribute the static sources/inferred hotspot 'type'"*. So a
   `type == 0` filter **silently no-ops on the live tail** — the recent series keeps volcanoes and
   gas flares that the historical series drops, and the SP/NRT seam becomes a step change that
   reads as a trend. **Case E's `cases/air-quality/pipeline/ingest_firms.py` has this bug today**
   (the filter is guarded by `if "type" in raw.columns`, which is simply false for NRT). This case
   uses `type` only to *build* a static exclusion mask from the SP archive, and applies **the
   mask** to every product. Worth back-porting to Case E.
4. **`cems-fire-historical-v1` is not on CDS** (404). It is on **EWDS**, a third Copernicus store.
5. **ADS and EWDS need no new registration** — see the table above.
6. **The ground-truth problem is not thin Jakarta coverage.** OpenAQ has **zero PM2.5 locations in
   Riau and zero in all of Kalimantan** — bbox *and* 25 km radius searches around Pekanbaru,
   Palangkaraya and Banjarbaru all return `found: 0`. The fire belt is unmonitored.

## Non-negotiables from the spec

- **The hotspot filter is load-bearing, not hygiene.** Static exclusion mask (OSM volcano nodes
  buffered 5 km, plus persistent sources found empirically from the data), applied to every
  product, plus a low-confidence drop. Measured composition at the 2019 peak: `type != 0` removes
  0.89 %, low confidence a further 5.7 % — and **only 3.2 % of detections are high-confidence, so
  filtering to high confidence would discard 97 % of the signal**. Gate G-J1 publishes what was
  removed and **fails if almost nothing was**.
- **Never concatenate MODIS bulk with MODIS API.** The bulk country files are Collection 6.1
  (`version` `6.2`); the API's `MODIS_SP` returns Collection 6.0 (`version` `6.03`). Same sensor,
  same day, different row counts. VIIRS is consistent across both routes.
- **The baseline is the FWI, not zero.** Fire is rare and seasonal, so raw AUC flatters everyone.
  G-J2 is a Brier skill score against per-cell day-of-year climatology *and* against the CEMS
  Canadian Fire Weather Index — an external, operational index we did not design and cannot tune,
  costing ~29 MB/year over our box. Beating climatology is table stakes.
- **This is transport, not chemistry.** No CTM of our own. But CAMS publishes a real one covering
  both anchor years, so the trajectory result is shown **next to** it — where we agree, the cheap
  model is working; where we diverge, the divergence is the finding.
- **Forecast means forecast.** Every feature is lagged to t−1, and the operational path is driven
  by **CHIRPS-GEFS** forecast precipitation (0–15 day leads, issued same-day, public domain)
  rather than by reanalysis. Both paths are scored, and the gap between them is published as the
  real cost of forecasting rather than hindcasting.
- **Receptors are reported individually, including the failures**, and each carries its tier.

## Signature interaction — "Blame the wind"

Pick a receptor city and a bad-air day; 72-hour back-trajectories fan out across the archipelago
and land on the fires that were burning where the air came from, province attribution resolving
beside them. One control flips the dial to forward mode and the same engine runs from today's
hotspots to tomorrow's downwind cities. Forward and backward share the integrator — which is also
exactly what gate G-J3 tests. Fully interactive in 2-D canvas; **no WebGL anywhere.**

## Colour identity

Plume magenta on ash — `[data-case="haze"]`, block proposed for `shared/design/tokens.css` and
carried locally in `web/src/styles/tokens.css` until it lands there.

Orange is triple-booked in this portfolio (trade copper `#D9722C`, transit red-orange `#E4562E`,
forest loss `#B4552A`); a fourth orange case would be indistinguishable in the homepage grid.
Magenta is also the *honest* choice rather than a designer's whim: satellite aerosol products —
TROPOMI UV Aerosol Index, MODIS AOD — render dense smoke at the magenta end, so the plume colour
is borrowed from the instrument. Ignition points keep a hot vermilion (`--fire`) used **only** for
point detections, never for UI.

## Decisions pending user verification

### Made during the build — these are the ones to check

**B1 · Attribution is published at PROVINCE level.** This is the biggest editorial call in the
case and the spec left it open. The alternative was island level ("Sumatra", "Kalimantan"), which
is safer and useless: nobody allocates a suppression aircraft, prices a concession, or schedules
an air-handling change on "Sumatra". The commercial premise of this case is that the answer is
actionable, and at island level there is no answer. Three things bound it on the page:
a province share is stated as **where the air came from**, never as who lit the fire; every share
carries the trajectory ensemble's spread; and **"no attributable source" is a first-class
outcome**, printed whenever a 72-hour back-trajectory passes over no detected fire — which is the
correct answer on the many bad-air days that are local rather than transboundary. If you want
this softened, the one-line change is in `export_web.py` (`attribution_granularity`) plus the
`attribute()` grouping key in `transport.py`.

**B2 · The forecast path is defined by INFORMATION SET, not by product.** The spec names
CHIRPS-GEFS as the days-ahead driver. There is no open GEFS reforecast archive covering
2012–2024, so a model *trained* on forecast fields is not buildable from open data, and
pretending otherwise would be the exact train/serve skew the spec warns about elsewhere. So:
the **forecast path** sees only features from days ≤ t and must carry the weather forward itself;
the **reanalysis path** additionally sees the weather that actually happened over the lead window
and is an explicit upper bound. The gap between them is published as the cost of forecasting
rather than hindcasting. CHIRPS-GEFS **is** still ingested (16 leads, same-day, verified live) and
drives the live refresh panel, labelled as what it is.

**B3 · ERA5 is sampled, and where it is sampled is a modelling decision.** Fifteen years of
full-hourly ERA5 over the AOI is ~58 GB against a 31 GB shared disk. The cuts taken:
- four synoptic hours a day for the **state** variables (00 and 06 UTC are 07:00 and 13:00 WIB —
  the humidity maximum and near peak fire danger, so daily max-T and min-RH are sampled at the
  right end of the diurnal cycle rather than averaged away);
- **precipitation requested on its own at all 24 hours**, because `total_precipitation` is an
  hourly accumulation and summing 4 of 24 would report one sixth of the rain — a silent dry bias
  precisely where an ignition model is most sensitive. One variable at full resolution costs
  0.081 GB/year, which is a rounding error;
- **ERA5-Land dropped entirely.** It is 0.1° against a 0.25° model grid, so nine of its cells are
  averaged into one model cell before the model sees them; the resolution is spent and 1.42
  GB/year is not. Soil water layers 1–3 on single levels carry the same physics on the grid we
  actually model;
- pressure levels restricted to `ERA5_PL_MONTHS` (Feb–Mar and Jun–Nov), because trajectories are
  only ever run in the burning months.

**B4 · The steering field is stored as a dense array, not a row per record.** Fifteen years of
6-hourly winds on three levels is 255 M rows ≈ 5 GB in parquet, most of it the key repeated 255
million times. As a `(time, level, lat, lon)` int16 array it is ~75 MB a year — and it is the
shape the integrator wants anyway. This is what makes the transport stage fit on the box.

**B5 · Every AOI country's bulk file is fetched, not just Indonesia's.** The bulk route is per
country and the `area` route is per bounding box, so an Indonesia-only history plus an AOI-wide
live tail would put a step change at the 2025 seam that looks exactly like a Malaysian fire season
starting. Indonesia + Malaysia + Singapore are pulled and all three are clipped to the same box.
*(This is the same class of bug as the NRT `type` problem, found the same way.)*

**B6 · CHIRPS supplies a 46-year SPI base period, not the daily rainfall.** The spec's
`prelim/global_daily/fixed/` is a CHIRPS-**2.0** path and 404s under v3.0; the v3 daily tree is
`daily/{final,prelim}/{sat,rnl}/`, and only `sat` has a prelim stream. Rather than splice daily
GeoTIFFs, CHIRPS v3 **monthly** globals (1981 → 2026-07, HTTP-Range read to the AOI band) give a
proper 46-year base for SPI-1/3/6 — the difference between "SPI-3" and "a 14-year z-score wearing
SPI's name" — and ERA5 supplies the daily rain. SPI is computed here (gamma per cell per calendar
month with a zero point mass) so the identical code can run on a forecast.

**B7 · G-J3's bearing check is a bearing check, and it may ship red.** A first pass over a single
year with no GFAS heights gave **60.1 % agreement within ±30°, median difference 21.5°** — below
the 70 % threshold. The threshold is not moving. Re-running with GFAS injection heights is the
honest next step, and if it still fails it ships failing with that number and a diagnosis, per
Case C's G-C5 precedent.

**B8 · Chart colours were validated, and one pair carries a documented WARN.** Against the dark
surface, `model / climatology / persistence / CEMS FWI` (`#E2569E / #E0A63F / #6B7CA6 / #4BB8A9`)
passes CVD separation and the normal-vision floor. The `observed / modelled` pair
(`#7FB2C9 / #E2569E`) sits at ΔE 7.7 under deuteranopia — inside the 6–8 band, which is legal
**only with secondary encoding** — so those two series carry direct labels *and* different mark
geometry (solid vs dashed). `--fire` is never used for a series, only for point detections, so the
house rule that "a red dot means something burned here" holds everywhere.

**B9 · OpenStreetMap's volcano nodes were unavailable on the first run.** All four Overpass
mirrors refused (406 / 502 / 500 / 504), so the geometric half of the static mask did not run and
the mask was carried entirely by the empirical persistent-source detector — which is the half the
spec says is actually relied on. The code now tries `GET ?data=` before `POST`, and the mask
rebuilds with the volcano buffer whenever a mirror answers. The composition is published either
way, so the page always says which half of the filter ran.

### From the spec, still open

1. **`[data-case="haze"]` token block** — accent `#E2569E`, `--fire #FF7A45`, `--clean #7FB2C9`,
   ground `#12100F`. **This has already landed in `shared/design/tokens.css`** and is copied
   verbatim into `web/src/styles/tokens.css`; no action needed beyond confirming you are happy
   with it.
2. **Three-tier ground truth, and calling tier 3 a model.** Singapore NEA is the only long, clean,
   commercially-licensed instrument record in the region (hourly, five regions, **history starts
   ~2016-03** — verified: 2015-10-20 returns zero items, 2013 returns HTTP 500). Indonesian
   OpenAQ units exist but are recent and few: Palembang (2025-10 →), a Clarity unit **labelled
   "Jakarta" but physically at −0.608/100.755 in West Sumatra** (2023-11 →), USU Medan. For
   Pekanbaru, Palangkaraya and Pontianak — the cities the story is *about* — **no open sensor has
   ever existed**, so CAMS EAC4 reanalysis stands in, flagged as a model on every row and given
   its own colour. **Confirm you are comfortable publishing a model-vs-model comparison, clearly
   labelled, rather than dropping those cities.** Dropping them would be quieter and less useful.
3. **2015 has no Singapore ground truth.** The anchor gate therefore scores 2015 on FIRMS
   detections plus CAMS EAC4, and says so. Confirm that is acceptable for a case whose headline
   event is 2015.
4. **Malaysia is a genuine hole and the spec says so.** `apims.doe.gov.my` returns 404 on every
   path including root; `api.data.gov.my` carries no air-quality dataset; the only aggregator with
   APIMS is AQICN, which forbids commercial use verbatim (*"can not be used in paid applications
   or services"*). **Rejected.** So the transboundary claim is Indonesia → Singapore, not
   Indonesia → Singapore/Malaysia, and the marketing copy must match.
5. **Naming provinces (and therefore, implicitly, actors) in the attribution.** The
   back-trajectory output is a province share vector for a receptor-day — a politically live
   statement in Indonesia and in Singapore–Indonesia relations. Default is province level with the
   trajectory uncertainty shown; you may prefer island level. **The single biggest editorial
   decision in this case.**
6. **Peat depth: PEATGRIDS at 1 km, not the Indonesian 1:50,000 map.** PEATGRIDS
   (`10.5281/zenodo.12559239`) is **CC BY 4.0**, peer-reviewed, 57.5 MB, and licence-clean. BIG's
   Satu Peta service carries KLHK's *Peta Fungsi Ekosistem Gambut* at 1:50,000 with a real
   `peat_thick` field in 0.5 m bins (**15,503 polygons in our box**) — far better data, but
   **"License not specified"**. So it is a validation and view-time overlay only, never stored.
   Same call as Case H's concessions. *Honest summary: no open, commercially-licensed, national,
   high-resolution Indonesian peat-depth raster exists.*
7. **KLHK land cover is used with attribution and a legal review.** `PL_AR_250K` (Penutupan Lahan
   2024, 442,816 polygons) is reachable — note the path is `/server/rest/services`, not
   `/arcgis/...`, and the class is the integer `PL2024_ID` because `NAMAOBJ`/`FCODE` are literally
   `"-"`. It also carries `PL2023_ID_R` in the same row, giving a **free, methodologically
   consistent one-year change flag that WorldCover cannot provide**. Stated use limit: not as
   reference finer than 1:250,000. No open licence granted. The licence-clean alternative is ESA
   WorldCover v200 (CC BY 4.0, anonymous S3, 38 tiles, 0.76 GiB) — used as the primary, with KLHK
   as the enrichment pending sign-off. *Do not diff WorldCover v100 against v200: different
   algorithm versions, so the difference is method, not land cover.*
8. **The Sentinel-5P UV Aerosol Index has a licence conflict and is held back until resolved.**
   DLR EOC's L3 daily product is technically ideal — anonymous STAC, anonymous COGs, HTTP 206
   range reads, and our whole AOI inside **one 512×512 tile at 0.97 MB/day** (≈1.2 GB for the
   entire 2018–2026 archive, same-day latency, no L2 swath handling). But the collection's
   `license` string reads `CC-BY-4.0` while the URL in that same field *and* the `rel:license`
   link both point at **CC BY-NC 4.0** — and four of DLR's 73 collections carry the NC link while
   siblings are clean, so it is not a global typo. **Written confirmation from DLR EOC is needed
   before this layer is published.** CO has no DLR product; `s3://meeo-s5p` is anonymous and
   unambiguous (plain Copernicus notice) if CO is wanted.
9. **A daily `make refresh` cron vs D3's static-snapshot rule** for non-flagship cases. The target
   exists and is idempotent; nothing is scheduled. A live "risk today" panel is the strongest
   version of this demo and the only part with an ongoing operational cost.
10. **MODIS extends base rates to 2001 but is never compared to VIIRS counts.** 1 km vs 375 m: a
   spliced count series would show a step change in 2012 that is an instrument, not a policy. Two
   series, two charts. Costs a chart, buys the 1997/2001-era context honestly.
11. **The Smithsonian Global Volcanism Program is Cloudflare-gated** from the dev network
   (verified: `volcano.si.edu` returns a challenge page). The geometric volcano filter therefore
   uses OpenStreetMap `natural=volcano` via Overpass — **295 nodes verified live in the Indonesian
   bbox, ODbL** — which over-includes extinct cones; that over-exclusion is quantified in G-J1
   rather than hidden. The empirical persistent-source detector needs no external list at all and
   is the filter actually relied on.
12. **The IOD index has no operational route.** The HadISST DMI ends ~3 months back and is stamped
   *"Preliminary"*, so it is a historical feature only — and 2019 is unreadable without it. An
   OISST-based DMI would close the gap; the spec states it rather than papering over it. (Note
   also: use `wksst9120.for` for weekly Niño3.4, **not** `wksst8110.for`, which still resolves but
   froze in January 2021 when the base period changed. Every BoM SOI URL is dead; Long Paddock's
   daily SOI, CC BY 4.0, is the replacement and is better.)
13. **CHIRPS v2 production ends after December 2026** — pinning it pins a product that stops
   updating mid-engagement, so the config builds on v3. The operational tail uses
   `prelim/global_daily/fixed/` (5-day latency; `fixed/` sums exactly to the pentad totals,
   `tifs/` carries residuals), and the model is **trained on prelim too**, or it has train/serve
   skew.

## Homepage card — copy for `site/src/pages/index.astro`

This case is scoped out of `site/`, so the change is described rather than applied. Two edits:

**1. Add the route to the `DEMOS` map** (after the `airquality` line):

```js
  haze: "http://localhost:4334/haze",               // TODO prod: /haze
```

**2. Add the tile.** It follows the `forest` card's shape exactly — `data-case="haze"` picks up
the plume-magenta identity that is already in `shared/design/tokens.css`, so no new CSS is needed
beyond a `.haze-visual` entry alongside `.aq-visual, .forest-visual, .transit-visual` in the
`padding: 0; overflow: hidden` rule.

```html
<a class="case live" data-case="haze" href={DEMOS.haze} target="_blank" rel="noopener">
  <div class="case-visual haze-visual" aria-hidden="true">
    <svg viewBox="0 0 400 240" preserveAspectRatio="xMidYMid slice">
      <defs>
        <linearGradient id="hz-sky" x1="0" y1="0" x2="0.3" y2="1">
          <stop offset="0%" stop-color="#12100F"/><stop offset="60%" stop-color="#1A1715"/>
          <stop offset="100%" stop-color="#241419"/>
        </linearGradient>
        <linearGradient id="hz-plume" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stop-color="#F6C9DE" stop-opacity="0.85"/>
          <stop offset="100%" stop-color="#E2569E" stop-opacity="0.15"/>
        </linearGradient>
      </defs>
      <rect width="400" height="240" fill="url(#hz-sky)"/>
      <!-- 72-hour back-trajectory fan, converging on a receptor -->
      <g fill="none" stroke="url(#hz-plume)" stroke-width="1.6">
        <path d="M318 78 C262 92 214 120 168 132 C126 143 88 138 40 154"/>
        <path d="M318 78 C266 100 222 132 178 148 C138 163 98 160 46 178"/>
        <path d="M318 78 C258 84 206 104 156 112 C112 119 74 112 28 124"/>
        <path d="M318 78 C270 110 236 148 200 170 C164 192 128 196 84 214"/>
        <path d="M318 78 C254 76 196 88 142 92 C100 95 62 88 18 96"/>
      </g>
      <!-- the fires the parcels land on -->
      <g fill="#FF7A45">
        <circle cx="146" cy="112" r="3.4"/><circle cx="128" cy="120" r="2.6"/>
        <circle cx="168" cy="132" r="4.2"/><circle cx="92"  cy="139" r="2.2"/>
        <circle cx="178" cy="148" r="3.0"/><circle cx="60"  cy="152" r="2.6"/>
        <circle cx="200" cy="170" r="2.4"/><circle cx="112" cy="163" r="3.2"/>
      </g>
      <!-- the receptor -->
      <circle cx="318" cy="78" r="5.2" fill="#7FB2C9"/>
      <circle cx="318" cy="78" r="11" fill="none" stroke="#7FB2C9" stroke-width="1" opacity="0.45"/>
      <text x="18" y="212" fill="#E2569E" opacity="0.95" font-size="12" letter-spacing="1.5"
            font-family="ui-monospace,monospace">72 HOURS UPWIND</text>
    </svg>
  </div>
  <div class="case-body">
    <span class="badge">LIVE DEMO</span>
    <h2>Fire &amp; Haze Early Warning</h2>
    <p>2.68 million VIIRS detections cleaned of the volcanoes and gas flares that make
    Indonesia's hotspot table partly a geology table — a tenth of the live feed, where the
    field everyone filters on does not exist. Then ignition risk days ahead, and
    back-trajectories that name the provinces a bad-air day in Singapore came from.</p>
    <span class="meta">VIIRS · ERA5 · CAMS GFAS · 11.9% FILTERED · INDONESIA → SINGAPORE</span>
  </div>
</a>
```

Alternative one-line pitch if the grid needs something shorter: *"Where fire starts, and where
the smoke goes — an ignition-risk surface days ahead, scored against the operational Canadian
Fire Weather Index, and a trajectory model that says whose land the air was standing on."*

## Resource behaviour

Every download loop checks free disk before the next chunk and exits 0 (resumable) below 10 GB.
`data/manifest.json` is the ledger. Rasters are window-read and reduced to the 0.25° model grid
immediately, and full-resolution tiles are deleted after aggregation, so standing disk stays near
the aggregate size rather than the archive size. Measured annual ERA5 volumes over the AOI: single
levels 1.05 GB, pressure levels 1.66 GB, ERA5-Land 1.42 GB. FIRMS bulk for Indonesia 2012–2024 is
~250 MB. Peak RSS target is under 2 GB so each stage fits a `MemoryMax=3G` systemd transient unit
alongside the other jobs on the shared box.
