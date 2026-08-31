# Case I — Rice & Food Security (Phase 3)

Java's harvest, seen through cloud. Paddy phenology read out of Sentinel-1 radar backscatter —
the flooding minimum, the tillering rise, the heading peak — turned into planted and harvested
area and a harvest date, and put next to BPS's own KSA figures.

Spec (governing document): `docs/spec-rice-security.html`.
Dashboard: port **4333**, base **`/rice`** (`demo-rice.service`, not yet deployed).

Status: **BUILT AND SHIPPED (2026-08-30).** Full pipeline run end to end on the server;
dashboard live at `http://52.77.253.154:4333/rice`. `make rebuild` is the entry point.

> **The one thing to read first.** The spec's primary data path — ASF OPERA L2 RTC-S1 on the
> repo's existing `EARTHDATA_TOKEN` — **is credential-blocked and cannot be unblocked by a
> pipeline.** The token is valid (a well-formed, unexpired EDL JWT for `taufikadinugraha`, which
> LP DAAC's egress accepts); ASF returns
> `403 {"error":"invalid_token","error_description":"EULA Acceptance Failure"}` on every object
> and on `cumulus.asf.alaska.edu/s3credentials`. Fixing it takes one interactive browser visit by
> the account owner to <https://urs.earthdata.nasa.gov/approve_app?client_id=BO_n7nTIlMljdvU6kRRB3g>.
> What shipped instead is **Microsoft Planetary Computer's `sentinel-1-rtc`** — the same physical
> quantity (RTC γ⁰) from the same ESA GRD products, **at 10 m instead of 30 m**, **CC BY 4.0**,
> anonymous, and window-readable from remote COGs. See decision 4 below.

## What it found — all five gates red, and the reason is measurable

> **CORRECTED 2026-08-31 after the adversarial review** (`pipeline/audit.py`,
> `web/src/pages/article.astro`, served at `/rice/article`). The original headline — *"we find the
> rice; we find one crop where there are two"* — is **wrong**, and it was wrong in a way the case's
> own data already contradicted. The corrected headline is below; the superseded text is kept in
> git history rather than quietly deleted.

**Headline: we find a third of the rice, and about three-quarters of the crops on the third we
find.** Harvested area is a flow, so the ratio to BPS factors exactly into an extent recall and an
intensity recall, and both are measurable against Open-SEA-Rice-10:

| year | fields found | crops per field found | = share of KSA | extent's share of the deficit |
|---|---|---|---|---|
| 2023 | 33.8 % | 73.9 % | 25.4 % | **78 %** |
| 2024 | 39.6 % | 71.1 % | 30.3 % | **73 %** |
| 2025 | 47.0 % | 79.2 % | 35.0 % | **76 %** |

So roughly **three-quarters of the shortfall is fields never found**, not crops never counted. The
82.6 % figure the page used to quote as evidence that "we find the rice" is a *precision*, which a
detector can always buy by detecting less; per-year **recall is 31–39 %**. On cells Open-SEA-Rice-10
calls double-cropping we return **1.49 cycles, not one**, and we see only **37.8 %** of those cells
at all — against **3.6 %** of single-cropping cells and **12.5 %** of triple-cropping ones. The
deficit is not seasonal either: after removing a systematic one-month late bias, the rendeng lobe is
recovered at 26.2 % and the gadu lobe at 27.8 %.

**What does explain it is revisit.** Thinning Karawang's own record from a 6-day to a 12-day gap —
same fields, same detector, same thresholds — costs **92 % of its crop cycles and 72 % of its
fields**, and reproduces East Java's observed shortfall. Running the other way, as Sentinel-1C and
1D came online inside the sample Lamongan's median gap fell 12 → 8 days and its recall rose
**×4.36** (3.7 % → 16.3 %). Sentinel-1B failed on 2021-12-23, six months *before* this record opens,
so the whole series already sits on the degraded side of that event. Cell size is a real
second-order effect; **revisit binds first**.

| Gate | Result | Number |
|---|---|---|
| **G-I1** KSA reconciliation | **FAIL** | uncalibrated R² **−11.09**, MAPE **72.7 %**, worst year **74.7 %**. *Calibrated* R² **0.82**, MAPE **6.6 %**, worst year **5.1 %** — passes every threshold, but a gate that only passes after fitting to the benchmark is not a gate, so the gate is red. |
| **G-I2** Harvest timing | **FAIL** | median \|error\| **5.0 weeks** against a 2-week threshold; worst unit bias **10.0 weeks** (Lamongan). Indramayu is the best unit at a stable **+5.0 weeks**, i.e. a systematic late bias rather than noise. In 4 of 15 unit-years the detector locks onto the wrong lobe of a bimodal harvest. |
| **G-I3** Cropping intensity | **FAIL** | irrigated units **1.09–1.58** cycles/yr against a 1.5–2.5 band (Karawang 1.58 and Subang 1.56 pass; Indramayu 1.44, Bojonegoro 1.13, Lamongan 1.09 fail). Rainfed Grobogan **1.10**, which *passes* its own <1.5 clause. |
| **G-I4** Rice-map agreement | **FAIL** | we reproduce **51.2 %** of Open-SEA-Rice-10's rice area against a 70 % threshold — but **82.6 %** of our detected paddy is inside their map. We are conservative, not wrong: 251,710 cells are theirs only, 55,510 ours only. |
| **G-I5** Temporal hold-out | **FAIL** | 2025/26 uncalibrated **−62.1 %** / MAPE 67.2 %; calibrated **+3.7 %** on the sum but MAPE 104.6 % monthly; timing **4.5 weeks**. |

**Two further review findings on the table above.** G-I1's *calibrated* R² 0.82 is an annual
aggregate: at the kabupaten-**month** resolution the calibration was actually fitted at, its R² is
**0.06**, and the satellite supplies **5.0 %** of the calibrated hectares — negatively in
Bojonegoro, Grobogan and Lamongan, where the fitted interaction's effective slope on detected area
is below zero. G-I2's 5.0-week timing error survives a shape-based re-estimator (whole-curve
cross-correlation gives the same 1-month median), so it is not an argmax artefact: 10 of 15
unit-years sit at a clean **+1 month** and 3 at −4 months, which is the lobe alias. Remove the
one-month constant and the correlation between our monthly curve and BPS's rises from **0.11 to
0.77** — the harvest **date**, not the hectare count, is the output that works.

