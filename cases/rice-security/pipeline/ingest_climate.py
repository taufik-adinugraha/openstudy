"""Stage 3 · climate — rainfall, meteorology and the ocean state that move the planting date.

Rice in Java is planted when the water arrives.  In irrigated systems that is a canal schedule;
in rainfed systems it is the monsoon onset, and in an El Nino year the onset slips by weeks and
the whole crop calendar slips with it.  This is the mechanism behind chapter 04 ("the calendar is
moving") and it is also the honest explanation for most of the year-to-year variance that a
purely satellite-derived harvested area will show.

SOURCES (pinned in config)
--------------------------
CHIRPS   daily 0.05 deg rainfall, 1981-present — used to compute monsoon-onset date per cell by
         the conventional accumulation rule (``config.ONSET_RULE``: first dekad exceeding a
         rainfall threshold and not followed by a dry spell within the next N days).  The rule is
         stated because "monsoon onset" has a dozen definitions and they disagree by weeks.
ERA5     temperature (growing-degree days, and the heat-stress days that hurt grain filling),
         solar radiation, and soil moisture.  NOTE the multi-variable split-response gotcha: CDS
         returns instantaneous and accumulated fields as SEPARATE NetCDFs and naive concatenation
         leaves precipitation and radiation as all-NaN columns that a model silently drops.
         Open them separately and join on the grid key; assert non-null before writing.
ENSO/IOD ONI and DMI, monthly, forward-filled with an interpolation flag.

OUTPUT: data/chirps.parquet (cell x day rainfall, onset date per cell per season),
data/era5.parquet (cell x day met), data/enso.parquet.
"""

from __future__ import annotations

import config
import util
from util import log


def chirps():
    """Stream CHIRPS dailies, clip to the AOI, reduce to the analysis grid, delete raw."""
    raise NotImplementedError


def monsoon_onset(rain):
    """Per-cell wet-season onset date under ``config.ONSET_RULE``.

    Returns the date and the rule's own diagnostics (accumulation reached, dry-spell test), so a
    cell whose onset is ambiguous is flagged rather than given a confident wrong date.
    """
    raise NotImplementedError


def era5():
    """ERA5 single levels for the AOI. See the split-NetCDF warning in the module docstring."""
    raise NotImplementedError


def ocean_indices():
    """ONI and DMI as a month-indexed table with an ``is_interpolated`` flag on the daily fill."""
    raise NotImplementedError


def main() -> None:
    log("ingest_climate: not implemented (scaffold)")
    util.require(bool(config.CDS_API_KEY), "CDS_API_KEY missing from repo-root .env")


if __name__ == "__main__":
    main()
