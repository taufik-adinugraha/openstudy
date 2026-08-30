"""Stage 10 · export — view-models for the dashboard, small enough to be interactive.

BUDGET
------
Total payload under ``config.WEB_BUDGET_MB`` for the first paint; anything per-receptor or
per-day is a separate lazily-fetched file keyed by the URL state, so scrubbing a year of
trajectories never blocks on a single monolithic download.  The browser gets aggregates and
pre-simplified geometry, never raw rows — the house rule from quality gate 4.

NaN DISCIPLINE
--------------
``json.dumps`` writes bare ``NaN``, which is not valid JSON and which some browsers parse and
others reject, producing a bug that only appears on one machine.  Every frame goes through a
sanitiser that converts NaN/Inf to ``null`` before serialisation, and the exporter asserts the
output round-trips through a strict parser.

FILES
-----
summary.json          headline numbers, gate table, vintages, licences — imported at build time
risk_days/<date>.json risk surface per day, quantised to a byte per cell
traj/<run_id>.json    one trajectory ensemble, simplified with Douglas-Peucker at a tolerance
                      that is visually lossless at the map's maximum zoom
receptors.json        per-receptor observed vs modelled series, with the per-province attribution
fires/<year>.json     hotspot points, thinned by FRP rank per cell per week so the map stays
                      readable and honest about what was dropped
adm.json              simplified ADM1/ADM2 geometry (TopoJSON-style shared arcs)

Every file carries its own ``vintage`` block: FIRMS source and last acquisition date, ERA5T
cutoff, ground-sensor last reading.  A "live" claim without a visible timestamp fails gate 5.
"""

from __future__ import annotations

import config
import util
from util import log


def sanitise(obj):
    """Recursively replace NaN/Inf with None so the output is strict-parseable JSON."""
    raise NotImplementedError


def write_summary():
    raise NotImplementedError


def write_risk_days():
    raise NotImplementedError


def write_trajectories():
    """Pre-compute the ensembles the hero and Explore can request, forward and backward.

    The signature interaction ("blame the wind") must respond in well under 300 ms, so the
    trajectory sets for every receptor x episode-day pair are precomputed here rather than
    integrated in the browser.  The set is bounded by ``config.EXPORT_EPISODE_DAYS``.
    """
    raise NotImplementedError


def write_receptors():
    raise NotImplementedError


def main() -> None:
    log("export_web: not implemented (scaffold) ->", config.WEB_DATA)
    util.guard_disk()


if __name__ == "__main__":
    main()
