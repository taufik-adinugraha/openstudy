"""Stage 5 · validate — gates G-F1..G-F4 (numbers in config.py; prose in the spec §F4).

G-F1  Out-of-sample skill (hard): leave-one-province-out R² ≥ 0.50, Spearman ρ ≥ 0.70 and
      RMSE ≤ 4.0 pp on the latest year's regency cross-section — thresholds set from the
      Indonesian literature (Putri 2022: Spearman 0.77, RMSE 3.2 pp; Sartirano 2023:
      Spearman 0.75; Chi 2022: spatial-CV R² 0.56).
G-F2  Java vs off-Java disclosure: both R² reported; if off-Java R² < 0.35 the site labels
      off-Java estimates "indicative" and shows the interval widening.
G-F3  Temporal hold-out: train ≤ 2023, predict the 2024 and 2025 BPS releases —
      Spearman ρ ≥ 0.65 on each; the honest "can it see forward, not just sideways" test.
G-F4  Benchmark integrity (hard): population-weighted kecamatan estimates reproduce every
      regency's BPS rate within 0.1 pp after benchmarking (bookkeeping — must be exact).

Also computed for the methodology page: random-k-fold vs spatial-CV gap (how much the naive
score flatters), kota vs kabupaten ordering, SHAP feature-family shares.

Writes data/stats.json (feeds the insight brief and the methodology page); exits non-zero if
a hard gate fails; G-F2/G-F3 are disclosure gates that change copy, not pass/fail.
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO validate →", config.STATS_JSON)


if __name__ == "__main__":
    main()
