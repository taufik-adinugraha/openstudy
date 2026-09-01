# Phase 2 — Jakarta Is Sinking

Sentinel-1 InSAR land-subsidence velocity for Greater Jakarta (deposited field,
Ohenhen et al. 2026, CC BY 4.0), fused with elevation, population, built-up
surface and observed floods into a kelurahan-level exposure map — and a clock.
Static reproducible snapshot per decision D3; rebuilt when new interferograms
warrant it. Spec: `docs/spec-jakarta-sinking.html`.

Status: **LIVE** — http://18.141.229.57:4327/jakarta (service `demo-jakarta`,
Astro dev on port 4327, base `/jakarta`). Gate G-C3 (own LiCSBAS run) pending.

## Pipeline (runs on the server, `~/demo-lab/cases/jakarta-sinking`)

    make ingest    ~300 MB of open inputs, idempotent, each source independent
    make grid      deposited field → 0.001° velocity raster + gates G-C1/G-C2
    make fuse      kelurahan exposure + the clock + gates G-C4/G-C5
    make export    NaN-safe web view-models + hero render
    make review    the eight adversarial-review tests → data/derived/review.json
    make article   review.json + published outputs → web/src/data/article.json
    make validate  = grid + fuse (re-evaluates every gate)
    make rebuild   = ingest + grid + fuse + export + review + article

`uv sync` first. Long jobs run as transient units
(`sudo systemd-run --unit jk-<name> … -p MemoryMax=4G`); the full
grid→fuse→export chain takes ~45 s and peaks ~0.5 GB.

## Gate results (2026-08-30 snapshot)

| Gate | Check | Result |
|------|-------|--------|
| G-C1 | Literature agreement — NW-coast hotspot cores (p10) ≥ 2 cm/yr, neighbourhood medians ≥ 1 cm/yr, central Jakarta within ±1 | **PASS** (Muara Baru −4.15 \| −4.92; Kosambi −4.36 \| −5.18; Monas +0.16) |
| G-C2 | GNSS agreement ±5 mm/yr (Susilo et al. 2023) | **PASS** (CJKT −4.6 vs −6.4; CTGR −1.4 vs −2.9; CBTU +1.9 vs −0.5) |
| G-C3 | Own LiCSBAS run vs the deposit | **DEFERRED** — not started (13 GB LiCSAR download; run after flagship bandwidth frees) |
| G-C4 | Exposure sanity | **PASS** — 267 kelurahan; WorldPop 10,724,798 (+1.5 % vs census 10,562,088); GHSL −2.1 % vs WorldPop; 100 % of mainland kelurahan ≥ 30 % InSAR coverage |
| G-C5 | Flood plausibility (Spearman ≥ 0.5 vs BPBD flood events) | **FAIL** — ρ = 0.16 (kept red, diagnosed: the 2021–24 BPBD record is dominated by riverine floods along the Ciliwung in higher-ground East Jakarta; flood frequency is *inversely* related to low ground, ρ = −0.14. The Jan 2020 extent does overlap the top-20 exposure kelurahan disproportionately: 12 % vs 5 % city-wide) |

Headlines: 10.7 M people (WorldPop 2020) across 661 km²; 441,934 on ground
sinking faster than 2 cm/yr; fastest kelurahan Pluit (p10 −3.99 cm/yr);
people on ground below +1 m: 391 k (2025) → 468 k (2050) under linear
extrapolation; below mean sea level: 121 k → 192 k; 14 kelurahan already have
low ground below MSL.

## Adversarial review (`/jakarta/article`, 15 sections, 11 figures)

`pipeline/review.py` runs eight pre-specified tests over data already on disk;
`pipeline/article.py` turns them plus the published outputs into
`web/src/data/article.json`, which both the article and the corrected case page
read, so prose can never drift from numbers.

| Test | Result |
|------|--------|
| H · replication vs the depositors' own table S2 | **PASS** — all five kota, largest mean error **0.032 cm/yr** |
| B · InSAR vs GNSS on matched windows | InSAR − GNSS(whole record) **+1.78 ± 0.51** mm/yr → **−0.03 ± 0.89** on 2017+ (+0.40 ± 0.90 on 2018+) |
| A · deceleration | CJKT **−8.58 → −3.77** mm/yr; 3/3 metro stations slowed (+3.18 mean); 12/17 elsewhere on north Java accelerated |
| C · ground estimator | below +1 m 2025: **391,029** (cell min) · 144,346 (q25) · **35,965** (cell mean); 61,713 people on 1,981 cells pinned to exactly 0.00 m |
| D · elevation epoch | 391,029 → **432,584** today = the published clock's **2037** |
| E · datum sweep | +10 cm sea level: below-MSL 2050 192,155 → 220,805 |
| F · radar switched off | 2050 ranking **ρ = 0.959**, 19/20 top-20 shared |
| G · decaying rate | 2050 below +1 m **422,303** vs 468,034 published (59% of the rise disappears) |

Corrections applied to the case page: the hero's "north coast" framing (12 of the
20 fastest-median kelurahan are in Jakarta Barat, 6 of 20 coastal, ρ(rate, low
ground) = 0.149); the false reference-frame explanation in the validation footer;
the hotspot footnote's attribution of the whole gap with 2000s literature to pixel
size; the unqualified "below +1 m" headline; the one-sided clock caveat; the
un-disclosed rounding artefact in the below-MSL series; the exposure ranking's
elevation dominance; and the flood check reframed from apology to finding.

