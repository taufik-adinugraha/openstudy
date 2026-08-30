# Case J — Fire & Haze Early Warning (Phase 3)

Where fire starts, and where the smoke goes. An ignition-risk surface per 0.25° cell per day at
1–7 day lead, scored against the operational Canadian Fire Weather Index rather than against a
coin flip; and a trajectory model on ERA5 winds, released at CAMS GFAS injection heights, that
names the receptors downwind — Sumatra, Kalimantan, and across the Strait, Singapore. The 2015
and 2019 crises are the anchors and are **held out of training entirely**.

Spec (governing document): `docs/spec-fire-haze.html`.
Dashboard: port **4334**, base **`/haze`** (`demo-haze.service`, not yet deployed).

Status: **SPEC'D + SCAFFOLDED (2026-08-30).** Sources scouted live and pinned; pipeline stubs
carry the method contract; nothing has been run. `make rebuild` is the entry point.

## Run

```sh
uv sync
make rebuild     # fires → static → indices → era5 → cams → ground → features → risk → transport → validate → export
make refresh     # daily: NRT hotspots → risk → transport → export (idempotent by acquisition date)
```

Uses `FIRMS_MAP_KEY`, `CDS_API_KEY`, `OPENAQ_API_KEY` and `GFW_API_KEY` from the repo-root `.env`.
**No new registration is needed anywhere in this case.** One ECMWF personal access token
authenticates against all three Copernicus stores — CDS, ADS and EWDS. What *is* outstanding is
**three one-time browser policy clicks**, and they are the only thing standing between the
scaffold and a full run:

| Store | What to click | Symptom if you don't |
|---|---|---|
| ADS (CAMS) | Data-protection statement + ADS terms of use | HTTP **403** `user didn't accept all required site policies` |
| EWDS (CEMS fire) | CEMS Early Warning Data Store terms (rev. 11) | same 403 |
| Earthdata GES DISC | EULA at the `resolution_url` | 403 `EULA Acceptance Failure` (only if the S5P GES DISC route is ever used) |

A 403 there is **not** a dead key — anonymous requests return 401, which is how we know the token
is doing its job. Same shape as the Black Marble licence note in `.env.example`.

ERA5 is the long pole: CDS queues server-side, so `ingest_era5.py` shards by year and
`pipeline/finish.sh` drives the rest of the DAG once the shards drain (the Case E pattern,
including the single-threaded gap-filling pass).

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

1. **`[data-case="haze"]` token block** — accent `#E2569E`, `--fire #FF7A45`, `--clean #7FB2C9`,
   ground `#12100F`. Needs adding to `shared/design/tokens.css` (this agent was scoped out of that
   file). The rationale above is the argument; the alternative — a fourth orange — is worse.
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

## Resource behaviour

Every download loop checks free disk before the next chunk and exits 0 (resumable) below 10 GB.
`data/manifest.json` is the ledger. Rasters are window-read and reduced to the 0.25° model grid
immediately, and full-resolution tiles are deleted after aggregation, so standing disk stays near
the aggregate size rather than the archive size. Measured annual ERA5 volumes over the AOI: single
levels 1.05 GB, pressure levels 1.66 GB, ERA5-Land 1.42 GB. FIRMS bulk for Indonesia 2012–2024 is
~250 MB. Peak RSS target is under 2 GB so each stage fits a `MemoryMax=3G` systemd transient unit
alongside the other jobs on the shared box.
