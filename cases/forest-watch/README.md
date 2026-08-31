# Case H — Forest & Commodity Watch (Phase 3)

Where Global Forest Watch stops: weekly RADD radar alerts (national), clustered into
disturbance **events** and linked to what the land becomes — mapped oil-palm plantation,
Universal Mill List catchments, peat and primary forest. Every stored linkage layer is
CC BY 4.0 with **no country carve-out** (each licence read live from the API's own dataset
metadata on 2026-08-30, not assumed).

Spec (governing document): `docs/spec-forest-watch.html`.
Dashboard: **http://52.77.253.154:4331/forest** (`demo-forest.service`, port 4331, base `/forest`).

## Run

```sh
uv sync
make rebuild     # ingest → alerts → loss → link → validate → export
make refresh     # weekly: latest RADD version → alerts → link → validate → export
```

Needs `GFW_API_KEY` in the repo-root `.env` (free, expires yearly).

> **The single most expensive gotcha in this case.** The GFW API gateway accepts the key only
> when the header is spelled exactly `x-api-key`. `urllib.request` title-cases custom headers to
> `X-api-key`, and every authenticated call then fails with a 403 reading *"Request is missing
> valid API key"* — which looks exactly like a dead key. Dataset **metadata** endpoints are
> public and need no key at all, so a broken key still "works" on `/dataset/{id}`: that is not a
> key test. `config.gfw_key_ok()` exercises an authenticated endpoint instead. Use `requests`
> or `http.client`, both of which preserve header case.

## Data path as built (reality, where it differs from the spec)

| Layer | Dataset · version | Grid | Size (IDN) | Licence |
|---|---|---|---|---|
| RADD alerts | `wur_radd_alerts` v20260823 | 10/100000 (10 m) `date_conf` | 703 MB | CC BY 4.0 |
| Tree-cover loss | `umd_tree_cover_loss` v1.13 | 10/40000 (30 m) `year__tcd30_2000` | 303 MB | CC BY 4.0 |
| Oil palm extent | `gfw_planted_forests` v20231128 (SDPT v2) | 10/40000 `simpleName` | ~205 MB | CC BY 4.0 |
| Peat | `gfw_peatlands` v20230315 | 10/40000 `is` | 49 MB | CC BY 4.0 |
| Primary forest 2001 | `umd_regional_primary_forest_2001` v201901 | 10/40000 `is` | 125 MB | CC BY 4.0 |
| GLAD-L alerts | `umd_glad_landsat_alerts` v20260829 | 10/40000 `date_conf` | 108 MB | CC BY 4.0 |
| Mills | `gfw_universal_mill_list` v202508 (points) | — | < 1 MB | CC BY 4.0 |
| Provinces | geoBoundaries IDN ADM1 (gbOpen) | — | small | CC BY 4.0 release / ODbL upstream |

Total raw ≈ **1.3 GB** (budget was 8 GB).

### Licence findings — the concession question, settled with evidence

The spec assumed no commercially-usable Indonesian concession vector exists. **Verified true**,
by reading each dataset's own `license` metadata field from the API:

| Dataset | Licence field, verbatim | Verdict |
|---|---|---|
| `gfw_oil_palm` | `CC BY 4.0 (excluding Indonesia)` | **Excluded** — the carve-out is exactly Indonesia |
| `rspo_oil_palm` | `Disclaimer for Map Publication` (a PDF, not an open licence) | **Excluded** |
| `idn_wood_fiber` | `View Only, Not Downloadable.` | **Excluded** |
| `gfw_wood_fiber` | `CC BY 4.0 (excluding Indonesia)` | **Excluded** |
| `gfw_mining_concessions` | `CC BY 4.0 (excluding Indonesia)` | **Excluded** |
| `gfw_plantations`, `gfw_logging`, `gfw_pre_2000_plantations`, `gfw_planted_forests_palm_oil_buffered_10km`, `gfw_universal_mill_list_buffered_50_km` | no `license` field at all | **Excluded** |
| **`gfw_planted_forests`** (SDPT v2) | **`CC BY 4.0`** — no carve-out | **Used** — this is the palm-extent layer |

