"""Stage 4 · matrix — origin → destination travel times.

Origins: population-weighted centroids of every Jabodetabek kelurahan/desa (~1,600).
Destinations: a 500 m hex grid over the built-up area (~12,000 cells) carrying the jobs proxy
and population, plus every hospital / puskesmas point.
Departure window: weekday 07:00-09:00, one departure per minute (r5py TravelTimeMatrixComputer,
percentiles 50 and 75); modes WALK + TRANSIT (all), and separately TRANSIT without rail, and
WALK-only, so chapter 3 can show what each mode layer adds. Max trip 90 min; 60/45/30-minute
cutoffs applied downstream.

Compute: 1,600 × 12,000 × 3 scenarios ≈ 58 M cells; r5 handles this in ~20-40 min on 8 cores.
Outputs: data/matrix.parquet (origin_id, dest_id, scenario, tt_p50, tt_p75) ≈ 300 MB.
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO matrix →", config.MATRIX, "| window:", config.DEPARTURE_WINDOW, "| cutoffs:", config.CUTOFFS_MIN)


if __name__ == "__main__":
    main()
