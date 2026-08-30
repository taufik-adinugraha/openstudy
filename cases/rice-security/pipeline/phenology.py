"""Stage 6 · phenology — the crop calendar, read out of the radar curve.

THE PHYSICS, IN THREE SENTENCES — because the dashboard has to explain it and so does the code
-------------------------------------------------------------------------------------------
A paddy field about to be transplanted is flooded: a smooth water surface is a specular reflector
that sends the radar pulse away from the satellite, so backscatter collapses to a sharp minimum
(strongest in VV, typically ``config.FLOOD_DB`` and below).  As the crop tillers, the canopy
becomes a volume of vertical scatterers standing in water, and VH backscatter climbs steeply —
several dB over a few weeks — to a maximum around heading.  After heading the canopy dries, the
crop is cut and the field is bare or re-flooded, and backscatter drops again.  That
minimum-then-steep-rise-then-peak sequence is close to unique to flooded rice, which is why radar
rice mapping works at all and why it works through the exact monsoon cloud that makes optical
methods fail in this country.

DETECTION
---------
For each cell and each candidate season:

  1. find local minima in VV below ``config.FLOOD_DB`` -> candidate transplanting dates
  2. require a rise in VH of at least ``config.RISE_DB`` within ``config.RISE_WINDOW_DAYS`` after
     the minimum -> confirms a growing crop rather than a puddle
  3. locate the following VH maximum -> heading
  4. harvest = heading + ``config.HEAD_TO_HARVEST_DAYS``, refined by the subsequent VH drop where
     one is observable before the next flooding
  5. reject any event whose defining dates fall inside an observation gap longer than
     ``config.MAX_GAP_DAYS`` — an undated event is better than a confidently wrong one

CONFUSERS, NAMED
----------------
Aquaculture ponds, tidal flats, reservoirs and freshly-ploughed wet fields all produce a
low-backscatter minimum.  Only rice produces the minimum FOLLOWED BY the steep VH rise, which is
why step 2 is a requirement and not a refinement.  Permanent water is removed by requiring the
cell to leave the low state at all.  Sugarcane and some vegetables can mimic the rise; the rice
mask prior (stage 2) is what separates them, and the residual confusion is a stated error term
rather than an assumed zero.

CROPPING INTENSITY FALLS OUT FREE
---------------------------------
The number of detected cycles per calendar year IS the cropping intensity — 1 for a rainfed
single crop, 2 for the standard irrigated pattern, 3 in the best-served irrigation commands.
Gate G-I3 checks that these land where agronomy says they should, which is a real independent
test of the detector that costs nothing extra.

OUTPUT: data/phenology.parquet — cell x season x {transplant_date, heading_date, harvest_date,
confidence, n_obs, max_gap_days, rejected_reason}.
"""

from __future__ import annotations

import config
import util
from util import log


def detect_cycles(curve):
    """Run the five-step detector on one cell's smoothed VV/VH curves. Returns candidate cycles."""
    raise NotImplementedError


def score_confidence(cycle, curve):
    """Confidence from rise amplitude, minimum depth, observation density and gap structure.

    Reported per cell and carried all the way to the map, so a low-confidence district is drawn
    as low-confidence rather than being averaged into a national number that looks certain.
    """
    raise NotImplementedError


def cropping_intensity(cycles):
    """Detected cycles per calendar year per cell — the free plausibility test (gate G-I3)."""
    raise NotImplementedError


def main() -> None:
    log("phenology: not implemented (scaffold)")
    util.guard_disk()


if __name__ == "__main__":
    main()