So no concession boundary is stored or redistributed anywhere in this case. The stored linkage
is plantation extent + mill catchments + peat + primary forest, all cleanly licensed. That is
the honest version of the spec's compromise, and it is stated verbatim on the page.

## Results (RADD v20260823, 2020-01-01 → 2026-08-19)

**404,287 disturbance events, 763,643 ha, clipped to Indonesia. 75.0 % of those hectares fall
inside at least one palm-mill sourcing catchment or mapped oil-palm estate; 13.6 % are on peat.**

| Class | Share of alerted ha | ha |
|---|---:|---:|
| Mill catchment (≤ 50 km, outside mapped palm) | 70.7 % | 540,015 |
| Unlinked (frontier) | 25.0 % | 191,138 |
| Palm — edge | 2.5 % | 18,913 |
| Palm — internal | 1.8 % | 13,577 |

| Gate | Result | Numbers |
|---|---|---|
| **G-H1** Hansen reconciliation ±5 % | **fail** (diagnosed) | 335 province-years; median deviation **0.69 %**, 99.7 % inside tolerance. The single exceedance is **Jakarta 2017: 1.08 ha against 1.15 ha** — a 0.07 ha difference. All 324 province-years above 100 ha pass, worst 3.98 %. Reported as computed, not re-cut. |
| **G-H2** RADD reconciliation ±10 % | **pass** | Riau +8.2 %, Central Kalimantan +1.8 %, Papua +0.9 %, on unfiltered alert hectares |
| **G-H3** GLAD-L agreement ≥ 60 % | **pass** | **78.6 %** of 17,062 high-confidence events ≥ 5 ha |
| **G-H4** Riau linked share ≥ 25 % | **pass, but vacuous** | **97.9 %** of 61,455 ha, 87.1 pp of it mill-catchment. The review measured Riau's base rate: **94.8 %** of Riau's alertable forest is already inside a mill catchment, so the gate is a **1.03× lift** and no threshold below 95 % could have failed. It also tested a proximity share against Gaveau et al.'s *direct-conversion* floor, which is a different quantity. Retained as a plumbing sanity test and relabelled on the page. |

## Review (2026-08-31) — `/forest/article`

An adversarial review of this case, published as its own page and driven entirely by
`pipeline/article.py` from this case's own outputs. Two new pipeline stages:

- `pipeline/baserate.py` — three tests the case never ran on itself: (a) a 445 m lattice sample
  of RADD's entire detection domain with the distance to the nearest of 1,396 mills at every
  point, giving the base rate; (b) 2001–2025 Hansen loss crossed at native 30 m with SDPT
  plantation extent and the 2001 primary mask; (c) the catchment share recomputed with no event
  floor, plus catchment overlap and radar-vs-optical lead.
- `pipeline/article.py` — assembles `web/src/data/article.json`, which the article page imports.

Headline findings, all from this case's own data:

| Finding | Number |
|---|---|
| Base rate: alertable forest within 50 km of a mill | **42.5 %** (all Indonesian land: 51.9 %) |
| Observed alerted share | 74.8 % → **lift 1.76×** |
| Same, with no 0.5 ha event floor | 59.2 % → **lift 1.39×** |
| Lift at 10 km (the maximum tested) | **3.01×** — 50 km is the second-least informative radius tested |
| Riau lift / North Kalimantan lift | **1.03× / 2.43×** |
| Mills claiming each in-catchment hectare | mean **8.58**, median 6, max 67 |
| Per-mill pressure summed vs hectares that exist | **7.14× over-count** — the column is not additive |
| Tree-cover loss 2001–2025 inside mapped plantation | **43.3 %** of 33.36 Mha (oil palm 9.51, rubber 2.81, wood fibre 2.12 Mha) |
| Our primary-forest loss vs GFW published | **+0.71 % (2023), +0.75 % (2024)** — a new, passing reconciliation |
| Radar lead over optical, in-window pairs | median **+20 days**, radar first in **78 %** |

