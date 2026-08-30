"""Stage 1 · fires — FIRMS hotspot archive and near-real-time, cleaned.

WHY THIS STAGE IS FIRST AND WHY IT IS NOT TRIVIAL
-------------------------------------------------
Roughly one detection in ten inside the Indonesian box is not a landscape fire.  Indonesia has
~130 active volcanoes and a working oil-and-gas industry, and both radiate at the wavelengths
VIIRS and MODIS watch.  A hotspot table taken at face value therefore reports Merapi, Sinabung,
Dukono and the Duri and Bontang flares as "fires" every single day of the year — a permanent,
perfectly seasonal-looking background that a risk model will happily learn and then present as
skill.  Two filters remove it:

  * ``type == 0`` only, WHERE THE FIELD EXISTS.  FIRMS classifies each detection: 0 = presumed
    vegetation fire, 1 = active volcano, 2 = other static land source (the flares live here),
    3 = offshore.  ** THE FIELD IS ABSENT FROM EVERY NRT PRODUCT. **  NASA states it plainly:
    "data distributed via the FIRMS download tool does not attribute the static sources/inferred
    hotspot 'type'".  So a bare ``type == 0`` filter SILENTLY NO-OPS on the live tail, the recent
    part of the series keeps volcanoes and flares that the historical part drops, and the SP/NRT
    seam becomes a step change that looks like a trend.  (Case E's ``ingest_firms.py`` has this
    bug today.)  ``type`` is therefore used to BUILD a static exclusion mask from the SP archive,
    and THE MASK — not the field — is what filters every product.
  * drop low confidence, and ONLY low.  Measured on the full box for 2019-09-15..19 (35,229
    detections): confidence is ``n`` 91.2 %, ``l`` 5.7 %, ``h`` only 3.2 %.  Filtering to
    high-confidence would discard 97 % of the signal.  ``config.DROP_CONF`` drops ``l``; MODIS's
    numeric 0-100 scale gets ``config.MIN_CONF_MODIS`` so the two sensors are filtered on
    comparable terms rather than on whatever the column happens to contain.

The geometric half of the mask: any detection within ``config.VOLCANO_EXCLUDE_KM`` of a known
volcanic summit (OSM ``natural=volcano`` via Overpass — 295 nodes in the Indonesian bbox, ODbL,
because the Smithsonian GVP is Cloudflare-gated) or of an empirically-identified persistent
source is removed.  Persistent sources are found from the data itself: a cell active on more than
``config.PERSISTENT_MIN_DAYS`` days in EVERY year of the record is a flare or a landfill, not a
landscape fire, whatever its ``type`` says.  That detector needs no external list and is the one
actually relied on.

Measured filter impact at the 2019 peak: ``type != 0`` removes 0.89 %, low confidence a further
5.7 %.  Note that the type share is small AT PEAK — away from the season the constant volcano and
flare floor is a far larger fraction of what is there, which is exactly why it has to go.

SENSOR CHOICE AND THE 2015 ANCHOR
---------------------------------
VIIRS SNPP standard processing begins 2012-01-20, so **both anchors are inside the VIIRS record**
and no MODIS splice is needed for them.  Measured on the Sumatra box for 2015-10-20: VIIRS 6,110
detections against MODIS 1,354, a factor of 4.5.  MODIS is ingested only to extend base rates
back to 2001, as its own series, on its own charts — 1 km against 375 m is not a comparison.

** COLLECTION TRAP. **  The bulk country files are MODIS Collection 6.1 (``version`` "6.2") while
the API's ``MODIS_SP`` returns Collection 6.0 (``version`` "6.03").  Same sensor, same day,
different row counts.  Do not concatenate them.  VIIRS is consistent (``version`` "2") in both.

TWO ROUTES, AND THE BULK ONE IS BETTER FOR HISTORY
--------------------------------------------------
The ``area`` API caps at FIVE days per request, so the 2012-2026 archive is ~1,000 windows per
source.  The per-country bulk CSVs avoid nearly all of that: the directory listing 404s under the
new backend, but the files themselves are live at predictable URLs
(``config.FIRMS_BULK_URL``), and VIIRS SNPP 2012-2024 for Indonesia is about 250 MB total.  Bulk
lags ~18 months (2025 and 2026 are 404), so history comes from bulk and the tail from the API.
The file sizes alone tell the story and are charted as such: Indonesia 2015 = 66.1 MB,
2019 = 33.0 MB, 2016 (La Nina) = 8.8 MB.

OUTPUT
------
``data/fires.parquet``          one row per retained detection: lat, lon, acq_datetime, sensor,
                                confidence, frp_mw, daynight, cell_id (0.25 deg), adm2_code
``data/fires_removed.parquet``  the same columns plus ``removed_reason`` — published, not binned
``data/fires_daily.parquet``    cell x day counts and summed FRP, the panel input for features.py

RESUMABILITY
------------
The FIRMS ``area`` endpoint caps a request at ``config.FIRMS_MAX_DAYS`` days, so the archive is
walked in windows and each finished window is recorded in the manifest.  The rate limit is
5,000 requests per 10 minutes on the shared key; ``--nrt-only`` fetches just the trailing window
for the daily refresh target.
"""

