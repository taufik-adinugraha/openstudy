"""Stages: ingest + filter (spec §B3).

Download BACI HS92 CSVs for config.BACI_RELEASE into DuckDB with schema checks
(year coverage, row counts vs release notes, value-total drift < 5% vs prior
release), then build the Atlas-style filtered sample and the HS4 aggregation.
Goods only — stated on the methodology page.
"""

from __future__ import annotations

import argparse
import sys

import config


def download_release(release: str) -> None:
    raise NotImplementedError("week 4: BACI zip download + checksum log")


def load_duckdb(release: str) -> int:
    """CSV -> trade.duckdb `flows` table. Returns row count."""
    raise NotImplementedError("week 4: duckdb ingest + schema checks")


def build_sample() -> None:
    """Country filter (pop >= 1M, trade >= $1B) + HS6->HS4; keep nickel HS6."""
    raise NotImplementedError("week 4: sample + aggregation views")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default=config.BACI_RELEASE)
    args = parser.parse_args()
    print(f"[ingest] BACI release={args.release} years={config.YEARS.start}-{config.YEARS.stop - 1}")
    try:
        download_release(args.release)
        rows = load_duckdb(args.release)
        build_sample()
    except NotImplementedError as todo:
        print(f"[ingest] STUB — not yet implemented: {todo}")
        return 0
    print(f"[ingest] {rows:,} rows loaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
