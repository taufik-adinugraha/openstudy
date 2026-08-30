"""Stage 5 · backscatter — raw acquisitions into one clean, evenly-sampled curve per cell.

This is where most of the accuracy of the whole case is won or lost, and none of it is glamorous.

SPECKLE
-------
SAR is coherent imaging, so every single-look pixel carries multiplicative speckle with a
standard deviation of the same order as the signal.  A per-pixel time series is unreadable.  The
fix is spatial multi-looking within the analysis cell (``config.CELL_M``, chosen so each cell
holds enough looks that the speckle standard error falls below the phenological signal we are
trying to detect) and, crucially, taking the MEAN IN LINEAR POWER and converting to dB
afterwards.  Averaging dB values is averaging logarithms and biases every cell low by an amount
that depends on its own variance — a bias that correlates with land cover and therefore looks
exactly like a real spatial pattern.

INCIDENCE-ANGLE AND ORBIT NORMALISATION
---------------------------------------
Series are built per relative orbit (see ingest_sar).  Combining orbits requires normalising for
incidence angle; the correction is fitted per land-cover class on stable targets over the dry
season, when the surface is not changing, so the fit cannot absorb crop signal.  Cells whose
orbit mix changes mid-record are marked and excluded from timing statistics rather than being
quietly corrected.

RESAMPLING
----------
Sentinel-1 revisit is nominally 12 days per orbit but is not uniform: overlapping orbits give
some cells better sampling, and the constellation's composition changed during the record.  The
curve is therefore interpolated to a regular ``config.STEP_DAYS`` grid with the ORIGINAL
observation dates retained alongside, so the phenology stage can refuse to date an event that
falls inside a gap longer than ``config.MAX_GAP_DAYS`` instead of inventing one.

SMOOTHING
---------
Savitzky-Golay over the resampled series (``config.SG_WINDOW``, ``config.SG_ORDER``): it
preserves peak position and amplitude, which is exactly what a moving average destroys and
exactly what this case measures.  Both the raw and the smoothed series are kept — the dashboard's
signature interaction draws the RAW points under the smoothed curve, because a client who can see
the noise trusts the fit.

OUTPUT: data/backscatter/<tile>.parquet — cell x date x {vv_db, vh_db, ratio_db, n_looks,
rel_orbit, is_interpolated}.
"""

from __future__ import annotations

import config
import util
from util import log


def multilook(scene_cells):
    """Spatial mean IN LINEAR POWER within each analysis cell, then to dB. See docstring."""
    raise NotImplementedError


def normalise_incidence(series, landcover):
    """Fit and apply the incidence-angle correction per land-cover class on dry-season stables."""
    raise NotImplementedError


def resample(series):
    """Interpolate to the regular ``config.STEP_DAYS`` grid; flag every interpolated point and
    every gap longer than ``config.MAX_GAP_DAYS``."""
    raise NotImplementedError


def smooth(series):
    """Savitzky-Golay filter; returns the smoothed curve beside the raw observations."""
    raise NotImplementedError


def main() -> None:
    log("backscatter: not implemented (scaffold)")
    util.guard_disk()


if __name__ == "__main__":
    main()
