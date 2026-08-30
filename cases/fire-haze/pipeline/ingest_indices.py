"""Stage 3 · indices — rainfall, drought and the ocean state that sets the season.

Indonesian fire seasons are not equal.  The severe years — 1997, 2015, 2019 — are El Nino years,
and 2019 was also a strong positive Indian Ocean Dipole; both push the monsoon late and dry out
peat that is otherwise waterlogged.  A model that does not carry the ocean state will fit the
mean season and miss exactly the years anyone cares about, so ENSO and IOD enter as features and
the anchor years are held out (gate G-J5) to prove the model did not simply memorise them.

SOURCES (all small, all text or gridded rainfall; pinned in config)
------------------------------------------------------------------
CHIRPS      daily/pentad 0.05 deg rainfall, 1981-present.  Rainfall deficit over the preceding
            30/60/90 days is the strongest single predictor of ignition in the literature, and
            CHIRPS is the only long, open, gauge-corrected option at a useful resolution here.
            THREE operational facts drive the code: (a) v2 production ENDS after December 2026,
            so v3 is what we build on; (b) the final product runs 30 days behind while ``prelim``
            runs 5, so a days-ahead model must be TRAINED AND SERVED on prelim or it has
            train/serve skew; (c) within prelim, use ``fixed/`` rather than ``tifs/`` — per CHC's
            own readme the fixed files sum exactly to the pentad totals and the others carry
            residuals.
CHIRPS-GEFS bias-corrected GEFS v12 precipitation FORECASTS, lead days 0-15, 0.05 deg, issued
            same day, public domain, no auth.  This is what makes the case genuinely
            forward-looking rather than a reanalysis replayed with a lag, and it is the answer to
            the obvious objection that ERA5 knows the future.
ONI/Nino3.4 NOAA CPC.  Use ``wksst9120.for`` for the weekly series, NOT ``wksst8110.for`` — the
            latter still resolves but froze in January 2021 when the base period changed, which
            is exactly the kind of silent staleness that poisons a feature for a year.
DMI (IOD)   NOAA PSL HadISST.  The 2019 season is unreadable without it — and it is the weak
            link: the series ends ~3 months back and is stamped "Preliminary", so it is a
            historical feature only and the spec says the operational gap out loud.
SOI         Long Paddock (Queensland), CC BY 4.0 — DAILY, ~2-day latency.  Every BoM SOI URL is
            dead (404), and Long Paddock is the better source anyway.

DERIVED, AND WHAT IS DELIBERATELY *NOT* DERIVED
-----------------------------------------------
SPI-1/3/6   computed here from CHIRPS, gamma fit per cell per calendar month on the FULL record
            so the standardisation is against climatology rather than against the modelling
            window (fitting on the window would flatten precisely the drought years we care
            about).  It is computed rather than downloaded for one reason: only a self-computed
            index can also be run on the CHIRPS-GEFS *forecast* and stay consistent with the
            operational path.  Ready-made products (CDS ``derived-drought-historical-monthly``,
            GDO's CHIRPS SPI-3) are used to VALIDATE ours, not to replace it.  SPEIbase is
            excluded: ODbL 1.0 share-alike is a viral-licence risk on a commercial deliverable.
KBDI        NOT computed here.  ``cems-fire-historical-v1`` on EWDS publishes the full Canadian
            FWI set — FWI, FFMC, DMC, DC, ISI, BUI, DSR — *and* ``keetch_byram_drought_index``,
            at 0.25 deg with 2-day latency and server-side bbox subsetting, for about 29 MB per
            year over our box.  Writing our own accumulator would be slower, less validated and
            no cheaper.  It is ingested as the EXTERNAL BASELINE the risk model must beat, which
            is worth far more than a baseline we designed ourselves.

The one thing that does not exist: an open medium-range FWI FORECAST.  ``cems-fire-forecast``
404s, only the seasonal product is published, and GWIS renders a 1-10 day forecast without
publishing it for bulk download.  That gap is the commercial opening for this case.

OUTPUT: data/chirps.parquet (cell x day rainfall, observed + forecast leads),
data/drought.parquet (cell x day SPI, and the CEMS FWI/KBDI set), data/enso.parquet.
"""

from __future__ import annotations

import config
import util
from util import log


def chirps():
    """Stream CHIRPS daily rasters, clip to the AOI, reduce to the model grid, delete raw.

    A global daily CHIRPS file is ~1-7 MB gzipped and there are ~16,000 of them for the full
    record, so the loop is disk-guarded and resumable per day and the raw file is discarded as
    soon as the cell means are written.
    """
    raise NotImplementedError


def spi(rain, scales=(1, 3, 6)):
    """Standardised Precipitation Index at 1/3/6-month scales.

    Gamma fit per cell per calendar month on the FULL record, then transformed to a standard
    normal.  Fitting on the modelling window instead would leak the drought years' own severity
    into their standardisation and flatten precisely the signal we want.
    """
    raise NotImplementedError


def chirps_gefs():
    """Fetch the 0-15 day bias-corrected precipitation forecast issued for a given day.

    Stored with its ISSUE date as well as its valid date, because a forecast is only a forecast
    if you can prove when it was made.  The risk model's operational path consumes these; the
    reanalysis path consumes observed CHIRPS, and the two are scored separately so the cost of
    forecasting rather than hindcasting is visible rather than assumed away.
    """
    raise NotImplementedError


def cems_fire_indices():
    """Pull the CEMS Canadian FWI set + KBDI from EWDS with server-side bbox subsetting.

    The existing ECMWF token authenticates here; a 403 reading "user didn't accept all required
    site policies" is a one-time browser click on the EWDS terms, not a credential problem.
    (Anonymous requests return 401, which is how we know the token is doing its job.)
    """
    raise NotImplementedError


def ocean_indices():
    """ONI, DMI and SOI as a day-indexed table.

    Monthly values are forward-filled to daily and a ``*_is_interpolated`` flag rides along, so
    nothing downstream can quietly treat a monthly index as a daily observation.  SOI is genuinely
    daily from Long Paddock, so it carries no such flag — and the difference is worth keeping
    visible rather than smoothing into a uniform table.
    """
    raise NotImplementedError


def main() -> None:
    log("ingest_indices: not implemented (scaffold)")
    util.guard_disk()


if __name__ == "__main__":
    main()
