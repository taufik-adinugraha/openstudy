"""Findings that came out of making the envelope interrogable, recorded as JSON.

The gates in validate.py score this implementation against the paper. These are
different: they are things the model always knew and nobody had asked it, and they
surfaced only when the dashboard was rewritten to recompute rather than interpolate.

Written to data/findings.json so the note's prose reads them instead of restating
them. Every one of these was a sentence somebody would otherwise have typed once
and let go stale.

Uses verify.py's `reconstruct`, which is the same closed form the dashboard runs and
which verify.py itself proves reproduces `solve()` to 5e-13. So there is one
implementation of this algebra in Python and one in the browser, and a test tying
them together.
"""

from __future__ import annotations

import collections
import datetime as _dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from model import CASCADE_COST_COEFF, CASCADE_COST_COEFF_AS_PRINTED  # noqa: E402
from validate import REPORTED_OPTIMA  # noqa: E402
from verify import load, reconstruct  # noqa: E402

ATM_BAR = 1.01325


def cost_min_dt(d, ax, coeff):
    """Which cascade approach minimises annual cost, over everything else?"""
    tally = collections.Counter()
    n = 0
    for m in d["lt"]:
        for te in ax["t_evap_c"]:
            for tc in ax["t_cas_c_c"]:
                for tk in ax["t_cond_c"]:
                    best = None
                    for dt in ax["dt_cascade_k"]:
                        r = reconstruct(d, ax, m, te, tc, tk, dt, coeff=coeff)
                        if "why" in r:
                            continue
                        if best is None or r["annual_cost_usd"] < best[1]:
                            best = (dt, r["annual_cost_usd"])
                    if best:
                        tally[best[0]] += 1
                        n += 1
    ks = sorted(tally)
    top = tally.most_common(1)[0]
    return {"coefficient": coeff, "combinations": n,
            "range_k": [ks[0], ks[-1]] if ks else None,
            "most_common_k": top[0], "most_common_share": round(top[1] / n, 4),
            "distribution": {str(k): tally[k] for k in ks}}