from __future__ import annotations

import argparse

import config
import util
from util import log


def fetch_bulk(year: int, country: str = "Indonesia"):
    """One per-country yearly CSV.  The route for everything up to ``FIRMS_BULK_LAST_YEAR``."""
    raise NotImplementedError


def fetch_window(source: str, start: str, days: int):
    """One FIRMS ``area`` call (max 5 days) -> raw DataFrame.  The route for the recent tail.

    Windows already in the manifest are skipped; windows ending within the last three days are
    refetched, because NRT keeps back-filling late granules for roughly 48 hours.
    ``config.FIRMS_QUOTA_URL`` reports the key's own transaction count, so the run can stay under
    5,000 per 10 minutes by measurement rather than by guesswork.
    """
    raise NotImplementedError


def build_static_mask(sp_archive):
    """The heart of gate G-J1, and the reason this case does not inherit Case E's NRT bug.

    Two components, unioned into one mask that is then applied to EVERY product:
      volcanoes  OSM ``natural=volcano`` nodes buffered by ``config.VOLCANO_EXCLUDE_KM``
      persistent every cell (``config.PERSISTENT_CELL_DEG``) active on more than
                 ``config.PERSISTENT_MIN_DAYS`` days in EVERY year of the record, plus every cell
                 where the SP archive's ``type`` field says 1 or 2 more often than it says 0

    Vegetation fire in Indonesia is intensely seasonal; something that burns in March and in
    November and in every year of a fourteen-year record is a flare or a landfill.  Written to
    ``data/static_mask.parquet`` and published on the methodology page as a map, because a
    filter this consequential should be inspectable rather than asserted.
    """
    raise NotImplementedError


def clean(raw, mask):
    """Apply the confidence filter and the static mask to one chunk.

    Returns ``(kept, removed)``.  ``removed`` carries ``removed_reason`` in
    {``type_volcano``, ``type_static``, ``type_offshore``, ``low_confidence``, ``near_volcano``,
    ``persistent_source``} so gate G-J1 publishes the composition of what was thrown away rather
    than only its size.  ``type``-derived reasons are available on SP rows only; NRT rows can
    only ever be removed by the mask or by confidence, and the reason column says so rather than
    implying a filter that did not run.
    """
    raise NotImplementedError


def to_daily(kept):
    """Cell x day counts, summed FRP and night-fraction — the panel input for features.py."""
    raise NotImplementedError


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrt-only", action="store_true",
                    help="daily refresh: fetch only the trailing NRT window")
    args = ap.parse_args()
    log("ingest_fires: not implemented (scaffold)", "nrt-only" if args.nrt_only else "full")
    util.require(bool(config.FIRMS_MAP_KEY), "FIRMS_MAP_KEY missing from repo-root .env")


if __name__ == "__main__":
    main()
