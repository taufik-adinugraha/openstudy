"""Stage 8 · transport — where the smoke goes, and where it came from.

WHAT THIS IS, STATED PLAINLY
----------------------------
A kinematic trajectory model driven by ERA5 winds.  Parcels are released above each fire,
advected on the blended ``config.TRAJ_LEVELS`` wind field, and their positions integrated
forward (or backward) for ``config.TRAJ_HOURS`` hours.  That is all it is.

What it is NOT, and the methodology page says so in these words:

  * It is not a chemistry-transport model.  There is no chemistry, no aerosol microphysics, no
    secondary organic aerosol formation, no wet or dry deposition beyond a crude exponential
    decay, and no aerosol-radiation feedback (which in 2015 was strong enough to suppress the
    boundary layer and make the haze worse than the emissions alone imply).
  * It is not a dispersion model.  A single trajectory is a line, not a plume; plume width comes
    from releasing an ensemble (``config.TRAJ_ENSEMBLE`` parcels jittered in release height and
    time) and reading its spread, which is a proxy for dispersion, not a simulation of it.
  * Vertical motion comes from ERA5 omega, and injection height — historically the single largest
    source of error, because a plume at 500 m and one at 2,000 m go to different countries — is
    taken from **CAMS GFAS**, which publishes ``injection_height``, plume top and plume bottom
    per fire.  That replaces the crudest parameterisation in this case with a published product.
    GFAS ends 2025-12-03, so the operational tail falls back to ``config.PLUME_RISE`` and every
    run records which height source it used and what share of parcels used the fallback.

The honest claim is therefore "which fires were upwind" and "which receptors are downwind", at
daily resolution, with a stated direction error — not "PM2.5 will be 87 ug/m3 in Singapore on
Thursday".  Everything the dashboard says is bounded by that.  And because CAMS publishes a real
chemistry-transport forecast covering both anchor years, the trajectory result is shown **next to
a CTM** rather than in place of one.  Where the cheap model agrees with CAMS it is doing its job;
where it diverges, the divergence is the finding.  That is a far stronger position than implying
a trajectory model is something it is not.

TWO DIRECTIONS, ONE ENGINE
--------------------------
forward   release at each fire cluster weighted by summed FRP, integrate forward; a receptor's
          exposure on day t is the FRP-weighted count of parcels within ``config.RECEPTOR_KM``
          of it, decayed by travel time.
backward  release at a receptor on a chosen day, integrate backward; the parcels land on the
          cells the air came from, and intersecting them with that period's fires produces the
          attribution — the "blame the wind" interaction, and the commercially interesting half.

The two share the integrator; only the sign of the timestep and the release points differ.  That
is deliberate: a back-trajectory that disagrees with the forward run over the same episode is a
bug, and gate G-J3 checks exactly that consistency.

OUTPUT: data/trajectories.parquet (run_id, direction, release_day, step_hour, lat, lon, weight),
data/receptor_exposure.parquet (receptor x day: modelled exposure, contributing province shares).
"""

from __future__ import annotations

import config
import util
from util import log


def wind_field(day):
    """Blend the ``config.TRAJ_LEVELS`` winds into one steering field for the day.

    Weights are fixed in config, not fitted — a fitted blend would be tuned on the very episodes
    the gates then score, and the resulting agreement would prove nothing.
    """
    raise NotImplementedError


def integrate(release_points, day, direction: int, hours: int):
    """Advect parcels with a second-order (Petterssen) scheme on hourly winds.

    First-order Euler drifts badly over 72 hours in curved flow — the iterative corrector is
    cheap and is the difference between a trajectory that lands on Singapore and one that lands
    in the Java Sea.  Parcels leaving the ERA5 domain are terminated and counted, never clamped
    to the boundary (a clamped parcel piles up on the edge and invents a source region).
    """
    raise NotImplementedError


def injection_height(cell, day, gfas):
    """Release height for a parcel: GFAS where it exists, ``config.PLUME_RISE`` where it does not.

    Returns ``(height_m, source)`` so the fallback share can be reported.  Even with GFAS the
    ensemble is released across a spread rather than a single height, and that spread is carried
    into the exposure estimate as an uncertainty band on the dashboard.
    """
    raise NotImplementedError


def compare_cams(exposure, cams):
    """Trajectory exposure against the CAMS chemistry forecast, aligned by ISSUE time.

    Aligning by valid time instead would compare our day-3 forecast against CAMS's analysis and
    flatter us.  Published as a divergence chart, not as a score we claim to win.
    """
    raise NotImplementedError


def receptor_exposure(traj, fires):
    """FRP-weighted, travel-time-decayed parcel density within reach of each receptor."""
    raise NotImplementedError


def attribute(receptor: str, day: str):
    """Back-trajectory attribution: which provinces' fires the air passed over.

    Returns a province share vector with a stated confidence, and — importantly — an explicit
    "no attributable source" outcome when the back-trajectory passes over no fire at all, which
    is the correct answer on the many bad-air days that are local, not transboundary.
    """
    raise NotImplementedError


def main() -> None:
    log("transport: not implemented (scaffold)")
    util.guard_disk()


if __name__ == "__main__":
    main()