**The single most telling number in the case:** applying the literature's unchanged −17 dB
flooding rule at a 100 m cell yields **15,161 ha** where the scale-adapted criterion yields
**744,218 ha** — a **−98.0 %** collapse. That is a fact about our pixel, not about Indonesian
rice, and it is published in the sensitivity table rather than argued.

**Chapter 01's evidence, which needed no model at all:** over these six regencies, 2023–2025,
**53 %** of Sentinel-2 scenes come back under 30 % cloud in the dry season and **7 %** in the wet
season — 4.2 % in November, 2.3 % in January. 6,205 scenes counted from the anonymous Earth
Search STAC. The radar acquisition count does not move.

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

### Made during the build, 2026-08-30 — the ones that changed the case

**B1. The primary data path changed, because the planned one is credential-blocked.**
The spec's route is ASF OPERA L2 RTC-S1 on the repo's `EARTHDATA_TOKEN`. The token is valid — a
well-formed, unexpired (59 days) EDL JWT for `taufikadinugraha`, and LP DAAC's egress accepts it.
**ASF does not.** Every ASF datapool object, OPERA RTC and RTC-STATIC alike, and
`cumulus.asf.alaska.edu/s3credentials` too, answers
`403 {"error":"invalid_token","error_description":"EULA Acceptance Failure",
"resolution_url":"https://urs.earthdata.nasa.gov/approve_app?client_id=BO_n7nTIlMljdvU6kRRB3g"}`.
That approval is interactive and account-owner-only; a bearer token cannot grant itself one
(`/oauth/authorize` answers `HTTP Basic: Access denied`). Two traps that produce a confident wrong
diagnosis and are now guarded in code: `requests` **strips the Authorization header on a
cross-host redirect** and ASF datapool 307s to another host, so without `util.edl_session()` the
real 403 presents as a 401 that looks like a bad token; and an anonymous GET of the same object
returns a 307 to a working-looking URL, which is precisely the "HTML login page written into a
`.tif`" failure the brief warned about.
**What shipped:** Microsoft Planetary Computer's `sentinel-1-rtc` — RTC γ⁰ produced by Catalyst
from the same ESA GRD products, **10 m instead of 30 m**, **CC-BY-4.0** stated in the collection
metadata, anonymous SAS, COGs with 512×512 tiles and six overview levels. This is an upgrade, not
a workaround: it retires the spec's single largest stated risk (30 m mixed pixels diluting the
flooding minimum), and because the AOI is window-read the ~50 GB transfer budget became **~2 GB
transferred and 0.9 GB standing**, with the "delete the raw burst" dance unnecessary because no
raw burst is ever written. Reading overview level 8 IS the multi-look, done server-side and
provably in linear power: overview linear means match the full-resolution mean to five decimals
(0.11770 / 0.11769 / 0.11774 at 80 / 40 / 160 m). **One tension to note:** the collection
advertises `msft:requires_account: true` and yet issues a working read token anonymously. We
took the endpoint's behaviour as the contract and recorded the tension. **Action for you:**
approving the ASF app takes one browser visit and restores OPERA as a second, independent
sensor — worth doing.

