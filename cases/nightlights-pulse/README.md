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
make validate            # gates G-A1..G-A4
```

`web/` is the case's Astro app (dark-first, `data-case="nightlights"` tokens).
