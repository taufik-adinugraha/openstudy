# Case F — Poverty Mapping from Space (Phase 3)

Village-level welfare estimation from satellite features — building footprints,
night lights, land cover, built-up surface, roads — trained on BPS's official
regency poverty rates (P0/P1/P2, 2016–2025 via WebAPI, verified with the lab
key), spatially cross-validated, then carried down to kecamatan by small-area
estimation benchmarked so the official number is never contradicted.

Status: **BUILT — full DAG implemented (ingest → features → train → downscale → validate
→ export) and the dashboard is live at <http://52.77.253.154:4332/poverty>.** Spec
(governing document): `docs/spec-poverty-map.html`.

## Results (2026-08-30, full national run)

Ingest moved **110,122,129 building footprints** across 273 Open Buildings partitions,
10 WorldPop years, 14 Black Marble annuals and 71 WorldCover tiles into per-kecamatan
aggregates for all 7,069 kecamatan, and **G-F1 fails.** It is published failing.

| gate | result | numbers |
|---|---|---|
| **G-F1** out-of-sample skill (hard) | **FAIL** | LOPO 2025: R² **0.395** (≥ 0.50), Spearman ρ **0.546** (≥ 0.70), RMSE **5.38 pp** (≤ 4.0) |
| **G-F2** Java / off-Java + urban / rural | disclosed | Java R² 0.357 · off-Java 0.381 · kabupaten 0.365 · **kota −0.599** |
| **G-F3** temporal hold-out | PASS as specified | ρ 0.985 (2024) / 0.977 (2025); **strict** (province held out too) ρ 0.548 / 0.538 |
| **G-F4** benchmark integrity (hard) | PASS | max \|recovered − official\| = **0.00 pp** over all 5,140 regency-years |

**The headline finding is the shape of the failure, not the failure.** 57 % of the squared
error is a single constant offset for a whole province. A satellite measures roofs and light;
it cannot see the *nominal poverty line* those roofs are judged against, and that line is what
sets a province's level. Remove the offsets and the model still orders regencies inside their
province at ρ 0.50 across 37 provinces. That is the component the case actually needs, because
benchmarking takes the level from BPS — but it is well short of what household targeting would
require, and the page says so.

Supporting numbers: random k-fold on the same rows reports R² 0.653 and 200 km blocks 0.577,
against the honest 0.395 — the inflation a non-spatial fold buys. A ridge baseline on the same
folds reaches 0.185. Attribution is dominated by roof size and shape (37 %) and land cover
(35 %). Inside a regency the estimates span a median 7.1 pp, up to 37.8 pp, but only **41 of
514** regencies have a poorest and a least-poor kecamatan whose intervals actually separate.
Input coverage: 372 kecamatan have no building footprint above the 0.70 confidence cut,
106 have no lit pixel in any year, 6 have no population.

## Ingest — streaming, resumable, unattended

`pipeline/ingest.py` never retains a raw input. One partition / tile / year is
downloaded, reduced immediately to per-kecamatan (ADM3) aggregates in
`data/interim/<source>/<key>.parquet`, and the raw file is deleted before the next
unit is fetched — so the retained footprint stays ~1–2 GB while ~16 GB flows through.

- **Resumable**: every completed unit is appended to `data/ingest_ledger.jsonl`;
  a rerun skips what is done. Idempotent — redoing a unit overwrites only its parquet.
- **Disk guard**: before each unit, if free disk < 10 GB the job logs and exits 0
  (no restart loop); rerun the same command to resume.
- **Bandwidth**: one connection per file, sequential — the box shares the link with
  other jobs.

```sh
uv run python pipeline/ingest.py            # all stages, in priority order
uv run python pipeline/ingest.py buildings  # or any subset of the stages below
```

| stage | source | unit | reduction |
|---|---|---|---|
| `bps` | BPS WebAPI var 621/622/623/624 | var × year | 514 kab/kota × 2016–2025 = 5,140 rows |
| `boundaries` | HDX COD-AB gdb (219 MB) | once | ADM2 522 + ADM3 7,069 parquet, then the zip is deleted |
| `worldpop` | WorldPop R2025A constrained 100 m | year | exactextract sum/count per ADM3 |
| `lights` | Black Marble annual, **reused** from Flagship A | year | exactextract sum/mean/count per ADM3 |
| `buildings` | Open Buildings v3 **level-6** partitions (315 files, 14.4 GB gz) | partition | pyarrow-streamed; roof count / area / area² / 17-bin size histogram per ADM3 |
| `worldcover` | ESA WorldCover 2021 v200 | 3°×3° tile | windowed class-pixel counts per ADM3 |
| `merge` | — | — | `data/features_raw_adm3.parquet` (+ ADM2 rollup) |

