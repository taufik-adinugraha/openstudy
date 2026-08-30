# Case G — Transit Access & Urban Equity (Phase 3)

TransJakarta (240 routes incl. 98 Mikrotrans — official feed), KRL, MRT and both LRTs on one
r5py/R5 routing graph with the OSM street and footpath network: 30/45/60-minute access to
job-dense floorspace and to healthcare for the 1,511 Jabodetabek kelurahan and desa, then the
equity read — Lorenz, Gini, Palma, and what each mode layer buys.

Status: **BUILT.** Ingest → rail GTFS → network → matrix (3 scenarios) → access → equity →
validate → export all run on the dev server; the dashboard is served at
`http://52.77.253.154:4329/transit`. Spec (governing document): `docs/spec-transit-equity.html`.

## Non-negotiables from the spec

- **Scheduled times, not congestion** — weekday 07:00–09:00 departure window, frequency-aware
  (RAPTOR, p50 over the window); stated on every view.
- **Hand-encoded rail is labelled** — no GTFS exists for KRL/MRT/LRT (verified against
  Transitland + Mobility Database); headways come from cited official pages and every rail
  time carries a ±15 % caveat.
- **Gates**: published-timetable OD sample within ±15 %/±8 min (G-G1, hard); stop-snapping
  ≥ 98 % within 200 m and zero unreachable origins (G-G3, hard); monotonicity + ITDP
  People-Near-Transit replication against the 2016 anchors (G-G4). **G-G2 (Google Routes
  comparison) is NOT evaluated** — it needs live Google Routes API calls the user has not
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
   estimates instead, creating a build-order dependency **F before G**. Case F had not
   published `estimates_adm3.parquet` when this ran, so `equity.json.poverty_link.available`
   is `false` with the reason recorded, and the access-vs-poverty chapter is **pending**
   rather than faked. Re-run `make equity export` once Case F lands.
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
15. **Angkot are largely absent from the data.** Only the community Bogor angkot feed (CC0)
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
