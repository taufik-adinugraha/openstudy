"""Stage 6 · equity — how unequally access is shared.

  Lorenz curve + Gini of population-weighted jobs access (60 min, all transit) across
  kelurahan; Palma ratio (top 10 % / bottom 40 %).
  Access vs poverty: kelurahan access against Case F's kecamatan poverty estimates (the
  cross-case link) and against the regency BPS poverty rate — Spearman ρ, and the "double
  disadvantage" list: bottom-quintile welfare AND bottom-quintile access.
  Mode-layer attribution: Gini with and without rail; with and without TransJakarta.
  DKI vs Bodetabek split — the core/periphery story a Jakarta client already suspects.

Outputs: data/equity.json (curves, coefficients, lists) — small.
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO equity →", config.EQUITY_JSON)


if __name__ == "__main__":
    main()
