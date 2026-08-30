"""Stage 7 · risk — ignition probability per cell per day, at 1-7 day lead.

MODEL
-----
Gradient-boosted trees (LightGBM) on the lagged panel, one model per lead time in
``config.LEAD_DAYS``.  Trees are the right family here: the relationships are sharply
non-linear and interactive (peat AND dry AND windy is not peat + dry + wind), the features are
a mix of continuous and categorical, and the model has to be explainable to a ministry.  A
neural net would buy nothing measurable and cost the explanation.

WHAT IT IS SCORED AGAINST — AND WHY THE BASELINE IS THE WHOLE ARGUMENT
---------------------------------------------------------------------
Fire is rare and intensely seasonal, so a model predicting "the climatological probability for
this cell on this day of year" already scores an impressive-looking AUC.  Quoting AUC against a
coin flip would be dishonest.  Three baselines are computed and published beside the model:

  climatology   per-cell, per-day-of-year historical ignition rate (the real baseline)
  persistence   yesterday's state in this cell
  FWI           the Canadian Fire Weather Index from CEMS ``cems-fire-historical-v1``, thresholded
                — an EXTERNAL, operational, peer-reviewed benchmark that we did not design and
                cannot tune.  It costs ~29 MB/year over our box, which makes it the cheapest
                credibility in the whole portfolio.  Beating climatology is table stakes; beating
                FWI is the claim worth making.
  KLHK hazard   Indonesia's own official fire-hazard map (``RAWAN_KARHUTLA_AR_250K``) as a
                static-risk comparator, which is what a ministry client will actually ask about.

Gate G-J2 is stated as a Brier skill score against climatology, not as raw AUC, precisely
because raw AUC on a seasonal rare event flatters everyone.

TWO PATHS, SCORED SEPARATELY
----------------------------
The reanalysis path consumes observed ERA5 and CHIRPS; the operational path consumes CHIRPS-GEFS
forecast precipitation at the matching lead.  The gap between them is the real cost of
forecasting rather than hindcasting, and it is published rather than assumed away.  (No open
medium-range FWI forecast exists — CEMS publishes only the seasonal product — which is precisely
why a days-ahead ignition-risk forecast is a commercial opening rather than a reimplementation.)

CALIBRATION
-----------
Raw GBM probabilities are over-confident on rare events.  Isotonic calibration is fitted on a
held-out season (never on the anchor years) and the reliability diagram is published — a risk
number a ministry might act on has to mean what it says: "0.2" must burn about one time in five.

EXPLANATION
-----------
SHAP values are computed on a sample and aggregated to per-feature-family contributions per
season, so chapter 02 can say *why* a cell is red today (dry + peat + wind) rather than only
that it is.  This is the difference between a demo and something a client would pilot.

OUTPUT: data/risk.parquet (cell x day x lead: p_ignition, calibrated), data/risk_meta.json
(feature importances, SHAP family aggregates, reliability bins, baseline scores, CV folds).
"""

from __future__ import annotations

import argparse

import config
import util
from util import log


def fit(panel, lead: int):
    """Fit one LightGBM classifier for one lead time with blocked-by-season CV."""
    raise NotImplementedError


def baselines(panel):
    """Climatology, persistence and (if available) FWI — computed on the same folds."""
    raise NotImplementedError


def calibrate(model, holdout):
    """Isotonic calibration on a held-out season; returns the calibrator and reliability bins."""
    raise NotImplementedError


def explain(model, sample):
    """SHAP contributions aggregated to feature families, per season and per fuel class."""
    raise NotImplementedError


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predict-only", action="store_true",
                    help="daily refresh: score today with the stored model, do not refit")
    args = ap.parse_args()
    log("risk: not implemented (scaffold)", "predict-only" if args.predict_only else "fit")
    util.guard_disk()


if __name__ == "__main__":
    main()
