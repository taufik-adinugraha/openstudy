"""Stage 4 · era5 — fire weather at the surface and steering winds aloft.

THE GOTCHA THAT COSTS A DAY IF IT IS NOT WRITTEN DOWN
------------------------------------------------------
A multi-variable CDS request does not come back as one NetCDF.  The service splits the response
by ``stepType`` — instantaneous fields (winds, temperature, dewpoint, pressure, boundary-layer
height) in one file, accumulated fields (total precipitation, surface solar radiation) in
another — and hands back a zip or two separate ``.nc`` payloads.  Concatenating them naively
along time produces a frame in which precipitation and radiation are all-NaN, which a
gradient-boosted model then silently drops as useless columns.  Nothing errors.  The fix is to
open each file separately and JOIN ON THE GRID KEY (valid_time, latitude, longitude) rather than
stacking, and ``assert`` a non-null fraction on every accumulated column before writing.

TWO DATASETS, TWO JOBS
----------------------
``reanalysis-era5-single-levels``   fire weather: 10 m u/v wind, 2 m temperature, 2 m dewpoint
                                    (-> relative humidity, the variable that actually drives
                                    ignition probability), total precipitation, surface pressure,
                                    boundary-layer height (the vertical volume the smoke gets to
                                    mix into — a low BLH is what turns smoke into an episode),
                                    volumetric soil water layers 1-2.
``reanalysis-era5-pressure-levels`` transport: u/v at 925 and 850 hPa.  Surface wind is the
                                    wrong thing to advect a plume with — smoke from a hot fire
                                    lofts above the surface layer and is steered by the flow at
                                    ~850 hPa.  Using 10 m wind alone systematically under-rotates
                                    trajectories toward the coast.  ``config.TRAJ_LEVELS`` holds
                                    the levels the trajectory model blends.

QUEUEING
--------
CDS queues server-side and a multi-year backfill takes hours to days.  The stage therefore
shards by year (``--shard i --of n``) and writes one parquet part per month; ``pipeline/finish.sh``
waits for the shards to drain, makes one single-threaded gap-filling pass for any month a shard
dropped on a transient error, and then runs the rest of the DAG.  Every part is idempotent, so
re-running is always safe.  The ERA5T tail lags ~5 days: the daily refresh target must not
assume today is available.

OUTPUT: data/era5_parts/<YYYY-MM>.parquet -> data/era5.parquet (cell x hour, then cell x day
aggregates: daily max temperature, min RH, total rain, mean BLH, mean u/v at each level).
"""

from __future__ import annotations

import argparse

import config
import util
from util import log


def request_month(dataset: str, variables: list[str], year: int, month: int, **kw):
    """One cdsapi retrieve for one month.  Returns the downloaded path(s).

    ``~/.cdsapirc`` already exists on the server and the ``cc-by`` licence is accepted; the key
    is also mirrored into ``.env`` as ``CDS_API_KEY`` / ``CDS_API_URL`` for portability.
    """
    raise NotImplementedError


def read_split_netcdfs(paths):
    """Open the instantaneous and accumulated NetCDFs and JOIN them on the grid key.

    See the module docstring.  Asserts that every accumulated column has a non-null fraction
    above ``config.MIN_NONNULL`` before returning, so the split-file failure mode is loud.
    """
    raise NotImplementedError


def derive(df):
    """Add the fire-weather variables the raw fields do not contain.

    relative_humidity   from 2 m temperature and dewpoint (Magnus formula)
    wind_speed/dir      from u/v at each level
    vpd                 vapour-pressure deficit — a better ignition covariate than RH alone
    rain_days_since     consecutive days below the ``config.WET_DAY_MM`` threshold
    """
    raise NotImplementedError


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", type=int, default=1)
    args = ap.parse_args()
    log(f"ingest_era5: not implemented (scaffold) shard {args.shard}/{args.of}")
    util.require(bool(config.CDS_API_KEY), "CDS_API_KEY missing from repo-root .env")


if __name__ == "__main__":
    main()
