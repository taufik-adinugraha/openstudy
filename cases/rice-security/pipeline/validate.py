"""Stage 9 · validate — gates G-I1..G-I5, written before any result was seen.

Thresholds live in ``config`` and were fixed at spec time.  A gate that fails is published red
with a diagnosis (the house precedent: Case C's G-C5 and Case H's G-H1 both ship failing and
explained).  Thresholds are never moved to fit an outcome.

G-I1  KSA reconciliation (HARD).  Provincial annual harvested area within
      ``config.GATE_PROV_PCT`` of BPS KSA for every year in the record; at kabupaten level,
      R^2 >= ``config.GATE_KAB_R2`` and MAPE <= ``config.GATE_KAB_MAPE``.  Evaluated on the
      UNCALIBRATED series as well as the calibrated one, because a gate applied only after
      fitting to the benchmark is not a gate.  The pre-2018 eye-estimate regime is excluded:
      comparing against a series BPS itself replaced would be measuring the wrong thing.

G-I2  Harvest-timing accuracy (HARD).  Median absolute error of the modelled peak-harvest week
      against the BPS monthly provincial series <= ``config.GATE_TIMING_WEEKS`` weeks, and no
      province biased by more than ``config.GATE_TIMING_BIAS_WEEKS`` weeks.  This is the gate
      that matters: area we can approximately reproduce, timing is what nobody publishes early.

G-I3  Cropping-intensity plausibility.  Detected cycles per year land inside
      ``config.GATE_CI_IRRIGATED`` for the irrigated lowland kabupaten and below
      ``config.GATE_CI_RAINFED`` for the rainfed ones.  An independent agronomic sanity check on
      the detector that costs nothing.

G-I4  Independent rice-mask agreement.  Our detected paddy extent agrees with the external rice
      map on >= ``config.GATE_MASK_AGREE`` of its area.  Agreement is MEASURED, not assumed, and
      the disagreement is mapped: the mask is a prior, and the places where we differ from a
      published product are the interesting part, not an embarrassment.

G-I5  Temporal hold-out.  ``config.HOLDOUT_SEASON`` is excluded from calibration entirely and
      G-I1 and G-I2 are re-reported on it before any tuning.  In-sample agreement with a
      benchmark you fitted to is not evidence.

OUTPUT: data/stats.json — every gate with threshold, computed value, pass/fail and a one-line
diagnosis when it fails.  The dashboard renders this table verbatim.
"""

from __future__ import annotations

import config
import util
from util import log


def g_i1_ksa_reconciliation():
    raise NotImplementedError


def g_i2_harvest_timing():
    raise NotImplementedError


def g_i3_cropping_intensity():
    raise NotImplementedError


def g_i4_mask_agreement():
    raise NotImplementedError


def g_i5_holdout():
    raise NotImplementedError


def main() -> None:
    log("validate: not implemented (scaffold) — gates", ", ".join(config.GATES))
    util.guard_disk()


if __name__ == "__main__":
    main()