**B2. The flooding threshold had to be restated, and this is the one place measurement forced a
change of definition.** The literature value (VV below −17 dB) is a *single-plot* number. Applied
to a 100 m analysis cell it detected **0.01 cycles per cell** — a measurement of our own cell
size, not of Indonesian rice. Measured over Indramayu's 107k rice-prior cells: the deepest VV the
whole four-year record ever reaches has median **−13.1 dB** (p25 −14.4, p75 −11.5) and only
**2.4 %** of cells ever cross −17 dB, while the **VH seasonal range is a healthy 8.1 dB median** —
the crop signal is completely intact, it is the absolute level that does not survive the scale. A
100 m cell holds several 0.3–0.5 ha sawah plots that are not transplanted on the same day, so the
cell mean never reaches the single-plot value. The criterion is therefore restated in the form
the physics justifies at any scale: **a flooding event is a fall of at least `FLOOD_DROP_DB`
below the cell's own non-flooded baseline** (its 80th-percentile VV). `FLOOD_DROP_DB = 3.0` was
set from physics *before any gate was evaluated* — the single-plot specular drop is 6–10 dB and a
Javanese 100 m cell has roughly half its area in synchronised transplanting, so half the
single-plot drop is the scale-adapted equivalent. It was **not** fitted to KSA. `FLOOD_DB` is
kept and reported per event as the bridge back to the literature, `validate.py` exports how every
headline moves at 2 dB and 4 dB **and under the unchanged literature rule**, and the share of
detected events that also satisfy −17 dB is published. **This is the decision most worth your
scrutiny.**

**B3. `SG_WINDOW` 7 → 5.** At `STEP_DAYS = 6` the spec's window is 42 days and the flooding
minimum it has to preserve is 2–3 weeks wide: a filter wider than the feature attenuates the
feature. 30 days is still far wider than the 12-day repeat, so speckle is still averaged down.
This is the spec's own argument against a moving average, applied to its own window length.

**B4. Orbits are selected by COVERAGE, not by acquisition count — and the obvious rule was
wrong in a way that looks fine.** Indramayu sits on the *edge* of relative orbit 98's swath:
T098 has the most dates of any orbit over the kabupaten (115) and reaches only **36 %** of its
area, stopping dead at 108.06 °E. Taking it would have produced a dense, clean, complete-looking
series for a third of Indonesia's largest rice producer and silence for the rest — which reads
downstream as "no rice". Orbits are now ranked by measured footprint coverage, added greedily to
98 % union coverage, then topped up by date count for revisit. An orbit with fewer than 20 dates
is dropped rather than normalised, because the offset that puts an orbit on the common reference
is a median over its own observations and a median over a dozen dates inside one four-month
window is not phase-independent. Resulting selections and the ±0.12–0.20 dB orbit offsets are in
`data/backscatter_meta.json` — offsets that size are exactly what an incidence-angle difference
should cost, which is the check that the normalisation is doing the right thing.

**B5. Detection runs on EVERY cell, not only inside the rice prior.** The spec has the mask
restricting the SAR read. Gate G-I4 has to *measure* agreement with Open-SEA-Rice-10, and a
detector only ever run inside the mask can only ever agree with it. The mask is carried per cell
as a covariate — confidence weighting, the G-I4 confusion matrix, the mask-sensitivity interval —
and costs roughly twice the cells, which the window-read route makes affordable.

**B6. A FOURTH published BPS defect, found here and not in the reconnaissance.** Jawa Tengah's
domain 3300 var 463 is sound for 2018–2023 (regency rows sum exactly to its own provincial row,
which matches the independent national table to the cent). **For 2024 and 2025 both halves break
at once:** the provincial row collapses to 106,347 / 125,882 ha against a true ~1.55 / ~1.67 Mha,
while the regency rows sum to 3.00 / 3.22 Mha — about 1.93× the truth. Productivity in the same
cells is fine, so it is not a unit change. Grobogan's own cells move with it: 129,631 ha in 2023
to 84,846 in 2024, a 35 % drop no agronomy supports. Every (province, year) is now refereed
against the independent national provincial table and a failure marks the year **unusable as a
benchmark** rather than being silently ingested. Consequence: **Grobogan's area benchmark runs to
2023 only**, so it is the cropping-intensity contrast (G-I3) rather than an area benchmark. The
three defects the scout catalogued are all reproduced: Kota Batu 2025 is an exact 100× decimal
slip (51,279.0 ha annual against a 512.8 ha monthly sum), var 2345's 2026 annual cell is stale,
and ~40 East Java regency-months are blank; annual totals are always recomputed from monthly
cells and blanks are never imputed as zero.

