"""Composition sweep: reproduce the paper's Figures 3-5 and its optimum mixtures.

Each figure scans CO2 mass fraction at a fixed operating point, printed in that
figure's own caption. Note that the three captions are NOT the same point:

    Fig 3  CO2/propane   T_EVAP = -80 C
    Fig 4  CO2/ethane    T_EVAP = -85 C
    Fig 5  CO2/ethylene  T_EVAP = -82 C

all at T_CAS,C = -25 C, T_COND = 30 C, DT = 5 C. The paper then compares the
three curves and selects CO2/ethylene. That comparison therefore rests on scans
taken at three different evaporation temperatures, which is what gate G-5 tests.

Figure 3 also plots COP running NEGATIVE (to about -2.5) between mass fractions
0.2 and 0.5, while Figures 4 and 5 stay positive throughout. A coefficient of
performance cannot be negative. This sweep records where the cycle is infeasible
instead of reporting a negative number for it.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from model import MIXTURES, solve  # noqa: E402

# Each figure's own caption, read off the published plots.
FIGURE_CONDITIONS = {
    "propane": {"figure": 3, "t_evap_c": -80.0, "reported_optimum_co2": 0.94},
    "ethane": {"figure": 4, "t_evap_c": -85.0, "reported_optimum_co2": 0.64},
    "ethylene": {"figure": 5, "t_evap_c": -82.0, "reported_optimum_co2": 0.37},
}
T_CAS_C = -25.0
T_COND = 30.0
DT = 5.0
ETA_IS = 0.65          # reconstructed; see model.py RECONSTRUCTION 2

STEP = 0.01


def scan(mixture: str, t_evap_c: float, eta_is: float = ETA_IS) -> list[dict]:
    """COP against CO2 mass fraction. Infeasible points are recorded, not faked."""
    rows = []
    x = 0.0
    while x <= 1.0 + 1e-9:
        row: dict = {"co2_mass_frac": round(x, 4)}
        # pure fluids are outside the mixture model's domain here
        if x <= 0.001 or x >= 0.999:
            row["status"] = "endpoint"
            rows.append(row)
            x += STEP
            continue
        try:
            res = solve(
                mixture=mixture, co2_mass_frac=x, t_evap_c=t_evap_c,
                t_cas_c_c=T_CAS_C, t_cond_c=T_COND, dt_cascade_k=DT, eta_is=eta_is,
            )
            if res.cop <= 0 or res.ltc.m_dot <= 0:
                # h1 <= h3: the "evaporator" gains no enthalpy, so the cycle does
                # not close. This is the region the published figure draws as a
                # negative COP.
                row.update(status="infeasible", reason="non-positive evaporator enthalpy rise")
            else:
                row.update(status="ok", cop=round(res.cop, 4),
                           eta_ex=round(res.eta_ex, 4),
                           annual_cost_usd=round(res.annual_cost_usd, 1))
        except Exception as e:  # CoolProp cannot solve this composition/state
            row.update(status="infeasible", reason=type(e).__name__)
        rows.append(row)
        x += STEP
    return rows


def main() -> None:
    out: dict = {"eta_is": ETA_IS, "t_cas_c_c": T_CAS_C, "t_cond_c": T_COND,
                 "dt_cascade_k": DT, "mixtures": {}}
    print(f"  composition sweep, eta_is = {ETA_IS}, T_CAS,C = {T_CAS_C} C, "
          f"T_COND = {T_COND} C, DT = {DT} C\n")
    print(f"  {'mixture':10} {'fig':>4} {'T_EVAP':>8} {'ours':>7} {'paper':>7} {'|diff|':>7} "
          f"{'COP*':>7} {'feasible':>9} {'infeasible':>11}")

    for mix, cfg in FIGURE_CONDITIONS.items():
        rows = scan(mix, cfg["t_evap_c"])
        ok = [r for r in rows if r["status"] == "ok"]
        bad = [r for r in rows if r["status"] == "infeasible"]
        if ok:
            best = max(ok, key=lambda r: r["cop"])
            ours = best["co2_mass_frac"]
            diff = abs(ours - cfg["reported_optimum_co2"])
            print(f"  {mix:10} {cfg['figure']:>4} {cfg['t_evap_c']:>7.0f}C "
                  f"{ours:7.2f} {cfg['reported_optimum_co2']:7.2f} {diff:7.2f} "
                  f"{best['cop']:7.3f} {len(ok):9} {len(bad):11}")
        else:
            print(f"  {mix:10} {cfg['figure']:>4} {cfg['t_evap_c']:>7.0f}C   no feasible composition")
        out["mixtures"][mix] = {
            "figure": cfg["figure"],
            "t_evap_c": cfg["t_evap_c"],
            "reported_optimum_co2_mass_frac": cfg["reported_optimum_co2"],
            "our_optimum_co2_mass_frac": (best["co2_mass_frac"] if ok else None),
            "our_max_cop": (best["cop"] if ok else None),
            "n_feasible": len(ok),
            "n_infeasible": len(bad),
            "infeasible_range": (
                [min(r["co2_mass_frac"] for r in bad), max(r["co2_mass_frac"] for r in bad)]
                if bad else None
            ),
            "rows": rows,
        }

    print("\n  infeasible composition ranges (where the published Fig 3 draws negative COP):")
    for mix, d in out["mixtures"].items():
        rng = d["infeasible_range"]
        print(f"    {mix:10} {('%.2f - %.2f CO2 mass fraction' % (rng[0], rng[1])) if rng else 'none'}")

    dest = pathlib.Path(__file__).parent.parent / "data" / "composition_sweep.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1))
    print(f"\n  wrote {dest.relative_to(dest.parents[2])}")


if __name__ == "__main__":
    main()
