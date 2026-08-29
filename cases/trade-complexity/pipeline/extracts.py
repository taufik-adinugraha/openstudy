"""Stages: extracts + pulse + publish (spec §B3, §B5).

extracts: view models as parquet/JSON, each <= 3 MB — treemap shares per year,
          ECI-rank trajectories (IDN + peers), nickel-chain value series,
          top-partner flows, product-space node states per year.
pulse   : quarterly Comtrade free-tier + BPS latest-year panel. NEVER mixed
          into the complexity math or into any BACI chart — separate data
          plane, separate vintage stamp.
publish : static build + deploy with the double vintage stamp
          ("trade data through 2024 · pulse through Qx-YYYY").
"""

from __future__ import annotations

import argparse
import sys


def export_views() -> None:
    raise NotImplementedError("week 5: view-model exports")


def pulse() -> None:
    raise NotImplementedError("week 5: Comtrade (<=500 calls/day) + BPS refresh")


def deploy() -> None:
    raise NotImplementedError("week 5: astro build + wrangler pages deploy")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["extracts", "pulse", "publish"])
    args = parser.parse_args()
    try:
        {"extracts": export_views, "pulse": pulse, "publish": deploy}[args.stage]()
    except NotImplementedError as todo:
        print(f"[extracts:{args.stage}] STUB — not yet implemented: {todo}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
