"""Stage 4 · sar — the Sentinel-1 time series.  The expensive stage; read this before running it.

THE HONEST SIZE PROBLEM
-----------------------
Sentinel-1 IW GRD is roughly 1 GB per scene (VV+VH, ~250 km swath).  Java alone needs on the
order of a dozen-plus scenes per 12-day repeat, so a two-year VV+VH archive over Java as
downloaded GRD is hundreds of gigabytes before a single pixel is calibrated — and an
all-Indonesia archive is terabytes.  That is not a demo; it is a data-centre invoice.  Pretending
otherwise in a client-facing case would be exactly the kind of overclaiming quality gate 3
exists to prevent.

So the case is scoped, and the scoping is stated on the page rather than buried:

  reporting scope   ``config.SCOPE_PROVINCES`` — Java's rice-bowl provinces, which together
                    produce about half of Indonesia's rice.  This is where the question is
                    economically decided and where BPS's own KSA sample is densest.
  deep scope        ``config.SCOPE_DEEP`` — the kabupaten processed at full temporal density
                    (the classic lumbung padi districts plus one rainfed contrast, so the case
                    shows both an irrigated triple-crop calendar and a rainfed single-crop one).
  national context  BPS statistics only, clearly labelled as official statistics rather than as
                    our measurement.

ACCESS ROUTE — SELECTED IN CONFIG, NOT HERE
-------------------------------------------
``config.S1_ROUTE`` picks one of the verified routes and this module implements each behind the
same interface, so the route can change without touching anything downstream:

  "stats"   Aggregated backscatter statistics per cell per date pulled from a server-side
            statistics/processing API.  If the free tier covers the AOI this is the right answer
            by an order of magnitude: kilobytes per cell instead of gigabytes per scene, no
            terrain correction of our own, and no standing disk.  The cost is a dependency on a
            hosted service and a quota, both recorded in config.
  "rtc"     Analysis-ready radiometrically-terrain-corrected products (on-demand or published).
            Removes the hardest part of the processing — terrain correction over Java's volcanic
            topography — at the price of a job queue and a credit budget.
  "grd"     Raw GRD, calibrated and terrain-corrected locally.  Full control, no quota, and the
            only route with no external dependency; also the only route that cannot cover the
            whole reporting scope inside the disk budget, so it applies to ``SCOPE_DEEP`` only.

Whichever route is used, the manifest records it per acquisition, and the dashboard states it.

WHAT MUST BE PRESERVED WHATEVER THE ROUTE
-----------------------------------------
  * ORBIT SEPARATION.  Backscatter depends on incidence angle, so ascending and descending
    passes — and different relative orbits — are NOT interchangeable.  Mixing them into one time
    series produces a sawtooth that looks like phenology and is not.  Series are built per
    relative orbit and only combined after normalisation (``backscatter.py``).
  * gamma0 in dB, VV and VH separately plus the VH/VV ratio.  VH is the workhorse for rice —
    volume scattering from the canopy is what rises through tillering — but the flooding minimum
    is clearest in VV.
  * the acquisition's own metadata: relative orbit, pass direction, incidence angle, platform
    (S1A vs S1C).  The S1B failure in 2021 and the S1C ramp-up change revisit frequency mid-record
    and that shows up as a change in temporal sampling, not as a change in the crop.

RESUMABILITY AND DISK
---------------------
Resumable per (relative orbit, acquisition date).  Free disk is checked before every acquisition
and the stage exits 0 below the floor.  Under the "grd" route the raw scene is deleted as soon as
the per-cell aggregates are written — the house rule from Case E — so standing disk stays near
the aggregate size rather than the archive size.
"""

from __future__ import annotations

import argparse

import config
import util
from util import log


def search(aoi, start: str, end: str):
    """Catalogue search for the AOI and window; returns one row per acquisition.

    Records relative orbit, pass direction and platform for every hit.  A cell whose orbit
    coverage changes mid-record is flagged here, not discovered later in the phenology curve.
    """
    raise NotImplementedError


def pull_stats(aoi, dates):
    """Route "stats": server-side aggregation to per-cell gamma0 statistics per date."""
    raise NotImplementedError


def pull_rtc(scenes):
    """Route "rtc": request or fetch analysis-ready terrain-corrected products."""
    raise NotImplementedError


def pull_grd(scenes):
    """Route "grd": download GRD, calibrate to gamma0, terrain-correct, aggregate, delete raw.

    Terrain correction is the step that cannot be skipped over Java: the island is a chain of
    volcanoes, and uncorrected backscatter on a slope is dominated by geometry rather than by
    what is growing on it.  The paddy itself is flat, which is why the rice mask (stage 2) also
    functions as a slope filter and keeps this step tractable.
    """
    raise NotImplementedError


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orbit", type=int, default=None, help="process one relative orbit only")
    ap.add_argument("--year", type=int, default=None)
    args = ap.parse_args()
    log(f"ingest_sar: not implemented (scaffold) route={config.S1_ROUTE} "
        f"orbit={args.orbit} year={args.year}")
    util.guard_disk(need_gb=config.SAR_CHUNK_GB)


if __name__ == "__main__":
    main()