Two findings that change the story rather than decorate it:

1. **RADD's detection domain in Indonesia is the UMD 2001 primary-forest mask.** 100 % of alert
   pixels fall inside it. `pipeline/baserate.py` measures that mask at **94.6 Mha, 49.7 % of
   Indonesian land** — close to Turubanova et al. (2018)'s published Indonesian primary-forest
   area, which is an independent check on our raster handling — and finds only **13.7 %** of the
   mapped oil-palm estate inside it. So the in-primary flag carries no information, and the
   palm-extent classes are structurally capped: an estate that was already plantation in 2001 is
   outside RADD's domain and can never raise an alert. Both are stated on the page.
   *(Corrected 2026-08-31: an earlier version of this note said the mask covers "6–28 % of the
   land in these tiles" — that was the share of each 10-degree tile square, ocean included — and
   said Java and Nusa Tenggara "return no alerts at all". They return 2,590 ha and 6,978 ha.)*
2. **The 0.5 ha event floor keeps only 33 % of alerted hectares nationally** (18–31 % in the three
   reconciliation provinces). The page publishes that ratio next to the event count instead of
   quietly reporting the filtered total as "the" number. The review found the floor is not
   neutral: dropped detections sit further from mills than the ones kept, so the floor lifts the
   published mill-catchment share from 59.2 % to 74.8 %.

## Decisions pending user verification

1. **`gfw_planted_forests` (SDPT v2) replaces Descals-from-Zenodo as the palm layer.** It is
   plain CC BY 4.0, already on the identical 10-degree tile grid (so co-location with the alerts
   is exact rather than a spatial join), and 205 MB instead of 300 MB of Zenodo downloads.
   **Corrected 2026-08-31 by the review.** This entry previously asserted that SDPT, being a
   compilation of national/NGO polygon datasets, *under-maps* smallholder palm relative to
   Descals' 10 m remote-sensed extent, and concluded that the published palm-linked share was
   therefore conservative. Measured on the review's own lattice (`pipeline/baserate.py --test c`),
   SDPT maps **19.07 Mha** of Indonesian oil palm against Descals et al. (2021)'s 11.54 Mha
   mapped / 12.05 Mha estimated and Gaveau et al. (2022)'s 16.24 Mha mapped / 18.83 Mha
   omission-adjusted — **1.65× Descals**, not a fraction of it. Polygon compilations carry whole
   estate blocks including unplanted ground, so the bias runs the other way. The conservatism
   argument is withdrawn. Swapping in Descals remains a one-layer change and would now be a
   *tightening* rather than a loosening.
2. **The palm raster band is `simpleName`, not `simpleType`.** `simpleType` in Indonesia carries
   only three values (0 / "Planted forest" / "Tree crops") and cannot say "oil palm";
   `simpleName` can. The value→class mapping is **calibrated empirically at ingest time**
   (`ingest.py --calibrate-palm` samples raster values and asks the vector table what class sits
   there) and written to `data/raw/palm_classes.json` — it is never a constant guessed in code.
   Calibrated result: **1 = Oil palm, 2 = Wood fiber or timber, 3 = Rubber.** `alerts.py`
   refuses to run if calibration is missing, rather than silently producing an empty palm layer.
3. **Hansen is taken from GFW's pre-masked `year__tcd30_2000` tile set** rather than re-deriving
   the ≥30 % canopy mask from `treecover2000` + `datamask` off the Google bucket. Same source
   data, same forest definition GFW's own country table uses, 303 MB instead of ~5–6 GB, and
   G-H1 still gates exactly what it was written to gate (windowed reads, geodetic pixel area,
   province rasterising). The alternative is a 5 GB download for no change in the answer.
4. **Mill catchments are computed from the UML *points* with a KD-tree on the unit sphere**, not
   from `gfw_universal_mill_list_buffered_50_km`. That pre-buffered raster returns HTTP 403 on a
   free key (it is not a public tile set), and points additionally give the *distance* and the
   *identity* of the nearest mill, which a pre-buffered mask cannot.
