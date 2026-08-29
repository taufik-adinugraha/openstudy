"""Stages: deseason + calibrate + nowcast + anomalies (spec §A3-A4).

deseason : per-regency STL + Hijri-calendar Ramadan-overlap regressor
           (Ramadan drifts ~11 days/Gregorian year and visibly shifts lighting).
calibrate: levels cross-section log(PDRB) ~ log(SOL) per year — gate G-A1
           (R² >= 0.65 every year 2015->latest, levels ONLY per Gibson et al.
           2021); panel FE elasticity reported with CIs, displayed, not gated.
nowcast  : Pulse Index = deseasonalized 3-month radiance growth through the
           panel elasticity, with uncertainty band. NEVER labeled "GDP".
anomalies: STL-residual z-scores; risers/fallers board needs |z| >= 2
           sustained >= 2 months.
"""

from __future__ import annotations

import argparse
import sys

GATE_LEVELS_R2 = 0.65      # G-A1
GATE_NOWCAST_WIN = 0.60    # G-A2: beat naive baseline in >=60% of provinces
GATE_XSENSOR_CORR = 0.90   # G-A4: national series vs Black Marble VJ146A3


def ramadan_overlap(year: int, month: int) -> float:
    """Fraction of the Gregorian month overlapping Ramadan (hijridate)."""
    raise NotImplementedError("week 2: Hijri overlap fraction")


def deseasonalize() -> None:
    raise NotImplementedError("week 2: STL + Ramadan regressor over ledger")


def calibrate() -> dict:
    """Per-year levels OLS + panel FE. Returns gate results for validate."""
    raise NotImplementedError("week 3: calibration against BPS PDRB")


def nowcast() -> None:
    raise NotImplementedError("week 3: Pulse Index + rolling backtest 2016-2024")


def validate() -> int:
    """Run gates G-A1..G-A4; nonzero exit blocks publish."""
    raise NotImplementedError("week 3: gate evaluation, results to stats.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["deseason", "calibrate", "nowcast", "validate"])
    args = parser.parse_args()
    try:
        {"deseason": deseasonalize, "calibrate": calibrate,
         "nowcast": nowcast, "validate": validate}[args.stage]()
    except NotImplementedError as todo:
        print(f"[models:{args.stage}] STUB — not yet implemented: {todo}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
