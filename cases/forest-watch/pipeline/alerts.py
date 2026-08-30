"""Stage 2 · alerts — from date_conf pixels to weekly alert clusters ("events").

Decoding (GFW convention): value = confidence × 10000 + days since 2014-12-31
(2 = low, 3 = high confidence → 2xxxx / 3xxxx). Zero = no alert.
Per focus province (clipped with the ADM1 polygon, windowed reads so a 10° tile never sits in
RAM whole — ~1-2 GB peak):
  1. mask to Hansen treecover2000 ≥ 30 % and datamask = land (the forest definition GFW uses,
     so gate G-H2 compares like with like);
  2. bucket alert dates into ISO weeks;
  3. connected-component labelling (8-connectivity, scipy.ndimage.label) per rolling 4-week
     window → clusters; keep clusters ≥ config.MIN_CLUSTER_HA (0.5 ha ≈ 50 px);
  4. per cluster: first/last date, area, high-confidence share, centroid, bbox, GLAD-L
     agreement flag (any GLAD-L alert within the footprint ±60 days).
Outputs: data/alerts/<province>/clusters.parquet (+ GeoParquet polygons) and a weekly ledger
data/alerts/ledger.parquet (province × week × count × ha).
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO alerts →", config.ALERTS_DIR, "| min cluster ha:", config.MIN_CLUSTER_HA)


if __name__ == "__main__":
    main()
