"""Stage 5 · validate — gates G-H1..G-H4 (numbers in config.py; prose in spec §H4).

G-H1  Hansen reconciliation (hard): our tree-cover-loss hectares per province × year
      (≥ 30 % canopy) vs the GFW Data API query on umd_tree_cover_loss for the same COD-AB
      province geometry — within ±5 % for every year 2015-2024. Same source data, so this
      tests our raster plumbing, not the world. National anchor: GFW's own country table
      says Indonesia 2023 = 1,395,285 ha, 2024 = 1,120,264 ha (config.GFW_IDN_TCL_HA).
G-H2  Alert reconciliation (hard): our RADD count and hectares per focus province for the
      last complete 12 months vs the GFW API's own aggregation over the same geometry —
      within ±10 %; differences from the forest mask and cluster minimum quantified.
G-H3  Two-sensor agreement: ≥ 60 % of high-confidence RADD clusters ≥ 5 ha carry a GLAD-L
      alert within ±60 days (independent radar vs optical detections agreeing).
G-H4  Linkage sanity: the palm+mill-linked share of alert hectares in Riau ≥ 25 % —
      literature floor (Gaveau 2022: ~32 % of 2001-19 loss went directly to oil palm;
      Trase: palm-driven deforestation ~18 % of peak by 2018-20). If the share lands below,
      the linkage radii are diagnosed before publish, not tuned to pass.

The methodology page also shows the KLHK divergence honestly: KLHK reports net deforestation
175,400 ha for 2024 (different forest definition, different minimum mapping unit) against
GFW's 1.12 Mha tree-cover loss — the page explains why both are true.

Writes data/stats.json; G-H1 and G-H2 are hard gates, G-H3 and G-H4 change copy.
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO validate →", config.STATS_JSON)


if __name__ == "__main__":
    main()