5. **Provinces are geoBoundaries IDN ADM1 (gbOpen), not COD-AB.** The COD-AB geodatabase
   downloads but exposes no layer whose name contains `adm1` under the current `pyogrio`, so the
   code falls back automatically. Consequence: 34 provinces, so "Papua" is still the pre-2022
   boundary as the spec intended, but the licence line is geoBoundaries' (CC BY 4.0 for the
   release; ODbL applies to OSM-derived upstream geometry) rather than CC BY-IGO. Worth a look
   if the COD-AB layer naming matters editorially.
6. **No tree-cover mask is applied to the alerts.** RADD is already a forest-disturbance
   product, and G-H2 compares against GFW's own *unmasked* aggregation over the same geometry —
   masking here would compare unlike with unlike. Stated in the methodology.
7. **Block-seam stitching, not approximation.** Events crossing a 10,000 px processing block
   boundary are re-joined with a union-find over block edges, so a clearing is one event rather
   than two. This was worth ~60 lines to keep the event counts defensible.
8. **Clustering is spatio-temporal (4-week window), not purely spatial.** The first
   implementation labelled 8-connected components over the whole 2020–2026 record at once. That
   is wrong in a way that is easy to miss: a frontier creeping across the same hillside for six
   years becomes a single event dated to its first pixel, which back-dated 55&nbsp;% of the
   archipelago's hectares into 2020. Two pixels now join an event only if they are 8-connected
   *and* their detection dates are within 28 days; components come from
   `scipy.sparse.csgraph.connected_components` over an explicit neighbour graph, which is
   faster than the dense labelling it replaced. **The G-H2 reconciliation gate is what caught
   this** — worth remembering when deciding whether gates earn their cost.
9. **The spec's 13-tile list was missing `00N_090E`** — south-west Sumatra and the Mentawai
   Islands. It surfaced as a systematic 4–7.5 % shortfall in G-H1 for West Sumatra **and no
   other province**, which is what a missing tile looks like from the outside. The tile list is
   now 15 ids (`10S_130E` added for completeness); ids the API does not publish are recorded as
   absent and cost one request. Second time a gate paid for itself.
10. **G-H2 compares unfiltered alert hectares, not the event table.** GFW's aggregation counts
   every alert pixel, while the event table drops detections below 0.5 ha by design, so
   comparing the two would guarantee a failure that says nothing about our plumbing. `alerts.py`
   therefore also writes `data/alerts/raw_<tile>.parquet` — unfiltered hectares on a
   0.05° × 1-week grid — and the page states what share of hectares the event floor keeps rather
   than netting it out.
11. **Naming mills and parent groups on a public page.** The UML fields are published CC BY 4.0,
   and the page names them. A Jakarta consultancy may still prefer aggregation to group level —
   unchanged from the spec, still your call.
12. **Weekly alert refresh cron vs D3's static-snapshot rule** for non-flagships — unchanged from
   the spec. `make refresh` exists and is idempotent by RADD version; nothing is scheduled.
13. **Sentinel-2 before/after chips (`chips.py`) are not built.** Ranked below the gates and the
    dashboard under the resource budget. The stub and the Makefile target remain.

## Resource behaviour

Every download loop checks free disk before each tile and exits 0 (resumable) below 10 GB.
`data/manifest.json` is the ledger: a tile present at its recorded byte count is skipped, a
partial `.part` file resumes with a Range request, and a tile the API presigns but S3 does not
have (all-ocean tiles such as `10N_140E`) is recorded as absent rather than retried forever.
Clustering never holds a 10-degree tile in memory — it walks 10,000 px blocks, peak ~1 GB
against the 3 GB unit cap.

## Server

- Ingest unit: `fw-ingest` — `journalctl -u fw-ingest`; resume with
  `sudo systemctl reset-failed fw-ingest; sudo systemd-run --unit fw-ingest ... pipeline/ingest.py`
- Clustering unit: `fw-alerts` — same pattern, `pipeline/alerts.py` (skips finished tiles)
- Web: `demo-forest.service` → http://52.77.253.154:4331/forest