Open Buildings points are assigned to kecamatan through a ~55 m rasterised
admin-index array built per S2 cell (padded 0.05° for geodesic cell edges), so the
join is a numpy lookup rather than 100 M point-in-polygon tests; buildings falling
outside the raster are counted and logged, never silently dropped.

Server run (transient unit, resumes on its own):

```sh
sudo -n systemd-run --unit pv-ingest --uid ubuntu --gid ubuntu \
  -p MemoryMax=4G -p Restart=on-failure -p RestartSec=60 \
  -p WorkingDirectory=/home/ubuntu/demo-lab/cases/poverty-map \
  --setenv=HOME=/home/ubuntu --setenv=PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin \
  /home/ubuntu/.local/bin/uv run python pipeline/ingest.py all
journalctl -u pv-ingest -f
```

## Non-negotiables from the spec

- **Target is the BPS poverty rate**, never "wealth" or "GDP"; kecamatan values
  are estimates benchmarked to the official regency rate (G-F4 makes this exact).
- **Spatial CV only** for the headline skill number (leave-one-province-out);
  random k-fold is computed solely to show how much it flatters.
- **Gates**: R² ≥ 0.50, Spearman ≥ 0.70, RMSE ≤ 4.0 pp (G-F1, hard); Java vs
  off-Java disclosure (G-F2); 2024/2025 temporal hold-out Spearman ≥ 0.65 (G-F3).
- **Reuse, don't re-download**: Black Marble annual composites come from
  `cases/nightlights-pulse/data/raw/bm/` on the dev server.
- **No CC BY-NC data**: Meta RWI and the SMERU map were scouted and rejected.

## Features → model → downscale → validate → export

| stage | what it does | output |
|---|---|---|
| `features` | reconciles COD-AB's 2020 ADM2 codes to the current BPS codes, rolls the extensive accumulators up to ADM2, and runs **one** `derive()` on both levels | `features_adm2.parquet` (514 × 10), `features_adm3.parquet` (7,069 × 10), `features_meta.json` |
| `model` | LightGBM on the regency P0; leave-one-province-out headline, 200 km blocks, random k-fold, ridge baseline, temporal hold-out, SHAP | `cv_predictions.parquet`, `model/`, `shap_adm2.parquet`, `model_stats.json` |
| `downscale` | applies the regency model to kecamatan features and benchmarks each regency exactly | `estimates_adm3.parquet` |
| `validate` | gates G-F1…G-F4, measured not tuned | `stats.json` |
| `export` | view-models for the web app; missing artefacts are recorded as `pending` | `web/public/data/*` |

```sh
uv sync
make rebuild               # full DAG; SCOPE=java for the ~11 GB fast path
make validate              # gates G-F1..G-F4
```

Server run of the model chain (same shape as `pv-ingest`):

```sh
sudo -n systemd-run --unit pv-train --uid ubuntu --gid ubuntu -p MemoryMax=3G \
  -p WorkingDirectory=/home/ubuntu/demo-lab/cases/poverty-map \
  --setenv=HOME=/home/ubuntu --setenv=PATH=/home/ubuntu/.local/bin:/usr/bin:/bin \
  /bin/bash -lc "uv run python pipeline/model.py && uv run python pipeline/downscale.py \
    && uv run python pipeline/validate.py && uv run python pipeline/export_web.py"
```

`web/` is the case's Astro app — **port 4332**, base `/poverty`,
`data-case="poverty"` tokens (ochre on soil), served by `demo-poverty.service`.
The scaffold and the spec both say 4328; Case E (air quality) took that port first.

### For Case G (transit equity)

`data/estimates_adm3.parquet` is the cross-case hand-off — one row per
(`pcode`, `year`) for 7,069 kecamatan × 2016–2025:

| column | meaning |
|---|---|
| `pcode` | COD-AB ADM3 P-code (`ID` + BPS digits) |
| `bps_code` | parent regency, **current** BPS vintage (post-pemekaran) |
| `year` | 2016–2025 |
| `p0_est` / `p0_lo` / `p0_hi` | benchmarked poverty-rate estimate, % , and its interval |
| `official_p0` | the BPS regency rate the estimate is benchmarked to |
| `pop`, `area_km2` | WorldPop population and equal-area km², for weighting |
| `benchmark_factor` | the regency's rescaling factor, disclosed |

