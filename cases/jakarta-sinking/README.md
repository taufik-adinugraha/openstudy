# Phase 2 — Jakarta Is Sinking

Sentinel-1 InSAR land-subsidence velocity for Greater Jakarta, fused with
elevation and population to estimate flood exposure by kelurahan.
Static reproducible snapshot per decision D3 (non-flagship cases don't run
scheduled pipelines); rebuilt when new interferograms warrant it.

Status: IN PREPARATION — data-path reconnaissance running; spec follows.

## Planned pipeline (subject to the spec)

    ingest    LiCSAR Sentinel-1 products for the Jakarta frames (or a deposited
              velocity dataset, if one exists and is licensed for reuse)
    velocity  LiCSBAS small-baseline time series → LOS velocity (mm/yr),
              clipped to Jabodetabek; referenced to a stable point
    fuse      DEM + population + kelurahan polygons → exposure per kelurahan
    validate  G-C1 hotspot rates vs published studies (North Jakarta 5–15 cm/yr)
    export    web view-models (velocity tiles, exposure table, time series)

## Identity (tokens)

`data-case="sinking"` — abyssal blue → cyan bathymetric ramp (water and depth).
