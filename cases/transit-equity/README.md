# Case G — Transit Access & Urban Equity (Phase 3)

TransJakarta (240 routes incl. 98 Mikrotrans — official feed), KRL, MRT and both LRTs on one
r5py/R5 routing graph with the OSM street and footpath network: 30/45/60-minute access to
job-dense floorspace and to healthcare for the 1,511 Jabodetabek kelurahan and desa, then the
equity read — Lorenz, Gini, Palma, and what each mode layer buys.

Status: **BUILT.** Ingest → rail GTFS → network → matrix (3 scenarios) → access → equity →
validate → export all run on the dev server; the dashboard is served at
`http://18.141.229.57:4329/transit`. Spec (governing document): `docs/spec-transit-equity.html`.

## What the build found (2026-08-30 run, `data/stats.json` + `data/equity.json`)

Access = share of Jabodetabek's job-dense floorspace (GHS-BUILT-S NRES) reachable within
60 minutes door-to-door on scheduled public transport, weekday 07:00–09:00 departures, p50.

| | |
|---|---|
| DKI Jakarta, median resident | **4.29 %** |
| Bodetabek, median resident | **0.05 %** — a gap of 88× at 60 min, 14× at 30 min |
| Menteng (pop-weighted) | 9.98 %, 9 min to a hospital |
| Bekasi (pop-weighted) | 0.31 %, 20 min to a hospital |
| Gini of population-weighted access | **0.743** (0.737 without rail, 0.629 walking only) |
| Palma ratio | 109.5 — the bottom 40 % reach almost nothing |
| People who cannot reach any hospital in an hour | **38 %** of the region |
| Kelurahan reaching no measured job-dense floorspace at all | 430 of 1,511 |
| What rail adds to the average resident | +0.26 pp of reachable floorspace |
| What all public transport adds over walking | +1.32 pp |

The headline is not "Jakarta has good transit and the periphery has less". It is that an hour
of *mapped* public transport buys a central Jakarta resident about a tenth of the region's
job-dense floorspace and buys most of Kabupaten Bogor nothing at all — and that a large part
of the periphery's zero is a **data** fact as much as a transport fact, because the angkot
that actually move those residents publish no timetable anywhere. Access outside the
corridors should be read as a floor.

**Read the review before quoting any of the numbers above as levels.** `pipeline/review.py`
runs eleven adversarial tests on this case's own outputs and the article at
`/transit/article` publishes them. In one line: the ordering these numbers produce is robust
to everything we tested; the levels are not.

## The adversarial review (`pipeline/review.py` → `/transit/article`)

| Test | Result |
|---|---|
| **A · threshold sweep** | Gini 0.655 → 0.718 → 0.743 and the DKI/Bodetabek gap 14× → 46× → 88× across 30/45/60 min. The "86×" headline is a property of the cut-off. Inequality *rises* with the budget: time compounds for the connected and does nothing for the disconnected. |
| **B · naive baseline** | A 10 km straight-line circle plus distance to the region's floorspace centre of mass reproduces R² 0.604 of log routed access (ρ 0.819). The choropleth is mostly a compass; the residuals are the product. |
| **C · opportunity swap** | Measured on population reachable rather than floorspace, the Gini is 0.686 not 0.743 and the Palma 16 not 110. |
| **D · MAUP ladder** | Same people, same network, same hour: Gini 0.743 / 0.721 / 0.660 and Palma 109.5× / 35.8× / 12.6× at kelurahan / kecamatan / kabupaten. Kelurahan areas span 415-fold. |
| **E · edge effects** | **Null.** Only 45 kelurahan (0.8 % of population) lie within 10 km of the bbox. Truncation is not driving the zero mass. |
| **F · rail chord deficit** | All three rail lines are scheduled 5.8–8.0 % *faster* than their operators' published times, in the same direction. Mean optimism 6.8 % against a mean straight-line chord shortfall of 5.7 % — the geometry explains MRT and LRT almost exactly, and under-explains KRL. The ±15 % caveat does not cover a bias. |
| **G · poverty link** | Case F has published, so the axis is computed: Spearman −0.51, concentration index +0.184, and the share reaching nothing rises 1.6 % → 56.7 % from the least-poor to the poorest fifth. 170 kelurahan (1.06 M people) are in both worst quintiles. |
| **H · export precision** | 217 kelurahan (3.29 M people) were published as exactly 0.0 because `access.json` rounded to 4 dp — 4.8 % of the measure's own median. **Fixed:** the export now writes 6 dp. |
| **I · incidence** | 94 % of what public transport adds over walking, and 89 % of what rail adds, accrues to the 30 % of the region inside DKI. Every layer raises access *and* raises the Gini (0.629 → 0.737 → 0.743). |
| **J · surface vs volume** | Re-weighting the identical matrix with GHS-BUILT-V (building volume) rather than GHS-BUILT-S (footprint): DKI median 4.29 % → 5.28 %, gap 88× → 147×, Gini 0.743 → 0.781 — and ρ 0.9988, so the ranking is untouched. Volume is the better default. |
| **K · denominator** | On a city frame (DKI residents, DKI destinations, DKI denominator) the mean is 11.5 % and the Gini 0.35, not 1.5 % and 0.74. Against World Bank PRWP 8971's eleven African cities — the only published set reporting this exact quantity — Jakarta-as-a-city is unremarkable and Jabodetabek-as-a-region looks worse than Cape Town. The study area is 6,858 km²; Cape Town's is 2,467. |

