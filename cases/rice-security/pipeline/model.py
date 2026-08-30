"""Stage 8 · model — calibration to KSA, and the forward-looking harvest-timing prediction.

TWO MODELS, AND THE ORDER MATTERS
---------------------------------
1. CALIBRATION.  The detector produces hectares; BPS KSA produces hectares; they will not be
   equal.  A single national scale factor would make the headline number agree and teach us
   nothing.  Instead a small, interpretable regression maps detected harvested area to KSA per
   kabupaten with cropping intensity, irrigation share and observation density as covariates, so
   the residual structure is visible: *where* we over- or under-detect, and against what.  The
   calibration is fitted on ``config.CAL_YEARS`` and never on the hold-out.  If the fitted
   coefficients are implausible the honest response is to report the uncalibrated comparison as
   well, and both are exported.

2. TIMING PREDICTION.  The commercially interesting output is not last year's area, which BPS
   already publishes — it is *when this season's harvest will land and how big it will be*, said
   before BPS says it.  Transplanting is detected in near-real time and harvest follows it by a
   crop duration modulated by variety, temperature (growing-degree days) and water stress, so a
   prediction issued at transplanting has roughly ``config.LEAD_WEEKS`` weeks of lead over the
   official monthly release.  That lead is the product.

VALIDATION IS BY TIME, NOT BY RANDOM SPLIT
------------------------------------------
The final season in the record (``config.HOLDOUT_SEASON``) is held out entirely.  Random
cross-validation over a spatio-temporally autocorrelated panel would produce a flattering number
that has nothing to do with forecasting.  The hold-out is scored once, and reported whether or
not it flatters.

WHAT IS DELIBERATELY NOT MODELLED
---------------------------------
Yield.  Radar backscatter carries some yield signal but nothing like enough to claim tonnage at
kabupaten level, and Indonesian rice production is a politically live number.  Production is
therefore reported as *our harvested area x BPS's published productivity*, with the arithmetic
shown, and it is labelled as exactly that.  Claiming an independent production estimate would be
the single fastest way to lose a technical client's trust.

OUTPUT: data/model.parquet (region x season: detected, calibrated, predicted, intervals),
data/model_meta.json (coefficients, residual diagnostics, hold-out scores, lead achieved).
"""

from __future__ import annotations

import config
import util
from util import log


def calibrate(detected, ksa):
    """Fit the interpretable detected -> KSA mapping on ``config.CAL_YEARS``."""
    raise NotImplementedError


def predict_timing(phenology, climate):
    """Harvest date and volume from transplanting date + GDD accumulation + water stress."""
    raise NotImplementedError


def holdout_score(model):
    """Score once on ``config.HOLDOUT_SEASON``; report regardless of the result."""
    raise NotImplementedError


def production_from_area(area, bps_productivity):
    """Our area x BPS productivity, with the arithmetic exported so the page can show it.

    Not an independent production estimate, and never presented as one.
    """
    raise NotImplementedError


def main() -> None:
    log("model: not implemented (scaffold)")
    util.guard_disk()


if __name__ == "__main__":
    main()
