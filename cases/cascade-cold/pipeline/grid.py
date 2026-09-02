"""Sample the operating envelope once, so a browser never has to.

A single operating point costs ~115 ms natively and ~660 ms in a WASM build, because
each one runs dozens of iterative mixture VLE flashes. Live recomputation in the browser
is therefore not available at interactive speed — not a transport problem, a compute one.

So the envelope is sampled here, over exactly the decision-variable ranges the paper
declares, and the dashboard interpolates. That is weaker than recomputation and stronger
than shipping a handful of author-chosen scenarios, and the page says which it is. The
interpolation error is measured against exact points rather than assumed.
"""

from __future__ import annotations

import itertools
import json
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from model import MIXTURES, solve  # noqa: E402

# the paper's own ranges for its four decision variables
T_EVAP = [-80.0, -82.0, -84.0, -86.0, -88.0, -90.0]
T_CAS_C = [-40.0, -36.0, -32.0, -28.0, -24.0, -20.0, -16.0, -12.0, -8.0, -4.0, 0.0]
T_COND = [30.0, 32.0, 34.0, 36.0, 38.0, 40.0]
DT = [1.0, 3.0, 5.0, 7.0, 9.0, 11.0, 13.0, 15.0]
ETA_IS = 0.65

AXES = {"t_evap_c": T_EVAP, "t_cas_c_c": T_CAS_C, "t_cond_c": T_COND, "dt_cascade_k": DT}


def sample(mixture: str, co2: float):
    """COP, exergetic efficiency and annual cost at every node. None where infeasible."""
    out, ok, bad = [], 0, 0
    for te, tc, tk, dt in itertools.product(T_EVAP, T_CAS_C, T_COND, DT):
        try:
            r = solve(mixture=mixture, co2_mass_frac=co2, t_evap_c=te, t_cas_c_c=tc,
                      t_cond_c=tk, dt_cascade_k=dt, eta_is=ETA_IS)
            if r.cop <= 0 or r.cop > 5:
                out.append(None); bad += 1
            else:
                out.append([round(r.cop, 5), round(r.eta_ex, 5), round(r.annual_cost_usd, 1)])
                ok += 1
        except Exception:
            out.append(None); bad += 1
    return out, ok, bad


def main() -> None:
    t0 = time.time()
    grids = {}
    for mix, spec in MIXTURES.items():
        co2 = spec["reported_co2_mass_frac"]
        vals, ok, bad = sample(mix, co2)
        grids[mix] = {"co2_mass_frac": co2, "values": vals, "feasible": ok, "infeasible": bad}
        print(f"  {mix:10} {ok:5} feasible / {ok + bad:5} nodes   "
              f"{(time.time() - t0) / 60:5.1f} min elapsed", flush=True)

    doc = {
        "axes": AXES,
        "order": ["t_evap_c", "t_cas_c_c", "t_cond_c", "dt_cascade_k"],
        "outputs": ["cop", "eta_ex", "annual_cost_usd"],
        "eta_is": ETA_IS,
        "note": ("Sampled offline because one point costs ~115 ms: dozens of iterative "
                 "mixture VLE flashes. The dashboard interpolates between these nodes."),
        "mixtures": grids,
    }
    dest = pathlib.Path(__file__).parent.parent / "data" / "envelope.json"
    dest.write_text(json.dumps(doc, separators=(",", ":")))
    kb = len(dest.read_bytes()) // 1024
    print(f"\n  wrote {dest.name}  {kb} KB  in {(time.time() - t0)/60:.1f} min")
    print(f"  total nodes: {sum(len(g['values']) for g in grids.values()):,}")


if __name__ == "__main__":
    main()