### Corrections applied to the case page from the review

- The Gini is no longer described as comparable to income inequality — a hard cut-off
  manufactures exact zeros an income distribution never has.
- Chapter 03 now publishes the full cut-off sweep (`equity.json.by_cutoff`) beside the Lorenz
  curve, so no inequality statistic appears without its time budget.
- The "cannot reach a hospital in an hour" stat is now explicitly *on scheduled public
  transport*, with a note that the region moves on motorcycles this model does not route.
- Chapter 04 now states the *direction* and *incidence* of the layer effects
  (`equity.json.incidence`), not only the mean deltas.
- `equity.py:poverty_link` matched Case F on a column name that does not exist (`adm3*code`
  rather than `pcode`) and would have picked `official_p0` over `p0_est`. Fixed and pinned to
  the latest year; the axis is no longer pending.
- `export_web.py` writes access shares at 6 dp instead of 4.
- The People-Near-Transit check is now labelled on the page as a coverage statistic rather
  than a test: ITDP's 2016 anchor counts rapid transit only (BRT corridors, KRL excluded, its
  city and metro rows sharing one numerator), and at 98 % of Jakarta the replication has no
  power to discriminate.
- Known limits gained the cut-off, footprint-vs-volume, MAUP, rail-bias and
  network-failure entries, all rendered from `review_summary.json` so they cannot drift.

### Gate results

- **G-G1 · timetable sanity — FAIL (3 of 4).** MRT Lebak Bulus→Bundaran HI: published 30 min,
  our timetable schedules 27.6. KRL Bogor→Jakarta Kota: 95 published, 88.8 scheduled. LRT
  Jabodebek Harjamukti→Dukuh Atas: 45 published, 42.4 scheduled. The TransJakarta corridor
  fails: the official feed has **no end-to-end Corridor 1 trip** (it is encoded as overlapping
  partial trips), so the corridor is tested on its scheduled commercial speed — **11.2 km/h in
  the operator's own feed against brtdata.org's published 19 km/h**. Straight-line inter-stop
  distance makes 11.2 a lower bound by ~10–15 %, so the gap is real: the bus times routed here
  are conservative, not optimistic.
- **G-G2 · external routing check — NOT EVALUATED.** It requires live Google Routes API calls,
  which were not authorised for this run. Recorded as pending with that reason.
- **G-G3 · network integrity — FAIL.** 99.44 % of 8,568 GTFS stops snap to the street graph
  within 200 m (gate needs 98 %), but 15 of 1,511 origins cannot be routed at all: 5 in
  Kepulauan Seribu (no road link, outside the routable clip) and **10 on the mainland**
  (Bitung Jaya, Katulampa, Sentul, Srimukti and six smaller desa — their population-weighted
  centre snaps onto an isolated street fragment). The gate requires zero, so it fails; the
  affected population is 142k, under 0.4 % of the region.
- **G-G4 · plausibility — PASS.** Rail ≥ no-rail at every origin (0 material violations of
  1,511; 21 differences below the 0.5 pp tolerance are R5's Monte Carlo sampling of frequency
  headways). DKI median 4.29 % > Bodetabek 0.05 %. People-near-transit replication: Jakarta
  98 % and Greater Jakarta 42 % of population within 1 km of ≤15-min-headway service, both
  above the ITDP 2016 anchors (44 % / 16 %) as expected — though the Jakarta figure is high
  because this is a straight-line buffer over every TransJakarta and Mikrotrans stop, not
  ITDP's street-network methodology; that caveat is on the dashboard.