On the dev server: `~/demo-lab/cases/poverty-map/data/estimates_adm3.parquet`
(git-ignored, regenerate with `make downscale`). Join on `pcode`; the 8 kecamatan
inside non-census COD-AB polygons carry a null `p0_est` and must not be imputed.

### Geometry budget

ADM3 GeoJSON at COD-AB precision is tens of megabytes. `export_web.py` therefore quantises
every coordinate onto **one shared integer lattice**, drops points that become collinear
(a symmetric triangle-area test, so a shared border is judged identically from either side
and no slivers open), prunes islands under 1.5 % of their unit's largest part, and
delta-encodes the result with Google-polyline varints. It walks the lattice coarser until
the file fits the budget and writes the achieved resolution into the payload, which the page
displays. Result: **7,069 kecamatan at a 78 m lattice in 2.07 MB**, 514 regencies in 0.62 MB.

## Decisions pending user verification

1. **Licence election + rejections.** Open Buildings v3 used under its CC BY 4.0
   option (it is dual CC BY 4.0 / ODbL). Meta Relative Wealth Index and the SMERU
   2015 poverty map are **CC BY-NC → excluded**, which removed the planned
   independent wealth cross-check; the temporal hold-out (G-F3) replaces it.
2. **ADM3 topology from HDX COD-AB** (BPS-derived, CC BY-IGO, P-coded), because
   geoBoundaries gbOpen has no ADM3/ADM4 for Indonesia. COD-AB has 522 ADM2 units
   vs the flagship's geoBoundaries 519 — reconciled via P-codes + the pemekaran
   crosswalk before training.
3. **Kemendesa IDM** village index as an optional check once its licence is
   clarified (portal TLS is broken; data.go.id mirror has no licence text).
4. **Annual re-run** (each March BPS release) vs D3's static-snapshot rule.
5. **Embeddings deferred**: Tessera (CC0, ~200 GB for Java) and Major TOM
   (CC BY-SA) are v2 candidates, not for the 16 GB server.
6. **ADM2 reconciliation — RESOLVED, 514 of 514.** COD-AB 522 vs BPS 514 reconciled in
   `features.py::reconcile`: **488 match on the P-code digits, 26 are recoded by name**
   (the post-2020 pemekaran — the Papua splits, 91xx/94xx → 92xx and 95xx–97xx), **0
   unresolved**. The 8 remaining COD-AB polygons are *Danau Toba*, four unnamed *Danau*,
   *Waduk Cirata*, *Wadung Kedungombo* and *Hutan* — lakes, reservoirs and a forest block
   that are **not census units**, carry no BPS code by construction, and are labelled as
   such rather than counted as failures. The whole audit ships to the page
   (`data/adm2_reconciliation.csv` → `reconciliation.json`), so a reviewer sees every
   remapped code and its name match.
   The name normaliser is ported from `cases/nightlights-pulse/pipeline/bps.py::recode_map`
   **with a bug fixed**: there the "kota" prefix was stripped before the kota/kabupaten
   distinction entered the key, so *Kota Sorong* (9171) matched plain *Sorong* (9202) and
   two source codes collided on one target. Here the flag is part of the key, matching is
   restricted to the two unmatched sets, and a target can be claimed only once.
7. **Poverty-ramp direction (spec sentence is self-contradictory).** F6 says the ramp
   "runs soil-dark → ochre → pale sand so 'brighter = poorer' never happens". Read
   literally as a low→high ramp, that *is* brighter = poorer. We took the ramp direction
   as written — **low poverty = soil-dark, high poverty = pale sand** — because on a
   near-black ground the alternative buries the poorest units, and honoured the intent of
   the clause a different way: the ramp is deliberately earth-toned and non-luminous (top
   stop `#EBD9B8`, not a light-source cream), and the night-lights radiance ramp appears
   nowhere on the page — lights are rendered in neutral steel as an *input*. **Flip it if
   you meant the other reading**; it is one constant, `RAMP_POV`, in `web/src/pages/index.astro`.
8. **38 leave-one-province-out folds, not the spec's 34.** The spec was written against
   the pre-2022 province list; BPS now publishes 38 provinces after the Papua splits, and
   the folds follow the current codes so that a held-out unit's neighbours really do leave
   the training set.
