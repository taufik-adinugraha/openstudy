# Boundary crosswalk (load-bearing — spec §A2)

This directory is the ONE part of `data/` that lives in git, because it is
hand-curated, not derived.

- `kabupaten_2020.gpkg` — the frozen boundary vintage for the entire series.
  (Week-2 task: obtain from BPS; GADM 4.1 level 2 as fallback. ~50 MB — if too
  large for git, pin an R2 copy here by checksum instead.)
- `pemekaran_crosswalk.csv` — maps BPS region codes across district splits,
  columns: `year, bps_code, bps_code_2020, note`. Every post-2012 pemekaran
  event must have a row; zonal.py refuses codes it cannot map.

Without this, district splits fabricate fake growth jumps in the ledger.
