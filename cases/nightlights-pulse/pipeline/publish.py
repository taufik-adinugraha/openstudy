"""Stages: brief + publish (spec §A3).

brief  : shared.pipeline.brief.generate() over stats.json -> PR (merge = review).
publish: export monthly PMTiles + parquet extracts + JSON view models,
         static build, deploy to Cloudflare Pages with the data-vintage stamp.
"""

from __future__ import annotations

import argparse
import sys


def export_views(month: str) -> None:
    raise NotImplementedError("week 3: PMTiles + view-model JSON exports")


def deploy(month: str) -> None:
    raise NotImplementedError("week 3: astro build + wrangler pages deploy")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    args = parser.parse_args()
    try:
        export_views(args.month)
        deploy(args.month)
    except NotImplementedError as todo:
        print(f"[publish] STUB — not yet implemented: {todo}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
