# Case I — Rice & Food Security (Phase 3)

Java's harvest, seen through cloud. Paddy phenology read out of Sentinel-1 radar backscatter —
the flooding minimum, the tillering rise, the heading peak — turned into planted and harvested
area and a harvest date, and put next to BPS's own KSA figures.

Spec (governing document): `docs/spec-rice-security.html`.
Dashboard: port **4333**, base **`/rice`** (`demo-rice.service`, not yet deployed).

Status: **SPEC'D + SCAFFOLDED (2026-08-30).** Sources scouted and pinned; pipeline stubs carry
the method contract; nothing has been run. `make rebuild` is the entry point.

## Why radar

Rice in Java grows through the monsoon. Optical satellites see cloud for most of the growing
season, which is why every optical rice-area product for this country is either annual, coarse,
or quietly interpolated. Sentinel-1 does not care about cloud, and — better than that — the crop
calendar is written directly into the radar signal:

- a paddy about to be transplanted is **flooded**, and a sheet of water is a specular reflector
  that sends the pulse away from the satellite → a sharp **VV minimum**;
- as the crop tillers, the canopy becomes a volume of vertical scatterers standing in water →
  a steep **VH rise**, several dB over a few weeks, peaking around heading;
- after heading the canopy dries and the field is cut → the signal drops again.

Minimum → steep rise → peak is close to unique to flooded rice. That sequence *is* the method.

## Run

```sh
uv sync
make rebuild     # stats → aux → climate → sar → backscatter → phenology → area → model → validate → export
```

Uses `BPS_API_KEY` and `CDS_API_KEY`/`CDS_API_URL` from the repo-root `.env` (both already work).
The Sentinel-1 access route is selected by `config.S1_ROUTE`; `ingest_sar.py` implements each
route behind one interface so the choice can change without touching anything downstream.

`make stats` runs **first on purpose**: it is cheap, and if BPS KSA cannot be retrieved there is
nothing to benchmark against and the case does not exist. Failing there costs minutes; failing
there after the SAR ingest costs days.

## Non-negotiables from the spec

- **Scope is stated, not implied.** A full-Indonesia S1 VV+VH time series is terabytes per year.
  The reporting scope is Java's three rice-bowl provinces (~half of national production); the
  deep scope is five kabupaten chosen to show both an irrigated multi-crop calendar and a rainfed
  single-crop one; national figures are BPS's, labelled as official statistics, never as our
  measurement.
- **Harvested area is a FLOW, not a stock.** A field yielding three crops contributes its area
  three times. A satellite product that reports paddy *extent* and calls it harvested area will
  disagree with BPS by roughly the cropping intensity — a factor of two — and the satellite will
  get the blame. `paddy_extent_ha`, `harvested_ha` and `planted_ha` are named and shown
  separately, everywhere.
- **The rice mask is a prior, not a label.** It restricts where detection is attempted and cuts
  the SAR volume enormously, but treating it as truth would mean this case just reproduces
  someone else's map. Gate G-I4 *measures* agreement and maps the disagreement.
- **Orbits are never mixed.** Backscatter depends on incidence angle; combining relative orbits
  without normalisation produces a sawtooth that looks exactly like phenology.
- **Average in linear power, then convert to dB.** Averaging decibels is averaging logarithms and
  biases every cell low by an amount that depends on its own variance — a bias that correlates
  with land cover and therefore looks like a real spatial pattern.
- **Yield is not modelled.** Production is reported as *our harvested area × BPS's published
  productivity*, with the arithmetic shown and labelled. Indonesian rice production is a
  politically live number; claiming an independent estimate is the fastest way to lose a
  technical client.
- **Validation is by time.** The final season is held out entirely (G-I5). Random CV over a
  spatio-temporally autocorrelated panel produces a flattering number unrelated to forecasting.

## Signature interaction — "The radar probe"

Drag a probe anywhere over the paddy map. The panel draws **that location's real Sentinel-1 VH
and VV curves** — raw observations as points, the Savitzky-Golay fit over them — with the
detected flooding minima and heading maxima snapping into place live, and the derived planting
and harvest dates dropping onto a calendar strip. Move the probe and the curve morphs.

It does three things at once: it teaches the physics in one gesture, it proves we hold per-pixel
data rather than a downloaded map, and it shows the noise the fit is drawn through — a client who
can see the scatter trusts the line. The hero opens with the probe auto-driving across an
irrigated command, then hands over control. Fully interactive in 2-D canvas/SVG — **no WebGL
anywhere.**

## Colour identity

Flooded paddy seen by radar — `[data-case="rice"]`, block proposed for
`shared/design/tokens.css` and carried locally in `web/src/styles/tokens.css` until it lands.

