# Flagship A — Nighttime-Lights Economic Pulse

Monthly VIIRS night-lights nowcast of economic activity across Indonesia's
514 regencies. Full spec (governing document):
https://claude.ai/code/artifact/06da6f68-9ed2-4a61-b8ce-bac9086856d3

## Non-negotiables from the spec

- **Frozen boundaries**: all zonal stats use the 2020 kabupaten vintage;
  other years map through `data/crosswalk/pemekaran_crosswalk.csv`.
  Without this, district splits fabricate fake growth.
- **Levels-only gate**: R² ≥ 0.65 applies to the levels cross-section only
  (Gibson et al. 2021). Panel elasticity (~0.2) is displayed with CIs, never gated.
- **Never say "GDP"**: the product is a lights-implied activity index.
- **Lights source (D12)**: NASA Black Marble via LAADS archive set 5200 —
  VNP46A3 (2012→) spliced with VJ146A3 (2018→), inter-calibrated on overlap.
  EOG VNL is a manual annual cross-check only (programmatic EOG is paid
  since 2026-06-01).
- **Attribution**: NASA Black Marble citation (Román et al. 2018) on every
  lights visual; EOG credited on the annual cross-check chart.

## Run

```sh
uv sync
make month M=2026-07     # full DAG for one month
make bps                 # BPS PDRB tables (cached under data/raw/bps)
make models              # flares → deseason → calibrate → export-models
make validate            # gates G-A1..G-A4
```

`web/` is the case's Astro app (dark-first, `data-case="nightlights"` tokens).

## Decisions pending user verification (model stage, 2026-08-30)

- **BPS calibration source**: national domain `0000` vars **2194** (annual real
  PDRB ADHK by expenditure, kab/kota, 2010–2025) and **2534** (quarterly ADHK by
  industry, kab/kota, 2022–2025), total turvar 1550/2189 — one call per year
  instead of a 514-domain crawl; the two tables agree to <0.2%. Post-2022 Papua
  province codes are recoded to the 2020-vintage crosswalk by name
  (26 regencies, `data/raw/bps/code_recode.csv`).
- **Flare mask** (deviation from spec §A3's 5 km): VIIRS Nightfire global flare
  survey 2012–2019 (Elvidge & Zhizhin 2021, ORNL DAAC, doi:10.3334/ORNLDAAC/1874,
  EOSDIS "openly shared without restriction"; EOG's live catalog needs a manual
  account). Sites kept when seen in ≥3 of 8 surveys (drops one-off detections at
  the Sorowako/Konawe smelters), buffered at **3 km** — measured on the 2025
  composite, ~84% of an isolated flare's excess light falls inside 3 km while
  5 km swallows whole towns (Kota Sorong −72%, Bontang −71% of SOL). 5 km and
  1.5 km shares are kept as sensitivity columns. Correction is a per-regency,
  per-year share from the annual composites, applied to the monthly ledger as a
  derived series (raw kept); 2026 reuses the 2025 share. Sites first lit after
  2019 are missed — stated on the page.
- **Deseasonalisation**: y = log(SOL/coverage) with weights min(1, cov/0.6)²
  (zero below 5% coverage), backfitting a weighted local-linear trend (±7-month
  tricube) with month-of-year dummies + Ramadan-overlap share (hijridate);
  Ramadan β shrunk to the national estimate. Months with no usable composite are
  trend-filled and flagged (`flag_no_data`); coverage <30% flagged low-confidence.
- **Gate scope**: G-A1 runs 2018→ (ledger starts 2018; spec says 2015→). PASS,
  min R² 0.67. G-A2 is a single out-of-sample year (2025 quarterly), not the
  spec's 2016–2024 rolling backtest: 44% province win rate → **FAIL, displayed**.
- **Nowcast mapping**: quarterly YoY growth calibration (a + β·Δlog lights).
  The honest finding: β ≈ 0.002–0.007 (R² ≤ 0.01) — lights locate activity
  (levels R² ~0.7) but barely time it; the movers board therefore ranks the
  observable deseasonalised lights growth (size floor, flare regencies excluded),
  not the mapped growth.
- **Page**: new chapter "04 · Calibration"; Explore renumbered 04→05. The chapter
  fetches `data/index.json` + `data/calibration.json` at runtime (build never
  depends on them; a pending note shows if absent).
