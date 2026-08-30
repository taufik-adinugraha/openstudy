# Case F — Poverty Mapping from Space (Phase 3)

Village-level welfare estimation from satellite features — building footprints,
night lights, land cover, built-up surface, roads — trained on BPS's official
regency poverty rates (P0/P1/P2, 2016–2025 via WebAPI, verified with the lab
key), spatially cross-validated, then carried down to kecamatan by small-area
estimation benchmarked so the official number is never contradicted.

Status: **INGEST RUNNING — acquisition + per-admin-unit aggregation implemented and
streaming on the dev server (2026-08-30). Features/model/downscale/export are still
stubs.** Spec (governing document): `docs/spec-poverty-map.html`.

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

## Run (stubs for now)

```sh
uv sync
SCOPE=java make rebuild    # Java fast path (~11 GB raw); default SCOPE=idn (~20 GB)
make validate              # gates G-F1..G-F4
```

`web/` is the case's Astro app (port 4328, base `/poverty`,
`data-case="poverty"` tokens — ochre on soil).

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
6. **ADM2 reconciliation gap (measured, not estimated).** COD-AB 522 vs BPS 514:
   **488 codes match on the P-code digits, 26 BPS-only, 34 COD-AB-only**
   (`data/adm2_reconciliation.csv`). The residual is the post-2020 pemekaran —
   COD-AB is a 2020-04 vintage, the BPS series runs to 2025 (Papua splits 92xx/95xx–97xx
   in particular). Resolving it needs the name-based recode used by Flagship A
   (`cases/nightlights-pulse/pipeline/bps.py::recode_map`); that belongs to `features.py`
   and has **not** been done yet. Nothing is dropped silently — the unmatched codes are
   written out for review.
7. **Feature layers shipped vs pending.** Streaming now: BPS, COD-AB topology,
   WorldPop 2016–2025, Black Marble annual (reused), Open Buildings v3 (315 level-6
   partitions), ESA WorldCover 2021. **Not implemented in this pass**: GHSL
   BUILT-S/NRES/SMOD (needs Mollweide→WGS84 handling), OSM highway density (needs an
   osmium/pyosmium dependency and a 1.7 GB PBF), Sentinel-2 spectral indices (optional
   in the spec), and the Microsoft GlobalML footprint cross-check. Each is additive —
   a new stage in `ingest.py` with its own ledger keys — and none blocks training on
   the roof/lights/population/land-cover feature families.

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
