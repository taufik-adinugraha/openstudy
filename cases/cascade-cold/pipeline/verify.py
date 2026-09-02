"""Does the browser's closed form reproduce solve() exactly?

circuits.json claims that two small tables describe the whole envelope with no
interpolation. That claim is worth nothing unless it is tested, so this file
implements the reconstruction the *dashboard* uses — the same algebra, in the
same order — and compares it against `solve()` at random operating points.

If this does not agree to floating-point noise, the dashboard is lying and
circuits.py is wrong. A tolerance loose enough to hide a modelling difference
would defeat the purpose, so it is set at 1e-9 relative.
"""

from __future__ import annotations

import json
import math
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from circuits import carnot_cop  # noqa: E402
from model import (  # noqa: E402
    CASCADE_COST_COEFF,
    COST_ELEC_PER_KWH,
    FAN_KW,
    HOURS_PER_YEAR,
    MIXTURES,
    Q_EVAP_W,
    T0_C,
    T_DROP_K,
    U_CASCADE,
    U_COND,
    U_EVAP,
    crf,
    solve,
)

DATA = pathlib.Path(__file__).parent.parent / "data" / "circuits.json"
TOL = 1e-9


def load():
    d = json.loads(DATA.read_text())
    ax = d["axes"]
    return d, ax


def reconstruct(d, ax, mixture, te, tc, tk, dt, coeff=CASCADE_COST_COEFF,
                elec=COST_ELEC_PER_KWH, hours=HOURS_PER_YEAR, rate=0.08, life=10,
                eta=None):
    """Exactly the algebra the dashboard runs, from the two tables alone."""
    ekey = f"{eta if eta is not None else d['eta_is']}"
    i_te, i_tc = ax["t_evap_c"].index(te), ax["t_cas_c_c"].index(tc)
    lt = d["lt"][mixture]["eta"][ekey][i_te * len(ax["t_cas_c_c"]) + i_tc]
    if "why" in lt:
        return {"why": lt["why"], "where": "low circuit"}

    t_cas_e = round(tc - dt, 6)
    i_e, i_k = ax["t_cas_e_c"].index(t_cas_e), ax["t_cond_c"].index(tk)
    ht = d["ht"]["eta"][ekey][i_e * len(ax["t_cond_c"]) + i_k]
    if "why" in ht:
        return {"why": ht["why"], "where": "high circuit"}

    w_l = lt["wl"]
    q_cascade = lt["qc"]                     # heat handed from the low circuit up
    w_h = q_cascade * ht["kw"]
    q_cond_h = q_cascade * ht["rq"]

    w_total = w_l + w_h
    cop = Q_EVAP_W / w_total

    te_k = te + 273.15
    cop_carnot = te_k / ((T0_C + 273.15) - te_k)
    eta_ex = cop / cop_carnot
    if not (0 < eta_ex < 1):
        # A refrigerator cannot beat Carnot between the same two reservoirs.
        return {"why": "would exceed the Carnot limit", "where": "cycle"}

    w_h_kw, w_l_kw = w_h / 1000.0, w_l / 1000.0
    a_cond = q_cond_h / (U_COND * (tk - T0_C))
    a_evap = Q_EVAP_W / (U_EVAP * T_DROP_K)
    a_cascade = q_cascade / (U_CASCADE * dt)

    fan = 629.05 * FAN_KW**0.76
    capital = (9624.2 * w_h_kw**0.46 + 10167.5 * w_l_kw**0.46
               + 1397 * a_cond**0.89 + fan
               + 1397 * a_evap**0.89 + fan
               + coeff * a_cascade**0.68)
    running_kw = w_h_kw + w_l_kw + 2 * FAN_KW
    annual = capital * crf(rate, life) + elec * hours * running_kw

    return {
        "cop": cop, "eta_ex": eta_ex, "annual_cost_usd": annual,
        "capital_usd": capital, "w_total_kw": w_total / 1000.0,
        # the engineering units the paper computes and never reports
        "lt_p_evap_bar": lt["pe"] / 1e5, "lt_p_cond_bar": lt["pc"] / 1e5,
        "lt_pr": lt["pc"] / lt["pe"], "lt_m_dot_g_s": lt["md"] * 1000.0,
        "ht_p_evap_bar": ht["pe"] / 1e5, "ht_p_cond_bar": ht["pc"] / 1e5,
        "ht_pr": ht["pc"] / ht["pe"],
        "a_cascade_m2": a_cascade, "a_cond_m2": a_cond, "a_evap_m2": a_evap,
        "q_cascade_w": q_cascade,
    }