**B7. G-I1's provincial clause is evaluated on the deep-scope aggregate, and G-I2 on calendar
years.** Six kabupaten are not three provinces, so a provincial claim is not ours to make; the
gate is the same test at the largest unit the evidence supports (the sum of the six against the
sum of the same six in KSA), and aggregating does not cancel a systematic detector bias, it
exposes it. G-I2 moves from crop year to calendar year because BPS's kabupaten monthly tables end
at December 2025, so the hold-out crop year has only six of twelve months published — scoring a
peak week from half a season is a different test, not a harder one. Calendar 2025 is outside
`CAL_YEARS` either way, so the hold-out property survives.

**B8. Smaller reality corrections, each verified live.** geoBoundaries names Indonesian ADM1 in
**English** ("West Java"), which no BPS join survives without a crosswalk. COD-AB's SHP zip
layers are `idn_admin3`, not `idn_adm3` — matching on "adm3" finds nothing and looks exactly like
Case H's missing-layer problem, which it is not; ADM3 is present and 166 kecamatan fall inside the
six kabupaten. The reconnaissance's CHIRPS v3 COG path (`global_pentad/cogs/`) 404s; the live tree
is `pentads/global/cogs/chirps-v3.0.YYYY.MM.P.cog`, flat, and range-readable, which delivers the
same saving by a different route (0 pentads unavailable across 2018–2026). MPC's STAC `next` link
is a POST with `merge: true` and a body of only `{"token": …}` — replacing the body instead of
merging it silently drops the collection, bbox and datetime and pages the whole archive.
NOAA's `origin.cpc.ncep.noaa.gov` does not resolve from this network; PSL serves the same ONI.
Zenodo answers 504 under load often enough to need retries, and the Open-SEA-Rice-10 **deposit**
licence reads `cc-by-4.0`, confirming the spec's read.

### Carried forward from the spec — still needing your sign-off

1. **`[data-case="rice"]` token block** — accent `#A8CE3A`, cyclic `--phen-*` ramp, ground
   `#0A1214`. **Already present in `shared/design/tokens.css`** and copied verbatim into
   `web/src/styles/tokens.css`; nothing was edited in the shared file.
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

Measured on the 16 GB / 4 vCPU box, 2026-08-30 (the whole rebuild is about 80 minutes of wall
clock, not the overnight job the spec budgeted for — the window-read route is why):

| unit | stage | wall clock | peak RSS | output |
|---|---|---|---|---|
| `ri-sar` | `ingest_sar.py` | **29 min** (1,124 slots, 4 workers) | 1.8 G | 904 MB |
| `ri-mask` | `ingest_aux.py mask` | 8 min | 2.1 G | in `cells.parquet` |
| `ri-climate` | `ingest_climate.py` | 13 min | 0.5 G | 3.5 MB |
| `ri-bs` | `backscatter.py` | **21 min** | 3.0 G | 792 MB |
| `ri-ph` | `phenology.py` | 2 min | 1.4 G | 1.18 M cycles |
| — | `area` → `model` → `validate` → `export` | 15 min | < 2 G | 8.3 MB web |

Every long stage runs as a systemd transient unit:

```sh
sudo -n systemd-run --unit ri-sar --uid ubuntu --gid ubuntu \
  -p MemoryMax=3G -p Restart=on-failure \
  -p WorkingDirectory=/home/ubuntu/demo-lab/cases/rice-security \
  --setenv=HOME=/home/ubuntu --setenv=PATH=/home/ubuntu/.local/bin:/usr/bin:/bin \
  /home/ubuntu/.local/bin/uv run python pipeline/ingest_sar.py --workers 4
# progress:  journalctl -u ri-sar -f
# resume:    sudo systemctl reset-failed ri-sar   then re-run the same command.
#            A finished (kabupaten, orbit, date) slot is a file on disk and is skipped, so a
#            resumed run costs only what it has not already fetched.
# backscatter wants MemoryMax=5G and phenology 6G; both are single-shot, neither is resumable
# (they are minutes, not hours, so a restart is cheaper than a checkpoint).
```

To re-select orbits after a coverage change, delete `data/sar_index.json` and re-run `ri-sar`;
existing per-orbit slots are kept and only the newly-chosen orbits are fetched.
