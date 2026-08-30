"""Stage 7 · area — cells and dates into planted and harvested hectares per region per month.

THE DEFINITION THAT DECIDES WHETHER THE BENCHMARK IS FAIR
---------------------------------------------------------
"Harvested area" in Indonesian statistics is a FLOW, not a stock: a field that yields three crops
in a year contributes its physical area three times.  A satellite product that reports paddy
extent — the stock — and calls it harvested area will disagree with BPS by roughly the cropping
intensity, i.e. by a factor of two, and the disagreement will be blamed on the satellite.  So
this stage counts DETECTED HARVEST EVENTS, each contributing its cell's geodetic area to the
month in which it falls, and the two quantities are named separately and shown separately:

  paddy_extent_ha    the stock — physical area that grew rice at least once in the year
  harvested_ha       the flow — the sum over detected harvest events, comparable to BPS KSA
  planted_ha         the same flow indexed by transplanting date, which leads harvested by
                     roughly one crop duration and is the actually-useful early warning

Getting this wrong is the most likely way for the whole case to produce a confident wrong number,
which is why it has its own stage and its own paragraph on the methodology page.

AREA ARITHMETIC
---------------
Cell areas are geodetic.  Partial cells at kecamatan boundaries are apportioned by their
intersection fraction rather than assigned whole to the majority region — Java's districts are
small and whole-cell assignment produces visible, systematic edge artefacts on exactly the map
the client will look at first.

UNCERTAINTY
-----------
Each monthly total carries an interval derived from (a) the per-cell detection confidence from
stage 6, (b) the mask sensitivity — the total recomputed on the un-dilated and dilated masks, and
(c) the observation-gap fraction.  A point estimate without an interval is not a measurement, and
this is a case whose entire pitch is measurement.

OUTPUT: data/area_month.parquet (region x year x month x {planted_ha, harvested_ha, lo, hi}),
data/area_season.parquet, data/extent_year.parquet.
"""

from __future__ import annotations

import config
import util
from util import log


def to_region(phenology, cells):
    """Apportion cell events to kecamatan and kabupaten by intersection fraction."""
    raise NotImplementedError


def monthly(events):
    """Harvest and planting flows per region per month, with the confidence-weighted interval."""
    raise NotImplementedError


def extent(events):
    """Annual paddy extent — the STOCK.  Named distinctly everywhere it appears."""
    raise NotImplementedError


def main() -> None:
    log("area: not implemented (scaffold)")
    util.guard_disk()


if __name__ == "__main__":
    main()