Accent is a **young-paddy chartreuse `#A8CE3A`** (~78° hue) — the one green-adjacent slot the
other eight cases leave open; forest's canopy green sits at ~133°, far enough that the two never
read as the same case. The phenology ramp is **cyclic**, because a crop calendar is a loop:
flood teal → vegetative green → heading chartreuse → ripening gold → harvest stubble → back to
flood. The ramp is data-only and never doubles as the accent.

## Decisions pending user verification

1. **`[data-case="rice"]` token block** — accent `#A8CE3A`, cyclic `--phen-*` ramp, ground
   `#0A1214`. Needs adding to `shared/design/tokens.css` (this agent was scoped out of that file).
2. **Scope: Java only, five deep kabupaten.** The alternative is a thinner national product that
   cannot be validated at kabupaten level. This is the single biggest scoping call in the case
   and it is the one that keeps it honest — see the size arithmetic in the spec (§I3).
3. **Analysis units: detect at 100 m cells, map at kecamatan, report and validate at kabupaten.**
   Reporting at kecamatan would imply a precision BPS's KSA sample cannot test, and gate G-I1
   would become unfalsifiable. The dashboard draws kecamatan and quotes kabupaten, and says which
   is which.
3b. **BPS rice is NOT on the national domain at regency level — the house gotcha does not apply
   here.** On domain `0000` every rice table's `vervar` group is "38 Provinsi": 38 provinces plus
   `9999 INDONESIA`, no regencies. Kabupaten rice lives only on the **provincial** domains, with a
   different var id and table shape per province. The payoff for finding this: **Jawa Barat
   (domain 3200, var 935) and Jawa Timur (3500, var 578) publish MONTHLY harvested area per
   regency for 2018–2025**, which is a **65-regency × 96-month = 6,240-observation benchmark** and
   is what makes gate G-I2 testable at kabupaten level rather than on four provincial curves.
   Jawa Tengah (3300, var 463) is annual only, and Grobogan is therefore validated annually.
   Verified consistent three ways: kabupaten rows sum exactly to the provincial row, which matches
   the independent national table to the cent.
   Two API features worth using: `th` accepts ranges/lists (max 2 years per call) and `keyword`
   search works on the `var`/`publication`/`pressrelease` models — together they cut the whole
   ground-truth pull to ~65 requests and under a megabyte.
   Three data defects the pipeline asserts against rather than inherits: **Kota Batu 2025** has an
   exact 100× decimal slip in its "Tahunan" cell (51,279.0 ha against a monthly sum of 512.8);
   **var 2345's 2026 annual cell** was never refreshed past the Jan–Apr sum, so annual totals are
   always recomputed from monthly cells; and ~40 East Java regency-months are blank where they are
   almost certainly true zeros.
3c. **The BPS licence splits, and the split is workable.** The **data** terms explicitly permit
   commercial use — *"using the data for both commercial and non-commercial purposes"* — subject
   to citation and no implied endorsement. The **WebAPI developer** terms are narrower:
   *"BPS berkomitmen pada akses gratis dan terbuka ke API kami untuk tujuan non-komersial"*, and
   they forbid selling or sublicensing API access. Reading: the numbers are fine commercially with
   citation; what is forbidden is reselling the pipe. So we ingest to our own store, cite BPS, and
   never expose or proxy the API. **Worth putting in front of whoever signs off.**
3d. **The best possible ground truth exists and is almost entirely unpublished.** KSA's own phase
   classification — *vegetatif awal/akhir*, *generatif*, *persiapan lahan*, *puso* — is exactly
   the label set a satellite phenology model wants. BPS publishes it through the WebAPI for **DKI
   Jakarta only, 2018–2021** (domain 3100, vars 1006/1017/1018/1019/1020/1022) — a province with
   almost no rice. **A direct data request to BPS is the highest-value action available to this
   case and costs nothing but a letter.**
