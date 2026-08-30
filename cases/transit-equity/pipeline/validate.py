"""Stage 7 · validate — gates G-G1..G-G4 (numbers in config.py; prose in spec §G4).

G-G1  Timetable sanity: for the sample OD pairs in config.VALIDATION_OD (corridor end-to-end
      runs with published TransJakarta journey times, MRT Lebak Bulus→Bundaran HI, KRL
      Bogor→Jakarta Kota), r5 in-vehicle time within ±GATE_TT_TOL_PCT (15 %) or
      ±GATE_TT_TOL_MIN (8 min), whichever is larger.
G-G2  External routing check: for 50 random OD pairs, r5 door-to-door p50 vs Google Routes
      transit time (on-the-fly only, never stored raw — Maps ToS): median absolute deviation
      ≤ GATE_GOOGLE_MAD_MIN (10 min); the distribution is charted in the methodology.
G-G3  Network integrity: every GTFS stop snaps to the street graph within 200 m (≥ 98 %);
      no kelurahan origin is unreachable in the WALK+TRANSIT scenario.
G-G4  Plausibility: DKI core kelurahan have higher 60-min jobs access than Bodetabek periphery
      (population-weighted medians ordered as expected); rail scenario ≥ no-rail scenario
      everywhere (monotonicity — a bookkeeping test); and the ITDP People-Near-Transit
      replication (share of population within 1 km of ≤15-min-headway transit) lands ABOVE
      the 2016 anchors (Jakarta 44 %, Greater Jakarta 16 %) — the network has only grown
      (MRT, LRT, Mikrotrans) — with the comparison charted, not asserted.

Writes data/stats.json; hard gates G-G1, G-G3, G-G4 fail the build, G-G2 is disclosure.
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO validate →", config.STATS_JSON)


if __name__ == "__main__":
    main()
