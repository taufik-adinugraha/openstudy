"""Stage 3 · train — gradient boosting of BPS regency poverty rate on satellite features.

Target: BPS P0 (% poor) per kabupaten/kota, 2016-2025 (var 621); P1/P2 as secondary targets.
Model: LightGBM regressor (monotone constraints where physics demands: more small roofs → not
less poverty), plus a ridge baseline so the gain from non-linearity is reported honestly.

Cross-validation is SPATIAL, never random: leave-one-province-out (34 folds) as the headline;
spatial-block k-fold (200 km blocks) as the sensitivity check. Random k-fold is computed only
to show how much it flatters the score (methodology chart).

Reported (all out-of-sample): R², RMSE (pp), Spearman ρ, and the same three split Java /
off-Java and urban (kota) / rural (kabupaten) — gate G-F1 and disclosure G-F2.
Feature attribution: SHAP values per regency, exported for the drilldown panel.

Outputs: data/model/lgbm.txt, data/cv_predictions.parquet (regency × year × fold prediction),
data/shap_adm2.parquet, data/model_report.json.
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO train → ", config.MODEL_DIR, "| gates:", config.GATE_R2_MIN, config.GATE_SPEARMAN_MIN)


if __name__ == "__main__":
    main()