4. **Sentinel-1 route: ASF OPERA L2 RTC-S1 (`config.S1_ROUTE = "rtc"`).** The arithmetic that
   decides it, verified live: Java needs **28 IW GRD scenes per 12-day cycle** across 12 relative
   orbits (ASF's count and CDSE's OData `$count` agree exactly), a GRD scene is **836–847 MB**
   measured, and two years of Java GRD is **~1.4 TB**. The same two years as OPERA RTC bursts is
   **~290 GB — 5× smaller and already terrain-corrected**, so calibration, terrain correction and
   the DEM all leave our scope. It is free and open (CMR `FreeAndOpenData: true`) and **the
   existing `EARTHDATA_TOKEN` is the credential — no new registration**. Measured revisit at real
   Java rice locations: Central Java **124 acquisition dates in two years** (~6-day effective,
   tracks T127+T076), West Java 56/yr, East Java 44/yr.
   *Trade-off to confirm:* OPERA is **30 m**, and Javanese sawah plots are 0.3–0.5 ha, so mixed
   pixels dilute the flooding minimum. The mitigation is a **10 m close-up on one focus regency**
   via CDSE Sentinel Hub (`GAMMA0_TERRAIN` + orthorectify, server-side, no egress) — Indramayu
   for two years is ~3,200 processing units against a free 10,000 PU/month, but **all-Java at
   10 m is ~3,300 PU per date and is therefore impossible on the free tier**. That close-up is
   the case's only registration ask: one free CDSE account. Fallback with no account at all:
   `s3://sentinel-s1-l1c` is genuinely anonymous (LIST 200, ranged GET 206) and the measurement
   TIFFs are true COGs, so an AOI can be window-read — at the price of doing our own RTC.
5. **The official Indonesian rice-field map is validation-only, never stored.** "Lahan Baku
   Sawah" (ATR/BPN Decree 686/SK-PG.03.03/XII/2019) is **live and anonymous** on BIG's service
   (`kspservices.big.go.id/satupeta/.../MapServer/36`, 1,242,551 sawah polygons), but **no
   licence is stated anywhere on it**, and every catalogue record on the national SDI portal
   returns a null licence field. Same shape as Case H's concession problem: excellent for
   validating our extent, unusable as a stored or redistributed layer. (The regional service host
   `geoservices.bappenas.go.id` did not resolve at all on 2026-08-30; and the SDI's HTML pages
   403 while its CKAN API answers normally with a browser User-Agent — the API is the way in.)
5b. **Rice mask: Open-SEA-Rice-10, with NESEA-Rice10 as the cross-check.** Both **CC BY 4.0 on
   Zenodo** — and the licence was read from the *deposit*, not the paper, because
   Open-SEA-Rice-10's article renders CC BY-NC-ND while its Zenodo record is CC BY 4.0. It is
   10 m, 2021, and its classes are **1/2/3 = single/double/triple crop**, so it is simultaneously
   the extent prior and the independent benchmark for gate G-I3. Its published Indonesia accuracy
   is OA 98.8 %, F1 0.851, R² 0.85 against national statistics, with a consistent **~6,460 km²
   underestimate** carried forward as a stated bias. **Rejected:** IRRI's Asia lowland rice extent
   (CC BY-NC-SA 4.0), RIICE/CRISP (dead site, commercial service), the Zhao/Zhang 30 m product
   (its raster stops at 5.63°N and never crosses the equator), and **ESA WorldCereal** — which
   has no rice class at all, and whose irrigation layer measured **0.09 %** over Semarang/Demak/
   Grobogan in the middle of the wet paddy season. WorldCereal must not be pitched as a paddy
   layer. Its Reference Data Module does hold real Java ground truth (336 polygons, 89 labelled
   rice, 2023) — but the RDM permits CC BY-NC per collection and this one exposes no licence
   field, so **confirm before using it even for validation**.
6. **geoBoundaries gbOpen has no Indonesian ADM3** (verified: the API 404s). ADM1 is 34 units,
   **ODbL 1.0**, 2017 vintage, OSM-derived; ADM2 is 519 units, **CC BY 3.0 IGO**, 2020, from BPS
   via OCHA. Kecamatan geometry therefore comes from HDX COD-AB (CC BY-IGO, last modified
   2026-06-23; gdb 208.6 MiB, shp 474.6 MiB). Case H found the `.gdb` exposes no layer named
   `adm1` under the current `pyogrio` and fell back to geoBoundaries — **the SHP zip is the
   workaround here**, at the cost of a larger download. Note the ODbL share-alike obligation on
   ADM1 if any derived boundary geometry is ever redistributed.
7. **The KSA methodology break at 2018 is drawn as a break.** BPS replaced its eye-estimate
   harvested-area method with the KSA area-frame sample from the 2018 reference year, and the
   series are not comparable. Any chart running one line through 2017–2018 tells a lie about a
   trend that is a definitional change. Pre-2018 is stored as a separate regime and excluded
   from G-I1.
8. **Monthly (sub-annual) KSA is what gate G-I2 needs**, and it is published at province level in
   the "Luas Panen dan Produksi Padi" release rather than necessarily in the WebAPI. Where the
   API does not carry it, the actual publication route is recorded in `config.BPS_MONTHLY` and
   the figures are ingested from it with the source stated on the page.
9. **Production = our area × BPS productivity**, shown as arithmetic. Confirm that is the framing
   you want on a sales asset; the alternative (silence on production) is safer still but loses
   the food-security headline.
