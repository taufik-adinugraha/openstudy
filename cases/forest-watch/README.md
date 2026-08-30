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

## Decisions pending user verification

1. **`gfw_planted_forests` (SDPT v2) replaces Descals-from-Zenodo as the palm layer.** It is
   plain CC BY 4.0, already on the identical 10-degree tile grid (so co-location with the alerts
   is exact rather than a spatial join), and 205 MB instead of 300 MB of Zenodo downloads.
   Trade-off: SDPT is a compilation of national/NGO polygon datasets, so it under-maps
   smallholder palm relative to Descals' 10 m remote-sensed extent — which biases the
   PALM-INTERNAL share **down** and the UNLINKED share **up**. The published number is therefore
   conservative, which is the right direction for a compliance claim. Swapping in Descals later
   is a one-layer change.
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
8. **Naming mills and parent groups on a public page.** The UML fields are published CC BY 4.0,
   and the page names them. A Jakarta consultancy may still prefer aggregation to group level —
   unchanged from the spec, still your call.
9. **Weekly alert refresh cron vs D3's static-snapshot rule** for non-flagships — unchanged from
   the spec. `make refresh` exists and is idempotent by RADD version; nothing is scheduled.
10. **Sentinel-2 before/after chips (`chips.py`) are not built.** Ranked below the gates and the
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