def main() -> None:
    d, ax = load()

    # ── 1. nodes the Carnot test refused ────────────────────────────────────
    refused = {}
    for m, g in d["lt"].items():
        rows = g["eta"][str(d["eta_is"])]
        bad = collections.Counter()
        temps = set()
        for i, r in enumerate(rows):
            if "why" not in r:
                continue
            bad["carnot" if "Carnot" in r["why"] else "solver"] += 1
            temps.add(ax["t_cas_c_c"][i % len(ax["t_cas_c_c"])])
        refused[m] = {"feasible": g["feasible"], "nodes": len(rows),
                      "beat_carnot": bad["carnot"], "solver_failed": bad["solver"],
                      "cascade_condensing_c": sorted(temps)}

    # ── 2. how much of the envelope runs the low side under vacuum ──────────
    sub = {}
    for m in d["lt"]:
        n = ok = 0
        lo = hi = None
        for te in ax["t_evap_c"]:
            for tc in ax["t_cas_c_c"]:
                r = reconstruct(d, ax, m, te, tc, ax["t_cond_c"][2], 5.0)
                if "why" in r:
                    continue
                n += 1
                p = r["lt_p_evap_bar"]
                lo = p if lo is None else min(lo, p)
                hi = p if hi is None else max(hi, p)
                ok += p < ATM_BAR
        sub[m] = {"feasible_pairs": n, "sub_atmospheric": ok,
                  "share": round(ok / n, 4) if n else None,
                  "suction_bar_range": [round(lo, 3), round(hi, 3)] if n else None}

    # ── 3. the cost-minimising approach, at both coefficients ───────────────
    dt_inferred = cost_min_dt(d, ax, CASCADE_COST_COEFF)
    dt_printed = cost_min_dt(d, ax, CASCADE_COST_COEFF_AS_PRINTED)

    # ── 4. what the paper's own reported approach costs, in steel and money ─
    at_optima = {}
    for m, o in REPORTED_OPTIMA.items():
        # the reported optima sit off-grid; take the nearest tabulated node
        te = min(ax["t_evap_c"], key=lambda v: abs(v - o["t_evap"]))
        tc = min(ax["t_cas_c_c"], key=lambda v: abs(v - o["t_cas"]))
        tk = min(ax["t_cond_c"], key=lambda v: abs(v - o["t_cond"]))
        rows = {}
        for dt in (1.0, dt_inferred["most_common_k"]):
            r = reconstruct(d, ax, m, te, tc, tk, float(dt))
            if "why" in r:
                continue
            rows[str(dt)] = {"cascade_area_m2": round(r["a_cascade_m2"], 3),
                             "annual_cost_usd": round(r["annual_cost_usd"]),
                             "cop": round(r["cop"], 4),
                             "eta_ex": round(r["eta_ex"], 4)}
        at_optima[m] = {"reported_dt_k": o["dt"], "nearest_node": [te, tc, tk],
                        "at_dt": rows}

    # ── 5. pressure ratios across the envelope, against one flat efficiency ─
    prs = []
    for m in d["lt"]:
        for te in ax["t_evap_c"]:
            for tc in ax["t_cas_c_c"]:
                for dt in ax["dt_cascade_k"]:
                    r = reconstruct(d, ax, m, te, tc, ax["t_cond_c"][-1], dt)
                    if "why" not in r:
                        prs.append((r["lt_pr"], r["ht_pr"]))
    lt_pr = [p[0] for p in prs]
    ht_pr = [p[1] for p in prs]

    out = {
        "case": "cascade-cold",
        "generated": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": ("Findings from making the operating envelope interrogable. These are "
                 "not gates: they are things the model always contained and nobody had "
                 "asked it."),
        "property_solver": {
            "library": "CoolProp 8.0.0",
            "per_mixture": refused,
            "what_it_means": ("A circuit cannot beat Carnot between its own two "
                              "temperatures. Where the tables refuse a node on that "
                              "test, the library returned a spurious saturated-liquid "
                              "root: for CO2/ethane at a cascade condensing temperature "
                              "of -4 C it gives -465 kJ/kg against +243 kJ/kg one kelvin "
                              "away. Unfiltered those points reached a COP of 1.73 at "
                              "-80 C, 94% of Carnot, and sat at the top of the "
                              "dashboard's own Pareto front."),
        },
        "sub_atmospheric_low_side": {
            "threshold_bar": ATM_BAR,
            "at_condenser_c": ax["t_cond_c"][2],
            "at_dt_k": 5.0,
            "per_mixture": sub,
            "what_it_means": ("Where the low circuit's suction sits below atmospheric, "
                              "the cold side of the plant is under vacuum at standstill "
                              "and any leak draws air and moisture into a -80 C "
                              "circuit. The paper does not report circuit pressures, so "
                              "this constraint is invisible in it."),
        },
        "cost_minimising_cascade_approach": {
            "inferred_coefficient": dt_inferred,
            "printed_coefficient": dt_printed,
            "what_it_means": ("The tenfold reduction in the cascade-exchanger cost "
                              "coefficient does not push the optimum to the smallest "
                              "approach — it is the printed coefficient that pins it to "
                              "the largest. The paper's own reported optima at 1.5-1.8 K "
                              "are not cost minima under either coefficient; they are "
                              "points chosen at the efficiency end of a two-objective "
                              "front."),
        },
        "at_the_reported_optima": at_optima,
        "pressure_ratio_range": {
            "low_circuit": [round(min(lt_pr), 2), round(max(lt_pr), 2)],
            "high_circuit": [round(min(ht_pr), 2), round(max(ht_pr), 2)],
            "points": len(prs),
            "what_it_means": ("One isentropic efficiency, 0.65, is applied across all of "
                              "these. Real isentropic efficiency falls with pressure "
                              "ratio, and the paper says it had that function and never "
                              "printed it, so the cold end of the envelope is flattered "
                              "by an unknown amount."),
        },
    }
    dest = pathlib.Path(__file__).parent.parent / "data" / "findings.json"
    dest.write_text(json.dumps(out, indent=1))

    print("  property solver refusals")
    for m, r in refused.items():
        print(f"    {m:10} {r['feasible']:4}/{r['nodes']} feasible · "
              f"{r['beat_carnot']} beat Carnot · {r['solver_failed']} failed to converge")
    print("\n  low side under vacuum (at "
          f"{ax['t_cond_c'][2]:.0f} C condenser, 5 K approach)")
    for m, r in sub.items():
        print(f"    {m:10} {r['sub_atmospheric']:3}/{r['feasible_pairs']:3} pairs "
              f"({100 * (r['share'] or 0):4.1f}%) · suction "
              f"{r['suction_bar_range'][0]}–{r['suction_bar_range'][1]} bar")
    print("\n  cost-minimising cascade approach")
    for label, r in (("inferred 2382.9", dt_inferred), ("printed 23829", dt_printed)):
        print(f"    {label:16} {r['range_k'][0]:.0f}–{r['range_k'][1]:.0f} K, "
              f"most often {r['most_common_k']:.0f} K "
              f"({100 * r['most_common_share']:.0f}% of {r['combinations']:,})")
    print(f"\n  pressure ratio  low {min(lt_pr):.1f}–{max(lt_pr):.1f} · "
          f"high {min(ht_pr):.1f}–{max(ht_pr):.1f}  over {len(prs):,} points")
    print(f"\n  wrote {dest.name}")


if __name__ == "__main__":
    main()