## Decisions pending user verification

1. **Gate renumbering.** The build brief asked for G-C4 = exposure sanity; the
   spec's original G-C4 (flood plausibility) became **G-C5**. Spec updated.
2. **G-C5 kept red.** The plausibility gate fails (ρ = 0.16 vs threshold 0.5).
   Published as FAIL with the diagnosis above rather than redefining the gate
   to pass. The 2020-extent overlap (12 % vs 5 %) is shown alongside.
3. **GHS-BUILT-S added to ingest.** Built-up area needed GHSL's built-up
   surface tile (R10_C29, 9.8 MB, CC BY 4.0) — only GHS-POP was in the
   original ingest set. Added to the `population` stage; idempotent.
4. **Ground proxy from a surface model.** GLO-30 is a DSM. Ground per 100 m
   cell = the minimum of its ~9 GLO-30 pixels (roads/yards, not roofs),
   clamped at −5 m; kelurahan "low ground" = p10 of those cell values. In
   fully roofed cells this still overstates ground; stated on the page.
5. **No DEM epoch correction.** GLO-30 (2011–2015 acquisitions, ±2–4 m
   vertical) is taken as the 2025 surface. Chosen over speculative back-casting;
   stated on the page. **Revised after the review:** the omission is now priced
   (`pipeline/review.py` test D — 391,029 → 432,584 below +1 m today, i.e. 12 of
   the clock's 25 years) and the page no longer claims the clock is "if anything,
   optimistic": a fixed sea-level datum pushes the same way, while the
   constant-rate assumption pushes hard the other way.
6. **Clock assumptions.** Linear extrapolation of 2017–2023 rates; cells
   without InSAR coverage held stable; thresholds +1 m and 0 m (EGM2008 MSL).
   Momentum, not a forecast — repeated wherever the clock appears.
7. **Analysis grid.** Everything is computed on WorldPop's own ~92 m cells
   (EPSG:4326); kelurahan assigned by cell centre — unbiased at internal
   boundaries, conserves city totals. Velocity resampled bilinearly;
   GHSL warped from Mollweide (sum/average).
8. **Kepulauan Seribu excluded** from map and rankings (6 island kelurahan,
   outside the velocity field); kept in the table with null metrics.
   Kelurahan rankings additionally require ≥ 30 % InSAR coverage (all 261
   mainland kelurahan qualify).
9. **Ramp orientation.** Per tokens/design brief: stable = deep navy
   (`--ramp-0`), fast subsidence = luminous cyan → pale (`--ramp-4`).
   `grid.py`'s diagnostic preview PNG uses the inverse; left untouched.
10. **Display-only gap fill.** The web velocity PNGs fill isolated empty cells
    (3×3 neighbourhood, ≥ 3 valid, two passes; 49 % → 64 % coverage). All
    analysis uses the raw grid; disclosed in `exposure.json` and the page.
11. **PMTiles dropped.** The 600×600 field ships as an encoded PNG coloured
    client-side (hero animation + map overlay) — smaller and static-host-only.
    Spec's export stage updated.
12. **BPBD parsing.** Flood records = rows flagged YES (any casing, incl. one
    "TES" typo) or with parseable depth > 0; the "TANGGAL_TEXT" dummy feature
    dropped; events = distinct dates; spatial join by representative point.
13. **View-models committed to git** (parity with the other cases): generated
    on the server by `make export`, pulled back and committed.
14. **Hotspot map labels** carry names only; the list alongside carries
    median | p10 with the gate ranges (2–6, ±1 cm/yr) — no per-hotspot
    literature numbers are claimed beyond the gate.
15. **"Why" chapter folded** into chapter 1 as a two-sentence cited note
    (Abidin 2011; Chaussard 2013) instead of a standalone chapter.

## Deferred (do not start without bandwidth planning)

Gate G-C3 — own LiCSBAS processing of LiCSAR frame `098A_09673_121312`
(2017–2024, ~13 GB, GACOS-corrected, clipped to 106.6/107.1/−6.45/−6.0),
compared pixelwise (r ≥ 0.7) and at hotspots (≤ 1 cm/yr) against the deposited
field. Also unlocks the hotspot cumulative-displacement time series for the
dashboard. `make insar` is a stub until then.

## Caveats (also on the methodology footer)

- Rates are 2017–2023; the deposited field averages ~75 m cells, so hotspot
  cores read lower than point-based PS studies (peaks > 10 cm/yr).
- GLO-30 floors coastal cells at 0.0 m, which front-loads the below-MSL curve.
- WorldPop/GHSL are 2020 snapshots; no growth scenarios.
- BPBD's RT-level record likely under-reports North-Jakarta tidal flooding
  (rob banjir), part of the G-C5 story.
- Jakarta Satu / BPBD layers carry no explicit licence text — used with
  attribution; HDX COD-AB is the licensed fallback for polygons.

## Identity (tokens)

`data-case="sinking"` — abyssal blue → cyan bathymetric ramp (subsidence);
coral `--danger` reserved for flood exposure.