## Non-negotiables from the spec

- **Scheduled times, not congestion** — weekday 07:00–09:00 departure window, frequency-aware
  (RAPTOR, p50 over the window); stated on every view.
- **Hand-encoded rail is labelled** — no GTFS exists for KRL/MRT/LRT (verified against
  Transitland + Mobility Database); headways come from cited official pages and every rail
  time carries a ±15 % caveat.
- **Gates**: published-timetable OD sample within ±15 %/±8 min (G-G1, hard); stop-snapping
  ≥ 98 % within 200 m and zero unreachable origins (G-G3, hard); monotonicity + ITDP
  People-Near-Transit replication against the 2016 anchors (G-G4). **G-G2 (Google Routes
  comparison) is NOT evaluated** — it needs live Google Routes API calls that were not
  authorised; `stats.json` records it as pending with that reason.
- **Jobs are a proxy** — GHS-BUILT-S NRES floorspace, named "job-dense floorspace", never
  "jobs".

## Run

```sh
uv sync                    # needs JDK 21 on PATH and the osmium-tool CLI
make rebuild               # ingest → rail → network → matrix → access → equity → validate → export
```

Heavy stages run on the dev server as transient units (`tr-ingest`, `tr-matrix`), capped at
`MemoryMax=3G`. Every stage is resumable: re-running skips finished matrix parts and cached
downloads, and each stage exits cleanly (code 3) if free disk drops below 10 GB or free RAM
below 4 GB.

`web/` is the case's Astro app (port 4329, base `/transit`, `data-case="transit"` tokens),
served by `demo-transit.service`.

## Decisions pending user verification

Carried over from the spec:

1. **TransJakarta GTFS licence is unstated** (feed, Transitland, Mobility Database all
   silent). Proceeding with attribution + a written-confirmation request. If refused, the case
   loses its richest layer.
2. **Meta RWI rejected (CC BY-NC)** → the equity axis uses Case F's kecamatan poverty
   estimates instead, creating a build-order dependency **F before G**. ✅ **RESOLVED.** Case F
   has published `estimates_adm3.parquet`; the matcher had a second bug (it looked for a
   column named `adm3*code`, but the key is `pcode`, and it would have selected `official_p0`
   over the modelled `p0_est`). Both fixed; `equity.json.poverty_link.available` is now `true`
   on the 2025 vintage across 187 kecamatan.
3. **Hand-encoded frequency GTFS for rail** as publicly-defensible methodology.
4. **Google Routes as validation-only comparator** — NOT used in this build (G-G2 pending).
5. **Kepulauan Seribu**: currently included in every aggregate (6 desa, boat-only access, all
   with ~zero measured access). Excluding them would lower the Gini slightly.

