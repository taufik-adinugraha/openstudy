"""Stage 2 · aux — boundaries and the rice reference mask.

BOUNDARIES
----------
geoBoundaries gbOpen (CC BY 4.0) for ADM1/ADM2/ADM3, with HDX COD-AB as the alternate.  GADM is
rejected outright: its licence forbids commercial use and this is a commercial demo.  Codes are
reconciled to the current BPS vintage with the name-based post-2020 recode ported from
``cases/poverty-map/pipeline/features.py``.

THE ANALYSIS UNIT, DECIDED HERE
-------------------------------
Three units, and they are not interchangeable:

  pixel / cell   where phenology is actually detected (``config.CELL_M`` metres)
  kecamatan      the map unit — fine enough that the harvest wave is visible as a wave rather
                 than as 30 blocky districts
  kabupaten      the REPORTING and VALIDATION unit, because that is the level at which BPS
                 publishes KSA harvested area.  Reporting at kecamatan would imply a precision
                 the benchmark cannot test, and gate G-I1 would become unfalsifiable.

The dashboard therefore draws kecamatan and quotes kabupaten, and says which is which.

THE RICE MASK — AND WHY IT IS A PRIOR, NOT A LABEL
--------------------------------------------------
An external rice-extent layer (``config.RICE_MASK``) restricts where phenology is even attempted,
which cuts the SAR volume enormously and removes whole classes of false positives (aquaculture
ponds, tidal flats and reservoirs all produce the same "low backscatter then high" signature that
a flooded paddy does).  But the mask is a PRIOR, not ground truth: it is a published product with
its own reference year and its own errors, and treating it as truth would mean this case simply
reproduces someone else's map.  So:

  * the mask is dilated by ``config.MASK_BUFFER_M`` so newly converted land can still be found;
  * cells inside the mask that never show a paddy signature are reported as mask false-positives
    rather than silently counted as rice;
  * gate G-I4 measures agreement with the mask instead of assuming it, and the disagreement is
    mapped, because where our answer differs from the published map IS the interesting part.

OUTPUT: data/adm.parquet (ADM1-3 with BPS codes), data/rice_mask.tif (on the analysis grid),
data/cells.parquet (the analysis cell index with its kecamatan and kabupaten codes).
"""

from __future__ import annotations

import config
import util
from util import log


def boundaries():
    """Fetch geoBoundaries gbOpen ADM1-3 for Indonesia, clip to the AOI, attach BPS codes."""
    raise NotImplementedError


def rice_mask():
    """Fetch and reproject the external rice-extent prior onto the analysis grid.

    ``config.RICE_MASK`` records which product was used, its reference year and its licence;
    if more than one verifies, the union is taken and the provenance kept per cell so the
    sensitivity of every downstream number to the mask choice can be shown, not asserted.
    """
    raise NotImplementedError


def build_cells():
    """The analysis cell index: cell id, centroid, area, kecamatan and kabupaten codes.

    Area is geodetic, not planar — a degree of longitude is ~111 km at the equator and Java
    spans 9 degrees of it; planar cell areas would bias the eastern kabupaten systematically.
    """
    raise NotImplementedError


def main() -> None:
    log("ingest_aux: not implemented (scaffold)")
    util.guard_disk()


if __name__ == "__main__":
    main()
