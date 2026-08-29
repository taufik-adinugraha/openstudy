"""Stages: complexity + layout (spec §B3).

complexity: per year — Balassa RCA -> M matrix (RCA >= 1) -> ECI/PCI via
            py-ecomplexity (eigenvector method), proximity phi, Indonesia's
            density and complexity-outlook gain per product.

            PCI SIGN NORMALIZATION IS AN EXPLICIT, TESTED STEP: py-ecomplexity
            issue #1 documents sign/scaling drift vs the Atlas convention.
            Rule: machinery/chemicals PCI must exceed raw-commodity PCI; if
            inverted, flip. A unit test pins this.

layout    : product-space graph = maximum-spanning-tree backbone + edges with
            phi >= PHI_THRESHOLD (~1,200 nodes / ~2,500 edges). Force layout
            computed ONCE with LAYOUT_SEED; coordinates cached to JSON.
            The browser never runs the simulation.
"""

from __future__ import annotations

import argparse
import sys

import config


def compute_year(year: int) -> None:
    raise NotImplementedError("week 4: RCA/M/ECI/PCI/phi per year")


def normalize_pci_sign() -> None:
    raise NotImplementedError("week 4: Atlas-convention sign check + flip")


def layout() -> None:
    raise NotImplementedError("week 4: MST + phi edges, seeded force layout -> JSON")


def validate() -> int:
    """Gates G-B1..G-B4 (spec §B4); nonzero exit blocks publish."""
    raise NotImplementedError("week 4: Spearman vs Atlas, IDN rank, BPS recon, story facts")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["compute", "layout", "validate"])
    args = parser.parse_args()
    try:
        if args.stage == "compute":
            for year in config.YEARS:
                compute_year(year)
            normalize_pci_sign()
        elif args.stage == "layout":
            layout()
        else:
            return validate()
    except NotImplementedError as todo:
        print(f"[complexity:{args.stage}] STUB — not yet implemented: {todo}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
