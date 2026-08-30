"""Stage 6 · features — the daily cell panel the risk model learns from.

UNIT OF ANALYSIS
----------------
One row per (0.25 deg cell, day) over the AOI and the modelling window.  0.25 deg is not a
compromise, it is ERA5's native grid: a finer model grid would be interpolation dressed up as
resolution, and the honest statement is that ignition risk is predicted at the scale at which
the weather is actually known.  Fire *locations* are reported at their native 375 m; risk is
reported at 0.25 deg, and the page never blurs the two.

FEATURE FAMILIES
----------------
weather      daily max temperature, min relative humidity, VPD, wind speed, precipitation,
             consecutive dry days, boundary-layer height
drought      SPI-1/3/6, KBDI, soil moisture (ERA5 layers 1-2), days since last wet day
fuel         peat fraction, peat depth where available, land-cover class fractions, primary
             forest fraction, distance to the nearest previous-year burn scar
pressure     ONI, DMI, SOI (with their interpolation flags)
calendar     day of year encoded as sin/cos, NOT as a raw integer — a tree splitting on
             "day > 250" learns the fire season as a cliff and cannot generalise across the
             year boundary
history      hotspot counts in the same cell over the trailing 7/30/365 days, and in the
             8 neighbouring cells — fire begets fire, and leaving this out makes the model look
             better than it is on paper and worse in use

LEAKAGE RULES (the part that decides whether the AUC means anything)
-------------------------------------------------------------------
  * every feature at day t uses data available at day t-1 or earlier.  ERA5 is a reanalysis and
    therefore knows the future; a forecast that consumes same-day reanalysis is not a forecast.
    ``config.FEATURE_LAG_DAYS`` enforces the shift and the spec states the consequence: in
    operation the same features would come from an ECMWF/GFS forecast, and the published skill
    is an upper bound on operational skill.  Say it, do not hide it.
  * splits are BLOCKED BY SEASON, not random.  Adjacent days in the same cell are almost the
    same row; a random split leaks and produces a beautiful, meaningless AUC.
  * the 2015 and 2019 seasons are removed from training entirely and reserved for gate G-J5.

TARGET
------
Binary: at least one retained hotspot in the cell on day t.  A count target is tempting but the
count is dominated by detection geometry (swath overlap, cloud, satellite overpass time) rather
than by fire behaviour, so the binary target is the one that means what it says.

OUTPUT: data/panel.parquet
"""

from __future__ import annotations

import config
import util
from util import log


def build_panel():
    """Join weather, drought, fuel, pressure, calendar and history onto the cell-day skeleton."""
    raise NotImplementedError


def add_history(panel, fires_daily):
    """Trailing hotspot counts in-cell and in the 8-neighbourhood, strictly lagged."""
    raise NotImplementedError


def apply_lag(panel):
    """Shift every predictor by ``config.FEATURE_LAG_DAYS`` and assert no same-day column
    survives.  A unit test on this function is worth more than any amount of model tuning."""
    raise NotImplementedError


def season_blocks(panel):
    """Label each row with its fire season (July-October of year Y) for blocked CV, and mark
    the ``config.ANCHOR_YEARS`` rows as held out."""
    raise NotImplementedError


def main() -> None:
    log("features: not implemented (scaffold)")
    util.guard_disk()


if __name__ == "__main__":
    main()
