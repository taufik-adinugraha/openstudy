"""Stage 10 · export — view-models for the dashboard, small enough to be interactive.

BUDGET
------
Total payload under ``config.WEB_BUDGET_MB`` for the first paint; anything per-cell or per-date
is a separate lazily-fetched file keyed by the URL state.  Aggregates and pre-simplified geometry
only — never raw rows (quality gate 4).

THE SIGNATURE INTERACTION HAS A DATA CONTRACT
---------------------------------------------
"The radar probe" needs, for any probed location, the real backscatter curve with its detected
events, in under 300 ms and with no server.  So the curves are pre-exported on a coarse probe
lattice (``config.PROBE_STEP_M``) as a compact binary-ish JSON — dates as an index into one
shared date array, values as quantised integers in tenths of a dB — and the browser interpolates
between lattice points only for the map cursor, never for the curve it draws.  Rough sizing at
the deep scope is a few hundred kB per province, which is why the lattice step is a config
constant and not an afterthought: it is the one number that decides whether the case's best
moment is instant or laggy.

NaN DISCIPLINE
--------------
``json.dumps`` writes bare ``NaN``, which is not valid JSON: some browsers parse it, others
reject it, and the resulting bug appears on one machine only.  Everything goes through a
sanitiser and the exporter asserts the output round-trips through a strict parser.

FILES
-----
summary.json          headline numbers, gate table, vintages, licences — imported at build time
probe/<tile>.json     the probe lattice curves + detected events (the signature interaction)
wave/<year>.json      kecamatan phenological stage per week — the harvest-wave frames
area.json             region x month planted/harvested with intervals, and the BPS KSA series
                      beside it (drawn in --official teal wherever it appears, per the house rule)
calendar.json         planting/harvest date anomalies against onset, ONI and DMI
adm.json              simplified kecamatan/kabupaten geometry (shared arcs)

Every file carries its own ``vintage`` block: Sentinel-1 last acquisition, BPS release date and
table id, CHIRPS/ERA5 cutoffs.
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


def write_probe_lattice():
    """Quantised backscatter curves + detected events on the probe lattice. See docstring."""
    raise NotImplementedError


def write_wave_frames():
    """Per-week kecamatan phenological stage, byte-quantised — the harvest-wave scrub."""
    raise NotImplementedError


def write_area():
    raise NotImplementedError


def main() -> None:
    log("export_web: not implemented (scaffold) ->", config.WEB_DATA)
    util.guard_disk()


if __name__ == "__main__":
    main()
