"""Stage 1 · stats — BPS official rice statistics, the thing this case is benchmarked against.

THIS STAGE RUNS FIRST ON PURPOSE.  It is cheap, and if the official series cannot be retrieved
there is nothing to validate against and the case does not exist.  Failing here costs minutes;
failing here after the SAR ingest costs days.

BPS WebAPI, the parts that are not in the documentation
-------------------------------------------------------
  * The national domain ``0000`` serves all 514 regencies in one response.  There is no need to
    crawl per-domain, and doing so is how the first version of the poverty case wasted an
    afternoon.
  * A BROWSER User-Agent is required.  The WAF rejects curl-style agents with a response that
    looks like a server error rather than a block.  ``util.browser_ua()`` supplies one.
  * The "App ID" the developer portal talks about IS the API key (``BPS_API_KEY`` in .env).
  * Variables are discovered, not guessed: ``model=var`` and ``model=subject`` are listed and
    searched for the rice terms, and the resolved ids are written to ``data/bps_vars.json`` so
    the run is reproducible and the ids are auditable rather than being magic constants in code.

THE METHODOLOGY BREAK THAT MUST NOT BE SMOOTHED OVER
----------------------------------------------------
BPS replaced its long-standing eye-estimate harvested-area method with KSA (Kerangka Sampel
Area — an area-frame sample survey, itself partly satellite-assisted) from the 2018 reference
year.  The old and new series are NOT comparable: the switch alone moved national harvested area
by a large margin, and any chart that runs a single line through 2017-2018 is telling a lie about
a trend that is actually a definitional change.  ``config.KSA_FIRST_YEAR`` marks the break, the
pipeline stores the two regimes as separate series, and every chart that crosses the break draws
it as a break.

CADENCE
-------
Annual harvested area and production are published per kabupaten.  Sub-annual (monthly) harvested
area is published at province level in the "Luas Panen dan Produksi Padi" release; where the
WebAPI does not carry it, ``config.BPS_MONTHLY`` records the actual publication route and the
figures are ingested from it with the source stated.  Gate G-I2 needs the monthly series — it is
the only official statement of harvest *timing*, which is the half of this case that is genuinely
hard and genuinely valuable.

OUTPUT: data/bps_ksa.parquet (region x year x [month] harvested area ha),
data/bps_production.parquet (production GKG tonnes, productivity ku/ha), data/bps_vars.json.
"""

from __future__ import annotations

import config
import util
from util import log


def list_variables(subject: str | None = None):
    """List BPS WebAPI variables on domain 0000 and return the rice-related ones.

    Searches ``var`` labels for the Indonesian terms (padi, luas panen, produksi, produktivitas)
    rather than relying on remembered ids, and writes what it resolved to ``data/bps_vars.json``.
    """
    raise NotImplementedError


def fetch_series(var_id: int, years: list[int]):
    """One BPS ``model=data`` pull for one variable across the year list, all regions.

    Paginated; the response's ``datacontent`` keys encode region x turvar x year and have to be
    decomposed against the ``vervar`` / ``turvar`` / ``th`` lookups returned in the same payload.
    """
    raise NotImplementedError


def reconcile_regions(df):
    """Map BPS region codes to the analysis vintage.

    Reuses the name-based post-2020 pemekaran recode from
    ``cases/poverty-map/pipeline/features.py`` — it resolves by P-code first and falls back to a
    normalised name, and it fixes the kota/kabupaten key collision that the nightlights version
    still carries.  Java is unaffected by the Papua splits, but the crosswalk is shared so the
    national context panels stay correct.
    """
    raise NotImplementedError


def main() -> None:
    log("ingest_stats: not implemented (scaffold)")
    util.require(bool(config.BPS_API_KEY), "BPS_API_KEY missing from repo-root .env")


if __name__ == "__main__":
    main()
