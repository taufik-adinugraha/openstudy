"""Stage 5b · cams — the chemistry benchmark, the plume heights, and the surrogate ground truth.

Three products, three distinct jobs, one Copernicus store (ADS).  The existing ECMWF token
authenticates; a 403 reading "user didn't accept all required site policies" is a ONE-TIME
BROWSER CLICK on the ADS terms, not a credential problem.  Anonymous requests return 401, which
is how we know the token is doing the work.

1. ``cams-global-atmospheric-composition-forecasts`` (2015-01-01 -> today, lead 0-120 h)
   A real chemistry-transport forecast of PM2.5 and aerosol optical depth, covering both anchor
   years.  This is the honest way to position a kinematic trajectory model: show it NEXT TO a
   CTM rather than implying it is one.  Where we agree with CAMS, the cheap model is doing its
   job; where we diverge, the divergence is the finding.  A demo that beats CAMS at nothing but
   is transparent about what it is will survive a technical audience; one that quietly implies
   it is a CTM will not.

2. ``cams-global-reanalysis-eac4`` (2003-01-01 -> 2025-12-31)
   PM2.5 at receptors that have never had a sensor — Pekanbaru, Palangkaraya, Pontianak — and
   the only reference that reaches 2015 at all, since Singapore's NEA history starts 2016-03.
   Consumed by ``ingest_ground.py`` as tier-3 surrogate truth, flagged as a model on every row.

3. ``cams-global-fire-emissions-gfas`` (2003-01-01 -> 2025-12-03)
   ** The find that upgrades the transport model. **  GFAS converts fire radiative power into
   emissions and — critically — publishes ``injection_height``, ``altitude_of_plume_top`` and
   ``altitude_of_plume_bottom``.  Plume height is the single largest uncertainty in this case:
   smoke injected at 500 m stays in the boundary layer and settles locally, smoke at 2,000 m
   joins the 850 hPa flow and crosses the Strait.  So where GFAS exists, the trajectory model
   releases parcels at a PUBLISHED injection height instead of at our own crude parameterisation.
   It ends 2025-12-03, so it is a training and backtest layer, not an operational one, and every
   run records which height source it used.

SIZE
----
The AOI is small at CAMS resolution (~0.4 deg forecasts, ~0.75 deg EAC4), so these are the
cheapest heavy-sounding sources in the case.  Sharded by year like ERA5 because the ADS queue
behaves the same way.

OUTPUT: data/cams_forecast.parquet, data/cams_eac4.parquet, data/gfas.parquet
"""

from __future__ import annotations

import argparse

import config
import util
from util import log


def retrieve(dataset: str, variables: list[str], year: int, month: int, api_url: str):
    """One ADS retrieve for one month.  Same cdsapi client, different endpoint URL and token."""
    raise NotImplementedError


def forecast_benchmark():
    """CAMS PM2.5/AOD forecasts, aligned to our own lead times so the comparison is like for like.

    Aligned by ISSUE time, not by valid time: comparing our day-3 forecast against CAMS's
    analysis would flatter us, and comparing it against CAMS's day-3 lead is the real test.
    """
    raise NotImplementedError


def eac4_surrogate():
    """EAC4 PM2.5 extracted at the tier-3 receptor coordinates."""
    raise NotImplementedError


def gfas_injection():
    """GFAS emissions + injection height / plume top / plume bottom on the model grid.

    Where a fire cell has GFAS coverage the transport stage uses these heights directly; where it
    does not, it falls back to ``config.PLUME_RISE`` and flags the run.  The fallback share is
    reported, because a trajectory driven by a guessed height and one driven by a published
    height do not deserve the same confidence.
    """
    raise NotImplementedError


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", choices=("forecast", "eac4", "gfas"), default=None)
    args = ap.parse_args()
    log("ingest_cams: not implemented (scaffold)", args.product or "all")
    util.require(bool(config.CDS_API_KEY), "CDS_API_KEY missing from repo-root .env")
    log("ADS policy clicks outstanding?", "; ".join(config.POLICY_CLICKS))


if __name__ == "__main__":
    main()
