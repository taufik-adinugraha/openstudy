"""Stage 4 · downscale — small-area estimates at kecamatan level.

Apply the regency-trained model to kecamatan features (features_adm3.parquet), then
BENCHMARK: within each regency, rescale kecamatan estimates so the population-weighted mean
equals the official BPS regency rate for that year. The official number is never
contradicted; the model only distributes it within the regency. Uncertainty: per-kecamatan
prediction interval from the LightGBM quantile models (p10/p90) widened by the regency-level
CV residual for its province — displayed, never hidden.

Independent cross-checks are thin by design: Meta's RWI is CC BY-NC (rejected for this
commercial demo), Podes microdata is paid, SMERU's map is CC BY-NC. What remains and is used:
the temporal hold-out (gate G-F3, in model.py's CV artefacts), kota > kabupaten ordering, and
a literature-agreement note against Putri et al. 2022 / Sartirano et al. 2023 (spec §F4).
Kemendesa IDM village indices are a candidate check with an unverified licence (README).

Outputs: data/estimates_adm3.parquet (kecamatan × year: p0_hat, p10, p90, benchmark factor).
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO downscale →", config.ESTIMATES_ADM3)


if __name__ == "__main__":
    main()
