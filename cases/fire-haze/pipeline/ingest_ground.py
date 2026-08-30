"""Stage 5 · ground — the observed air quality this case is scored against.

THE FINDING THAT RESHAPED THIS STAGE
------------------------------------
The plan assumed the problem was thin coverage in Jakarta.  It is not.  Queried live against
OpenAQ v3: Indonesia has 55 PM2.5 locations, and **Riau has zero.  All of Kalimantan has zero.**
Bounding-box and 25 km radius searches around Pekanbaru, Palangkaraya and Banjarbaru all return
``found: 0``.  The fire belt — the places whose air this case is about — is unmonitored in every
open source we could find.  Malaysia is worse: ``apims.doe.gov.my`` 404s on every path including
its root, and the only aggregator carrying APIMS forbids commercial use in terms.  Indonesia's
own ISPU moved to ``ispu.kemenlh.go.id`` after the 2024 ministry split (``menlhk.go.id`` is now
NXDOMAIN) but is server-rendered with no API and no stated licence.

So the verification design is three tiers, and the page says which tier every number comes from:

  tier 1  **Singapore NEA.**  The only long, clean, commercially-licensed ground record in the
          region: hourly, five regions, and the v1 endpoint takes a ``date`` and returns a full
          day of history.  Verified at the 2019 haze peak (2019-09-21, south region 105 ug/m3).
          This carries the hard gate.  ** Its history starts ~2016-03 ** — 2015-10-20 returns
          zero items and 2013 returns HTTP 500 — so the 2015 anchor has NO Singapore ground truth
          and that is stated rather than finessed.
  tier 2  the Indonesian OpenAQ units that DO exist, each with its short coverage printed beside
          it: Palembang (AirGradient, 2025-10 -> live), a Clarity unit at -0.608/100.755 that is
          **labelled "Jakarta" but physically in West Sumatra** (2023-11 -> live), and USU Medan.
  tier 3  CAMS EAC4 reanalysis PM2.5 as a SURROGATE at Pekanbaru, Palangkaraya and Pontianak,
          where no open sensor has ever existed.  It is a model, it is called a model everywhere
          it appears, it gets its own row and its own colour, and it is the only reference that
          reaches the 2015 anchor at all.  Comparing our trajectory model against a chemistry
          model is a weaker claim than comparing against an instrument — and saying so is worth
          more than quietly presenting a reanalysis as an observation.

OUTPUT: data/ground.parquet (receptor x day: value, unit, tier, source, n_hours, is_surrogate)
plus data/ground_meta.json (per-receptor coverage, licence, station ids, and the gaps).
"""

from __future__ import annotations

import config
import util
from util import log


def openaq_locations():
    """Resolve OpenAQ v3 location ids for each tier-2 receptor.

    Queried by bounding box, not by name: naming is inconsistent (one genuine Sumatra station is
    filed as "Jakarta"), and a name match silently returns nothing when a provider renames a
    sensor.  Indonesia is ``countries_id=1``, not 41.  Coverage (first and last measurement) is
    recorded per station so a unit that only came online in late 2025 cannot quietly become the
    evidence for a 2019 claim.
    """
    raise NotImplementedError


def openaq_series(location_ids):
    """Daily PM2.5 per location, paginated, resumable per (location, month)."""
    raise NotImplementedError


def nea_singapore(start: str, end: str):
    """Singapore NEA PM2.5 via the v1 endpoint, which accepts a date and returns that day.

    The v2 real-time endpoint ignores ``date`` entirely, so history comes from v1 only.  Regional
    values (north/south/east/west/central) are kept alongside the national mean because the
    regional split is what gate G-J3's direction check reads.
    """
    raise NotImplementedError


def cams_surrogate(receptors):
    """Tier-3 surrogate: CAMS EAC4 PM2.5 extracted at the unmonitored receptor coordinates.

    Flagged ``is_surrogate=True`` on every row.  Nothing downstream may aggregate a surrogate row
    into an observed statistic; ``harmonise`` asserts that.
    """
    raise NotImplementedError


def harmonise(frames):
    """One long table, one unit, explicit flags.

    PSI is an index, not a concentration: it keeps its own column and is never converted into a
    fake microgram value.  Where a receptor publishes both, PM2.5 is preferred and PSI is retained
    for the episode-day definition gate G-J3 uses.
    """
    raise NotImplementedError


def main() -> None:
    log("ingest_ground: not implemented (scaffold)")
    util.require(bool(config.OPENAQ_API_KEY), "OPENAQ_API_KEY missing from repo-root .env")


if __name__ == "__main__":
    main()
