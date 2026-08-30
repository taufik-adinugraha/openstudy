"""Stage 2 · static — the fuel and terrain layers that do not change day to day.

Everything here is either already licensed and pinned by Case H (forest-watch) or is fetched
once and cached.  Nothing in this stage is re-derived: the peat and primary-forest rasters come
from the same GFW Data API datasets Case H verified line by line (each licence read live from
the API's own ``license`` metadata field, not assumed), which means the two cases agree by
construction rather than by coincidence.

LAYERS
------
peat           ``gfw_peatlands`` — the single most important fuel variable in this case.  Peat
               is why Indonesian fire is a *haze* problem rather than a fire problem: a peat
               fire smoulders below the surface for weeks, emits several times the particulate
               per unit of biomass burned, and is nearly impossible to extinguish by rain alone.
               Every downstream ignition-risk and emission weight keys off this layer.
primary        ``umd_regional_primary_forest_2001`` — intact forest rarely carries fire, so its
               complement is where fire actually lives; also the honest denominator for
               "degraded land" claims.
landcover      ESA WorldCover 10 m, aggregated to the model grid as class fractions.  Cropland,
               shrubland and recently cleared land are the fuel classes that matter.
peat_depth     see ``config.PEAT_DEPTH`` — an OPEN, commercially usable Indonesian peat-DEPTH
               layer is the one genuinely doubtful source in this case.  If none verifies, the
               model uses peat *presence* only and the spec says so; depth is not invented.
adm            geoBoundaries gbOpen ADM2 (GADM is rejected: non-commercial).  Codes are
               reconciled to the current BPS vintage with the name-based post-2020 Papua recode
               ported from ``cases/poverty-map/pipeline/features.py`` — it fixes a
               kota/kabupaten key collision that the nightlights version still has.

All rasters are read in windows and reduced to the ``config.GRID_DEG`` model grid immediately;
the full-resolution tiles are deleted after aggregation (the house rule — see Case E), so the
stage costs bandwidth but almost no standing disk.

OUTPUT: data/static_grid.parquet (one row per model cell: peat fraction, peat depth if any,
land-cover class fractions, primary-forest fraction, elevation, adm2 code), data/adm.parquet.
"""

from __future__ import annotations

import config
import util
from util import log


def fetch_gfw_raster(layer: str):
    """Download one GFW tile set for the AOI tiles.

    The header must be spelled exactly lowercase ``x-api-key``: ``urllib.request`` title-cases
    custom headers and every authenticated call then 403s with a message that reads like a dead
    key.  ``requests`` preserves the case given, so this module uses ``requests``.  Do not
    "test" the key against ``/auth/apikey/{key}/validate`` (401 even for a good key) or against
    ``/dataset/{id}`` (public, proves nothing) — ``config.gfw_key_ok()`` exercises an
    authenticated query endpoint instead.
    """
    raise NotImplementedError


def fetch_worldcover():
    """ESA WorldCover 10 m tiles covering the AOI, streamed and aggregated per tile."""
    raise NotImplementedError


def aggregate_to_grid(paths):
    """Window-read each raster and reduce to class fractions on the model grid, then delete raw.

    Never holds a full tile in memory.  Peak RSS target is under 2 GB so the stage fits a
    ``MemoryMax=3G`` systemd transient unit alongside the other jobs on the shared box.
    """
    raise NotImplementedError


def boundaries():
    """geoBoundaries ADM2 -> BPS codes, with the name-based pemekaran recode.

    COD-AB is a 2020 vintage and predates the Papua splits; the recode matches by P-code first
    and falls back to normalised name, and it is the poverty-map implementation rather than the
    nightlights one because the latter collides ``Kota X`` with ``Kabupaten X``.
    """
    raise NotImplementedError


def main() -> None:
    log("ingest_static: not implemented (scaffold)")
    util.require(bool(config.GFW_API_KEY), "GFW_API_KEY missing from repo-root .env")


if __name__ == "__main__":
    main()
