"""Tabulate the two circuits separately, so the browser can recompute exactly.

The first version of the dashboard sampled the whole four-dimensional envelope —
9,504 nodes at ~115 ms each — and interpolated between them. That was honest but
weak, and a reviewing engineer put his finger on exactly where it hurt: a linear
interpolant between two feasible nodes will hand you a smooth, confident, wrong
number near the feasibility cliff.

It turns out none of that was necessary. Look at what `solve` actually does:

    ltc = Circuit(mixture, t_evap_c = T_EVAP, t_cond_c = T_CAS,C)
    htc = Circuit(propane, t_evap_c = T_CAS,C - DT, t_cond_c = T_COND,
                  q_evap_w = ltc.q_cond_w)

The low circuit depends on **two** of the four decision variables and nothing
else. The high circuit's state depends on the other two, and its duty enters
only as a linear scale on mass flow. So the whole envelope factorises:

    w_H      = q_cond_L * k_w(T_CAS,E, T_COND)          k_w = (h2-h1)/((h1-h3)*eta_elmech)
    q_cond_H = q_cond_L * r_q(T_CAS,E, T_COND)          r_q = (h2-h3)/(h1-h3)

and everything the paper's objective functions need — COP, exergetic
efficiency, all five capital terms, the annual cost — follows in closed form
from two small tables. A 6x21 table per mixture and one 55x11 table for the
shared propane circuit describe 62,370 operating points **exactly**, in about
15 KB, with no interpolation anywhere and no sampled envelope at all.

That buys three things the sampled version could not offer:

1. Every number on the dashboard is a recomputation, not a lookup between nodes.
2. The economic assumptions stop being baked in. Electricity price, running
   hours, lifetime, interest rate and the disputed cascade-exchanger cost
   coefficient all enter *after* the thermodynamics, so the reader can move them
   and watch the optimum move — including seeing for themselves what the printed
   coefficient does versus the one we inferred.
3. Engineering units come free. Suction and discharge pressure per circuit,
   both pressure ratios, mass flows, and the cascade exchanger area were all
   computed and then thrown away. They are the numbers a practitioner asks for
   first, and the sub-atmospheric standstill they imply on the low side is a
   real design constraint the paper never mentions.

`verify.py` checks the reconstruction against `solve()` at random points. It has
to agree to floating-point noise, or this file is wrong.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import CoolProp.CoolProp as CP  # noqa: E402

from model import (  # noqa: E402
    ETA_ELMECH,
    HTC_FLUID,
    MIXTURES,
    Q_EVAP_W,
    Circuit,
    _mix_name,
    mass_to_mole_fractions,
)

# The paper never states a compressor efficiency. Inverting its own three reported
# optima gives 0.642 (propane), 0.649 (ethane) and 0.662 (ethylene) — see README
# RECONSTRUCTION 2. Every number the dashboard prints therefore rests on a parameter
# with a real spread, so all three are tabulated and the page shows the band. The
# middle value is the one the pipeline has always used.
ETA_SET = [0.642, 0.65, 0.662]
ETA_IS = 0.65

# The paper's own decision-variable ranges, on a finer mesh than the sampled
# version could afford. T_EVAP keeps the paper's 2 K steps because that is the
# range it declares; the other three are now fine enough that the sliders no
# longer quantise the answer.
T_EVAP = [-80.0 - 2.0 * i for i in range(6)]                  # -80 .. -90
T_CAS_C = [-40.0 + 2.0 * i for i in range(21)]                # -40 .. 0
T_COND = [30.0 + 1.0 * i for i in range(11)]                  # 30 .. 40
DT = [1.0 + 1.0 * i for i in range(15)]                       # 1 .. 15

# The high circuit evaporates at T_CAS,C - DT, so it only ever sees these.
T_CAS_E = sorted({round(tc - dt, 6) for tc in T_CAS_C for dt in DT})


# CoolProp 8.0.0 fails to converge at isolated nodes for the two mixtures whose
# partner sits nearest its own critical temperature. Those nodes are refused rather
# than filled in, because a value guessed between two converged neighbours is
# indistinguishable from a value the model computed.
SOLVER_FAILED = ("the refrigerant property library could not converge on a state "
                 "here (CoolProp 8.0.0, solver_rho_Tp)")


def carnot_cop(t_cold_c: float, t_hot_c: float) -> float:
    """The best any device can do lifting heat from t_cold to t_hot."""
    tc = t_cold_c + 273.15
    return tc / ((t_hot_c + 273.15) - tc)


def lt_node(fluid: str, t_evap_c: float, t_cas_c_c: float,
            eta: float = ETA_IS) -> dict | None:
    """Low-temperature circuit at one (T_EVAP, T_CAS,C). None where it cannot close."""
    try:
        c = Circuit(fluid=fluid, t_evap_c=t_evap_c, t_cond_c=t_cas_c_c,
                    eta_is=eta, q_evap_w=Q_EVAP_W, glide="dew")
    except Exception as e:
        return {"why": SOLVER_FAILED, "detail": type(e).__name__}
    if not (c.h1 > c.h3):
        # The published Figure 3 plots a negative COP through this region. A
        # coefficient of performance cannot be negative; what is actually
        # happening is that the evaporator gains no enthalpy and the cycle stops
        # closing. Recorded as a reason rather than as a number.
        return {"why": "evaporator enthalpy rise is not positive"}
    if c.m_dot <= 0 or c.w_comp_w <= 0:
        return {"why": "non-physical mass flow or compressor work"}
    # A circuit cannot beat Carnot between its own two temperatures. This is not a
    # modelling nicety, it caught a real defect: for CO2/ethane at 0.64 mass
    # fraction, CoolProp 8.0.0 returns a saturated-liquid enthalpy of -465 kJ/kg
    # at a condensing temperature of -4 C against +243 kJ/kg one kelvin lower, and
    # throws outright at -6, -4.5, -2 and 0 C. A 700 kJ/kg step in a saturated
    # liquid enthalpy over 1 K is a failed density solve, not a phase transition.
    # Unfiltered it produced a COP of 1.73 at -80 C — 94% of Carnot for a cascade
    # with a 0.65 compressor — and those points sat at the top of the published
    # Pareto front, setting its axis.
    if Q_EVAP_W / c.w_comp_w > carnot_cop(t_evap_c, t_cas_c_c):
        return {"why": "the property solver returned a spurious saturated-liquid root "
                       "here: the circuit comes out better than Carnot"}
    return {
        "pe": round(c.p_evap, 1),          # Pa, suction
        "pc": round(c.p_cond, 1),          # Pa, discharge
        # Precision matters here: these two feed every derived number, and rounding
        # them to 6 dp left verify.py disagreeing with solve() at 1.2e-9 relative —
        # the published table's own rounding, not the algebra. 10 dp puts the
        # reconstruction below 1e-11.
        "md": round(c.m_dot, 12),          # kg/s
        "wl": round(c.w_comp_w, 10),       # W, electrical
        "qc": round(c.q_cond_w, 10),       # W, rejected into the cascade exchanger
    }


def ht_node(t_evap_c: float, t_cond_c: float, eta: float = ETA_IS) -> dict | None:
    """Propane circuit as two duty-independent ratios, plus its pressures."""
    if t_evap_c >= t_cond_c:
        return {"why": "high circuit cannot evaporate above its own condensing temperature"}
    try:
        te, tc = t_evap_c + 273.15, t_cond_c + 273.15
        p_evap = CP.PropsSI("P", "T", te, "Q", 1, HTC_FLUID)
        p_cond = CP.PropsSI("P", "T", tc, "Q", 0, HTC_FLUID)
        h1 = CP.PropsSI("H", "P", p_evap, "Q", 1, HTC_FLUID)
        s1 = CP.PropsSI("S", "P", p_evap, "Q", 1, HTC_FLUID)
        h2s = CP.PropsSI("H", "P", p_cond, "S", s1, HTC_FLUID)
        h2 = h1 + (h2s - h1) / eta
        h3 = CP.PropsSI("H", "P", p_cond, "Q", 0, HTC_FLUID)
    except Exception as e:
        return {"why": SOLVER_FAILED, "detail": type(e).__name__}
    if not (h1 > h3):
        return {"why": "evaporator enthalpy rise is not positive"}
    # same Carnot test as the low circuit. w = q_evap * kw * eta_elmech / ... — for a
    # duty-independent form, COP_HT = (h1-h3)/(h2-h1) * eta_elmech.
    if ((h1 - h3) / (h2 - h1)) * ETA_ELMECH > carnot_cop(t_evap_c, t_cond_c):
        return {"why": "the property solver returned a spurious saturated-liquid root "
                       "here: the circuit comes out better than Carnot"}
    return {
        "pe": round(p_evap, 1),
        "pc": round(p_cond, 1),
        # w_H = q_cond_L * kw ; q_cond_H = q_cond_L * rq
        "kw": round((h2 - h1) / ((h1 - h3) * ETA_ELMECH), 12),
        "rq": round((h2 - h3) / (h1 - h3), 12),
    }


def main() -> None:
    t0 = time.time()

    lt: dict[str, dict] = {}
    for mix, spec in MIXTURES.items():
        co2 = spec["reported_co2_mass_frac"]
        co2_mole, _ = mass_to_mole_fractions(co2, spec["partner"])
        fluid = _mix_name(spec["partner"], co2_mole)
        per_eta, counts = {}, {}
        for eta in ETA_SET:
            rows, ok, bad = [], 0, 0
            for te in T_EVAP:
                for tc in T_CAS_C:
                    n = lt_node(fluid, te, tc, eta)
                    rows.append(n)
                    ok, bad = (ok + 1, bad) if "why" not in n else (ok, bad + 1)
            per_eta[f"{eta}"] = rows
            counts[f"{eta}"] = ok
            print(f"  LT {mix:10} eta {eta}  {ok:4} feasible / {ok + bad:4}   "
                  f"{time.time() - t0:5.1f}s", flush=True)
        lt[mix] = {"co2_mass_frac": co2, "eta": per_eta,
                   "feasible": counts[f"{ETA_IS}"], "nodes_per_eta": len(T_EVAP) * len(T_CAS_C)}

    ht: dict[str, list] = {}
    for eta in ETA_SET:
        rows, ok, bad = [], 0, 0
        for te in T_CAS_E:
            for tc in T_COND:
                n = ht_node(te, tc, eta)
                rows.append(n)
                ok, bad = (ok + 1, bad) if "why" not in n else (ok, bad + 1)
        ht[f"{eta}"] = rows
        print(f"  HT {HTC_FLUID:10} eta {eta}  {ok:4} feasible / {ok + bad:4}   "
              f"{time.time() - t0:5.1f}s", flush=True)

    doc = {
        "axes": {"t_evap_c": T_EVAP, "t_cas_c_c": T_CAS_C, "t_cond_c": T_COND,
                 "dt_cascade_k": DT, "t_cas_e_c": T_CAS_E},
        "eta_set": ETA_SET,
        "eta_is": ETA_IS,
        "eta_elmech": ETA_ELMECH,
        "q_evap_w": Q_EVAP_W,
        "t0_c": 25.0,
        "note": ("The two circuits are separable: the low circuit depends only on "
                 "T_EVAP and T_CAS,C, and the high circuit's duty enters linearly. "
                 "So these tables reconstruct every operating point in the envelope "
                 "exactly, with no interpolation. verify.py checks that against "
                 "solve() at random points."),
        "lt": lt,                                      # [eta][t_evap][t_cas_c] per mixture
        "ht": {"fluid": HTC_FLUID, "eta": ht},         # [eta][t_cas_e][t_cond]
        "envelope_points": len(T_EVAP) * len(T_CAS_C) * len(T_COND) * len(DT) * len(MIXTURES),
    }
    dest = pathlib.Path(__file__).parent.parent / "data" / "circuits.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, separators=(",", ":")))
    kb = len(dest.read_bytes()) / 1024
    n_nodes = sum(len(r) for v in lt.values() for r in v["eta"].values()) \
        + sum(len(r) for r in ht.values())
    print(f"\n  wrote {dest.name}  {kb:.0f} KB in {time.time() - t0:.1f}s")
    print(f"  {n_nodes:,} tabulated nodes describe {doc['envelope_points']:,} operating "
          f"points exactly, at each of {len(ETA_SET)} compressor efficiencies")


if __name__ == "__main__":
    main()
