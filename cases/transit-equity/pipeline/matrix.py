"""Stage 4 · matrix — kelurahan → destination travel times, chunked and resumable.

Origins: ~1,500 kelurahan population-weighted centroids (points.py).
Destinations: the 1 km lattice carrying the jobs proxy, population and health facilities.
Departure: weekday 2026-09-02, 07:00 + a 120-minute window (per-minute departures), p50 —
SCHEDULED time, not congestion-adjusted. Modes per scenario: all (WALK+TRANSIT),
no_rail (WALK+BUS), walk (WALK only).

Chunking is the resource compromise (README): origins are processed in batches of
config.ORIGIN_BATCH and each batch is written to data/matrix_parts/<scenario>_<i>.parquet.
Re-running skips finished parts, so the job survives an OOM kill or a disk-floor stop.

Usage: python pipeline/matrix.py [scenario ...]      (default: all no_rail walk)
"""

from __future__ import annotations

import datetime
import sys

import numpy as np
import pandas as pd

import config
import network
import points
import util
from util import log

PARTS = config.DATA_DIR / "matrix_parts"


def _departure() -> datetime.datetime:
    d = datetime.date.fromisoformat(config.DEPARTURE_DATE)
    h, m = config.DEPARTURE_WINDOW[0].split(":")
    return datetime.datetime(d.year, d.month, d.day, int(h), int(m))


def _window_minutes() -> int:
    (h0, m0), (h1, m1) = (x.split(":") for x in config.DEPARTURE_WINDOW)
    return (int(h1) * 60 + int(m1)) - (int(h0) * 60 + int(m0))


def run_scenario(tn, scenario: str, origins, dests) -> None:
    from r5py import TransportMode, TravelTimeMatrix

    modes = [getattr(TransportMode, m) for m in config.SCENARIOS[scenario]]
    # One walking budget for every scenario (30 min, the standard access-walk assumption).
    # It must be identical across scenarios or the layer attribution in chapter 4 compares
    # different worlds — and "everything" would not be a superset of "walking only".
    kw = dict(
        departure=_departure(),
        departure_time_window=datetime.timedelta(minutes=_window_minutes()),
        percentiles=[50],
        transport_modes=modes,
        max_time=datetime.timedelta(minutes=config.MAX_TRIP_MIN),
        max_time_walking=datetime.timedelta(minutes=config.MAX_WALK_MIN),
    )
    n = len(origins)
    batches = [origins.iloc[i:i + config.ORIGIN_BATCH] for i in range(0, n, config.ORIGIN_BATCH)]
    log(f"scenario {scenario}: {n} origins × {len(dests)} destinations in {len(batches)} batches")
    for i, batch in enumerate(batches):
        out = PARTS / f"{scenario}_{i:03d}.parquet"
        if out.exists():
            continue
        util.guard_disk()
        t0 = datetime.datetime.now()
        ttm = TravelTimeMatrix(tn, origins=batch[["id", "geometry"]],
                               destinations=dests[["id", "geometry"]],
                               snap_to_network=True, **kw)
        df = pd.DataFrame(ttm)
        tcol = "travel_time" if "travel_time" in df.columns else \
            next(c for c in df.columns if c.startswith("travel_time"))
        df = df[df[tcol].notna()][["from_id", "to_id", tcol]]
        df.columns = ["from_id", "to_id", "tt"]
        df["tt"] = df["tt"].astype("int16")
        df["to_id"] = df["to_id"].astype("int32")
        df.to_parquet(out, index=False)
        log(f"  {scenario} batch {i+1}/{len(batches)}: {len(df):,} reachable pairs, "
            f"{(datetime.datetime.now()-t0).total_seconds():.0f}s, "
            f"{util.free_ram_mb()} MB RAM free")


def load(scenario: str) -> pd.DataFrame:
    parts = sorted(PARTS.glob(f"{scenario}_*.parquet"))
    if not parts:
        raise FileNotFoundError(f"no matrix parts for scenario {scenario}")
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)


def main() -> None:
    scenarios = sys.argv[1:] or list(config.SCENARIOS)
    util.guard_disk()
    util.guard_ram()
    PARTS.mkdir(parents=True, exist_ok=True)
    origins = points.build_origins()
    dests = points.build_destinations()
    tn = network.build()
    for s in scenarios:
        run_scenario(tn, s, origins, dests)
    log("matrix complete:", [f"{s}:{len(sorted(PARTS.glob(f'{s}_*.parquet')))}" for s in scenarios])


if __name__ == "__main__":
    main()
