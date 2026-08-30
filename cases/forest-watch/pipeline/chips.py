"""Stage 4 · chips — Sentinel-2 before/after for the largest clusters.

Earth Search STAC (earth-search.aws.element84.com/v1, collection sentinel-2-l2a, anonymous,
COG). For the top config.N_CHIPS clusters per province: least-cloudy scene in the 90 days
before the first alert and in the 90 days after the last, 2 km window, true colour (B04/B03/B02)
read at 20 m via COG overviews (~1-2 MB per read, ~100 MB total). Rendered as ≤ 60 kB PNG pairs
with the cluster outline burnt in, plus a JSON side-car (scene ids, dates, cloud %).
Cloud persistence over Kalimantan/Papua is the real constraint: when no scene < 40 % cloud
exists in a window, the chip is skipped and the panel says so rather than showing haze.

Outputs: web/public/chips/<cluster_id>_{before,after}.png + web/public/chips/index.json.
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO chips →", config.CHIPS_DIR, "| STAC:", config.STAC_URL)


if __name__ == "__main__":
    main()