def main() -> int:
    d, ax = load()
    random.seed(20260901)

    checked = skipped = refused = 0
    worst = {"rel": 0.0, "at": None, "field": None}
    for _ in range(400):
        mix = random.choice(list(MIXTURES))
        te = random.choice(ax["t_evap_c"])
        tc = random.choice(ax["t_cas_c_c"])
        tk = random.choice(ax["t_cond_c"])
        dt = random.choice(ax["dt_cascade_k"])

        eta = random.choice(d["eta_set"])
        got = reconstruct(d, ax, mix, te, tc, tk, dt, eta=eta)
        try:
            want = solve(mixture=mix, co2_mass_frac=MIXTURES[mix]["reported_co2_mass_frac"],
                         t_evap_c=te, t_cas_c_c=tc, t_cond_c=tk, dt_cascade_k=dt,
                         eta_is=eta)
        except Exception:
            # solve() raised; the reconstruction must also refuse this point
            if "why" not in got:
                print(f"  MISMATCH  solve() failed but the tables produced a number "
                      f"at {mix} {te}/{tc}/{tk}/{dt} eta {eta}")
                return 1
            skipped += 1
            continue

        if "why" in got:
            # The tables refuse a node where either circuit beats Carnot between its
            # own two temperatures. solve() applies no such test — it implements the
            # paper's model as written — so a refusal here is legitimate exactly when
            # solve()'s own circuits fail that test. Anything else is a real mismatch.
            lt_bad = (Q_EVAP_W / want.ltc.w_comp_w
                      > carnot_cop(want.ltc.t_evap_c, want.ltc.t_cond_c))
            ht_bad = (want.htc.q_evap_w / want.htc.w_comp_w
                      > carnot_cop(want.htc.t_evap_c, want.htc.t_cond_c))
            if not (lt_bad or ht_bad or not (0 < want.eta_ex < 1)):
                print(f"  MISMATCH  tables refused ({got['why']}) but solve() gave a "
                      f"physical COP {want.cop:.4f} at {mix} {te}/{tc}/{tk}/{dt}")
                return 1
            refused += 1
            continue

        for f, w in (("cop", want.cop), ("eta_ex", want.eta_ex),
                     ("annual_cost_usd", want.annual_cost_usd),
                     ("capital_usd", want.capital_usd),
                     ("w_total_kw", want.w_total_kw)):
            rel = abs(got[f] - w) / max(abs(w), 1e-12)
            if rel > worst["rel"]:
                worst = {"rel": rel, "at": f"{mix} {te}/{tc}/{tk}/{dt} eta {eta}", "field": f}
        checked += 1

    print(f"  {checked} points reconstructed and compared against solve(), across all three compressor efficiencies")
    print(f"  {skipped} points where solve() itself has no answer at all")
    print(f"  {refused} points refused by the Carnot test, all confirmed non-physical "
          "in solve() too")
    print(f"  worst relative disagreement {worst['rel']:.2e} "
          f"({worst['field']} at {worst['at']})")

    if worst["rel"] > TOL:
        print(f"\n  FAIL — above the {TOL:.0e} tolerance. The dashboard's closed form "
              "does not reproduce the model.")
        return 1
    print(f"\n  PASS — the closed form reproduces the model to within {TOL:.0e}.")

    # a few named points, printed so the engineering units are on the record
    print("\n  engineering units the paper computes and never reports:")
    hdr = ("point", "COP", "LT suct", "LT PR", "HT PR", "cascade A", "annual")
    print("  {:<26} {:>6} {:>9} {:>6} {:>6} {:>10} {:>9}".format(*hdr))
    for mix in MIXTURES:
        for dt in (1.0, 5.0, 15.0):
            r = reconstruct(d, ax, mix, -80.0, -28.0, 32.0, dt)
            if "why" in r:
                continue
            print(f"  {mix + f', DT {dt:.0f} K':<26} {r['cop']:6.3f} "
                  f"{r['lt_p_evap_bar']:8.3f}b {r['lt_pr']:6.1f} {r['ht_pr']:6.1f} "
                  f"{r['a_cascade_m2']:9.2f}m2 ${r['annual_cost_usd']:8,.0f}")

    sub = [(m, te, tc) for m in MIXTURES for te in ax["t_evap_c"] for tc in ax["t_cas_c_c"]]
    n_sub = 0
    for m, te, tc in sub:
        r = reconstruct(d, ax, m, te, tc, 32.0, 5.0)
        if "why" not in r and r["lt_p_evap_bar"] < 1.01325:
            n_sub += 1
    print(f"\n  low-side suction below atmospheric at {n_sub} of {len(sub)} "
          "(T_EVAP, T_CAS,C) combinations — a standstill air-ingress risk the paper "
          "never mentions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
