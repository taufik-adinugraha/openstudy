"""Stage 3 · link — the part GFW does not do: attach every cluster to the commodity system.

For each cluster (all linkage layers are CC BY 4.0 and stored locally):
  palm class        inside the Descals 2021-extent (clearing INSIDE existing plantation =
                    replanting/expansion) vs within 1 km of it (edge expansion) vs beyond
                    (frontier clearing) — the typology Gaveau et al. use;
  planting year     where inside palm, the Descals year-of-plantation raster dates the estate;
  nearest_mill      Universal Mill List mills within config.MILL_RADIUS_KM (50 km — the
                    fresh-fruit-bunch catchment Trase uses); count + nearest distance +
                    group/parent company fields as published in the UML;
  peat / primary    on-peat flag (gfw_peatlands) and in-primary-forest-2000 flag (Margono) —
                    the two flags that turn an alert into a compliance question (EUDR framing);
  linkage class     PALM-INTERNAL · PALM-EDGE (≤ 1 km) · MILL-CATCHMENT (≤ 50 km, outside palm)
                    · UNLINKED — mutually exclusive, in that order.
Concession boundaries are NOT part of the stored linkage: the only Indonesian concession
vectors are view-only/unlicensed (config.CONCESSION_OVERLAYS renders them live in the
explorer as a reference layer, clearly labelled, never downloaded or joined — README).

Aggregations: per province × quarter, hectare share per linkage class; per mill an "alert
pressure" score = alert ha in the catchment over the last 12 months (distance-weighted),
with cluster counts and the peat/primary shares.

Outputs: data/linked.parquet (cluster level), data/mills_scored.parquet, data/link_summary.json.
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO link →", config.LINKED, "| mill radius km:", config.MILL_RADIUS_KM)


if __name__ == "__main__":
    main()