Added during this build (all deviations from the spec's letter, taken for stated reasons):

6. **JVM heap 2.2 G, not the spec's 10 G.** Three other jobs share the 16 GB box. The matrix
   is chunked into 120-origin batches (`config.ORIGIN_BATCH`), each written to
   `data/matrix_parts/<scenario>_<i>.parquet` and skipped on re-run, so the job is resumable
   instead of large. It fits comfortably: peak unit memory ≈ 1.7–2.5 GB.
7. **Destination lattice is 1 km, not the spec's 500 m hexes** (5,745 cells rather than
   ~12,000). Halves the matrix cost at the same regional resolution; the coverage the cells
   capture (≈ 92 % of the region's jobs proxy and population) is recorded in the manifest.
8. **Destinations are routed at each cell's population-weighted centre of mass, not its
   geometric centre.** With geometric centres, ~2,400 rural cells could not snap to the street
   network and 436 origins showed zero access — an artefact, not a finding. Weighted centres
   put the destination point where the village actually is.
9. **One 30-minute walking budget in every scenario** (`config.MAX_WALK_MIN`). Earlier runs
   gave walking-only a 90-minute budget and the transit scenarios 30, which made "everything"
   not a superset of "walking only" and broke the layer attribution in chapter 4.
10. **OSM clip uses `osmium extract -s simple`.** The default `complete_ways` strategy needs
    more than 3 GB on Geofabrik's 900 MB `java-latest.osm.pbf` and was OOM-killed; `simple`
    peaks at 2.1 GB and only truncates ways that leave the bbox. The clip is then tag-filtered
    to a 52 MB routing subset so R5 builds inside the small heap. The 900 MB parent is deleted
    immediately after clipping (disk floor).
11. **Population comes from GHSL GHS-POP E2025 (one 100 m tile covers Jabodetabek), not
    WorldPop's 169 MB national raster.** Same resolution, a tenth of the bandwidth; WorldPop
    remains in `config.py` if a cross-check is wanted.
12. **p50 only** (the spec allows p50/p75). One percentile halves the view-model size; p75 is
    a one-line change in `matrix.py` if the spread matters.
13. **Rail line geometry on the dashboard is schematic** — straight segments between the OSM
    stations of each line, not the OSM way geometry. The Overpass query fetches relation
    membership only (`out body`), which is a few hundred KB instead of tens of MB of geometry.
14. **`krl_tanjung_priok` and the two LRT Jabodebek branches** were resolved from OSM by name
    regex (`config.RAIL_OSM_MATCH`); OSM spells the station "Tanjung Priuk". LRT Jabodebek is
    two relations (Cibubur and Bekasi lines) sharing a trunk, so it is encoded as two routes.
16. **Rail run times add no separate dwell.** `RAIL_SPEED_KMH` are *commercial* speeds derived
    from published end-to-end journey times, so dwell is already inside them; an earlier build
    added 45 s per station on top and made the MRT run 39 min against its published 30.
    `RAIL_DWELL_S = 0` and G-G1 now passes on all three rail lines.
17. **G-G1 is evaluated on the timetable, not on r5py's itinerary legs.** r5py's
    `DetailedItineraries` leg accounting produced nonsense here (936 min for Bogor→Jakarta
    Kota), so the gate compares the scheduled in-vehicle time in the GTFS the router uses with
    the operator's published journey time; the router's door-to-door p50 is published alongside
    as context.
18. **Monotonicity is evaluated with a 0.5 pp tolerance.** R5 samples frequency-based headways
    by Monte Carlo and the scenarios draw independently, so a handful of tiny negative
    differences are sampling noise. Both the exact count (21) and the material count (0) are
    published; the gate uses the material one. A fixed R5 seed would remove the need.
19. **Maps frame the mainland and shade on a square-root scale.** Kepulauan Seribu is 60 km
    offshore and was shrinking Jabodetabek to a third of the frame; access is so concentrated
    that a linear ramp painted nine tenths of the region flat black. Both choices are stated on
    the page, and the islands stay in every number and table.
20. **Angkot are largely absent from the data.** Only the community Bogor angkot feed (CC0)
    exists. Access in the outer kabupaten is therefore a *floor*: the paratransit that actually
    moves those residents has no open timetable. This is stated on the dashboard.

## Hand-encoded rail — every number and its source

`pipeline/config.py:RAIL_LINES` + `RAIL_SPEED_KMH`, echoed into
`data/gtfs/rail_sources.json` and shown in the dashboard's methodology chapter.

| Line | Peak / off-peak headway | Avg commercial speed | Source (read 2026-08-30) |
|---|---|---|---|
| MRT North–South | 5 / 10 min | 31 km/h | jakartamrt.co.id — official < 30 min for the 15.7 km Lebak Bulus–Bundaran HI |
| LRT Jabodebek (Cibubur, Bekasi) | 4.5 / 6.5 min | 32 km/h | lrtjabodebek.kai.id — service 05:53–23:11, ~45 min Harjamukti–Dukuh Atas |
| LRT Jakarta (Lin Selatan) | 10 / 10 min | 27 km/h | lrtjakarta.co.id (403s to fetchers; read in a browser) |
| KRL Bogor | 5 / 10 min | 36 km/h | kci.id GAPEKA 2025 lookup — ~95 min for 54.8 km Bogor–Jakarta Kota |
| KRL Cikarang | 8 / 15 min | 36 km/h | kci.id |
| KRL Rangkasbitung | 12 / 20 min | 40 km/h | kci.id |
| KRL Tangerang | 20 / 30 min | 32 km/h | kci.id |
| KRL Tanjung Priuk | 30 / 60 min | 32 km/h | kci.id |

Station coordinates and stop order come from the OSM route relations named in
`rail_sources.json` (ODbL). Run times are the inter-station great-circle distance at the
line's average commercial speed plus a 45 s dwell — a model, not a timetable. **Every rail
travel time in this case carries a ±15 % caveat until an operator publishes a feed.**