9. **Raw coordinates are excluded from the model.** Latitude/longitude and unit
   identifiers would let the booster memorise geography, which is precisely what
   leave-one-province-out exists to prevent. Centroids are exported for the map only.
10. **Interval widening sits outside the benchmark.** The point estimate reproduces the
    official regency rate exactly (G-F4). The p10/p90 band is then widened in quadrature by
    the province's own LOPO residual RMSE, which means the *band* is no longer
    benchmark-consistent. That is deliberate: an interval that pretends to the same
    precision as the point estimate would be dishonest.
11. **Port 4332, not the spec's 4328** — Case E (air quality) claimed 4328 first. Both
    `web/astro.config.mjs` and `web/package.json` carry 4332, and the unit is
    `demo-poverty.service`.
12. **Geometry codec.** Quantise-to-shared-lattice + symmetric collinear drop + island
    pruning + polyline varints, walking the lattice coarser until the file fits (see
    "Geometry budget" above). The alternative — per-polygon Douglas–Peucker — is not
    topology-preserving across shared borders and opens slivers.
13. **Feature layers shipped vs pending.** Streaming now: BPS, COD-AB topology,
   WorldPop 2016–2025, Black Marble annual (reused), Open Buildings v3 (315 level-6
   partitions), ESA WorldCover 2021. **Not implemented in this pass**: GHSL
   BUILT-S/NRES/SMOD (needs Mollweide→WGS84 handling), OSM highway density (needs an
   osmium/pyosmium dependency and a 1.7 GB PBF), Sentinel-2 spectral indices (optional
   in the spec), and the Microsoft GlobalML footprint cross-check. Each is additive —
   a new stage in `ingest.py` with its own ledger keys — and none blocks training on
   the roof/lights/population/land-cover feature families.

14. **G-F1 fails and is published failing.** See "Results" above. Two things were
    deliberately *not* done in response: the CV design was not relaxed (a random k-fold
    would have reported R² 0.65 and cleared the bar), and the official poverty line was not
    added as a feature (it is published per regency and would have supplied most of the
    missing province-level signal, but it is derived from the very survey we are predicting,
    so using it is circular). Instead the failure is decomposed and reported. **If you want
    the gate chased, say so and say which of those two is acceptable** — both change what
    the number means.
15. **G-F3 as specified is not a clean test, so both versions are published.** Training on
    ≤ 2023 and predicting 2024/25 leaves the same regencies in the training set at earlier
    years, and the roof/land-cover layers are single-vintage — so the specified test mostly
    measures how little a regency's rate moves between releases (ρ ≈ 0.98). A strict variant
    that holds out the province as well as the year is computed alongside and is the number
    the page plots and quotes.
16. **Dark ≠ missing (lights).** `ingest.merge` keeps the intensive `lights_mean`, so a
    kecamatan that was genuinely unlit in a given year produced a divide-by-zero and became
    a missing feature — 460 units. The Black Marble grid is identical every year, so
    `features.py` now recovers each unit's pixel count from any year that has light and
    reuses it; only 106 units are dark in every year and stay missing. Note this **lowered**
    the headline R² from 0.403 to 0.395. It was kept because it is correct, not because it
    helped.
17. **A negative urban R² is reported, not hidden.** Kota (cities) have low, tightly-clustered
    rates and the model does worse than the national mean on them, so every kota estimate is
    labelled *indicative* — the same treatment G-F2 prescribes for a weak off-Java split.

## Decisions taken during ingest (reality vs spec)

- The spec's "36 level-4 cells / 15.8 GB" is the level-4 packaging. The run uses the
  **level-6 no-header partition** (`polygons_s2_level_6_gzip_no_header/`) as the spec's
  RAM mitigation says: enumerated live from the public GCS listing and filtered by S2
  cell bounds against the scope bbox → **315 partitions, 14.4 GB gz**, the largest 1.7 GB
  (vs 5.9 GB for level-4 cell `2e7`). Column order in that variant is
  `latitude, longitude, area_in_meters, confidence, geometry, full_plus_code`; only the
  first four are parsed, so the WKT geometry is never materialised.
- BPS returns 552 vertical rows per year, not 514: 38 province rows are coded `PP00`
  (not 2-digit as the spec's note implies) with `<b>`-wrapped labels. Filtering
  `code % 100 == 0` yields exactly 514 kab/kota × 10 years = 5,140 rows.
- COD-AB layer names in the gdb are `idn_admin0…idn_admin4` (not `ADM2`/`ADM3`), and
  the P-codes are `ID` + BPS digits (`ID1107`).