10. **MODIS phenology is a cross-check with an expiry date, not an input.** MCD12Q2 is **stuck at
   2024** (a CMR query for 2025–26 returns zero granules) and **caps at two cycles per year** —
   structurally unable to represent Java's triple-cropped irrigated sawah, which is precisely the
   thing this case measures. Beyond that, LP DAAC retired MODIS from `e4ftl01.cr.usgs.gov` on
   2025-06-30, and **Terra and Aqua begin shutting down in late 2026 / early 2027**; both are
   already drifting from their design overpass times, which injects an illumination bias that
   *mimics* a phenological trend. So: our transition dates are derived from radar, MODIS appears
   only as a labelled comparison, and VIIRS (VNP13A1/VNP22Q2) is named as the durable successor.
11. **"Why radar" is measured, not asserted.** Chapter 01 counts usable Sentinel-2 scenes per
   month over Java from the anonymous Element84 Earth Search STAC (585 scenes in July 2026 alone
   below 30 % cloud, across 62 MGRS tiles) and shows the wet-season collapse next to the radar
   record. Note the Collection-1 bucket is `e84-earth-search-sentinel-data`, **not**
   `sentinel-cogs`.
12. **CHIRPS: v2 dies after December 2026, and Java fits in one COG tile.** Pinning v2 pins a
   product that stops updating mid-engagement, so the config builds on v3. There is no Asia subset
   and no server-side subsetting at all (THREDDS/OPeNDAP are dead — it is plain nginx), **but** the
   `cogs/` tree is tiled 512×512 and Java's pixel window falls wholly inside one tile, verified
   end-to-end with a Range request returning HTTP 206: **~125 KiB/day against 3 MiB for the whole
   global file, a 64× saving**. The `.tif.gz` products are gzip streams and cannot be range-read —
   that route means downloading 9.6 GiB of global files to extract ~210 MiB of Java. Also note
   `cogs/p05/2026/` is missing April and May entirely, and CHC's own README records a %CCD bug
   that zeroed precipitation where IR data was missing, *"always a problem for Eastern
   Australia/Indonesia/Japan, where a gap between two geostationary satellites exists"* — fixed in
   2015, so our window is clean, but zero-rainfall runs get sanity-checked before any threshold is
   built on them.
13. **The context sources are context, and two carry licence problems.** ASEAN AFSIS has **no
   rights statement at all** beyond a "Copyright 2017 … Rights Reserved" footer — worse than a
   restrictive licence, because there is no affirmative permission — and is a revision-lagged copy
   of BPS anyway. Kementan's BDSP works but re-serves BPS to the rounding (11,320,986.21 ha vs
   BPS's 11,320,986.23), so it is a cross-check, not a source. **FAOSTAT** is now CC BY 4.0 *with
   an appended clause*: *"Datasets shall not be used for or in conjunction with the promotion of a
   commercial enterprise and/or its product(s) or services"* — a consulting demo is exactly that
   edge case, so FAO is context-only and the question is escalated rather than assumed. FAO's ASIS
   contradicts itself outright (CC BY 4.0 on one page, `CC-BY-NC-SA-4.0` in FAO's own catalogue).
   The one genuinely useful FAO asset is the FPMA price API — live, anonymous, Indonesian retail
   rice monthly 2008→2026 with nominal, real and USD values, sourced from BPS.
14. **The food-security narrative has a real tension in it.** BPS var 295 (wholesale rice, monthly,
   2017–2026) shows prices **up ~9 % from January 2025 to July 2026 despite the record 2025
   harvest**. That is the kind of thing a client remembers, and it is one API call.

## Resource behaviour

Every download loop checks free disk before the next acquisition and exits 0 (resumable) below
10 GB. `data/manifest.json` is the ledger, keyed by (relative orbit, acquisition date). The raw
burst is deleted as soon as the per-cell aggregates are written — the house rule from Case E — so
standing disk stays near the aggregate size (~3 GB) rather than the transfer size (~50 GB).

The SAR ingest is the only long stage, and it is meant to run as a systemd transient unit like
the other heavy jobs on the shared box:

```sh
sudo -n systemd-run --unit rice-sar --uid ubuntu --gid ubuntu \
  -p MemoryMax=3G -p WorkingDirectory=/home/ubuntu/demo-lab/cases/rice-security \
  --setenv=HOME=/home/ubuntu --setenv=PATH=/home/ubuntu/.local/bin:/usr/bin:/bin \
  /home/ubuntu/.local/bin/uv run python pipeline/ingest_sar.py
# resume after a failure: sudo systemctl reset-failed rice-sar, then re-run the same command —
# finished (orbit, date) pairs are skipped from the manifest.
```
