# cascade-cold — replication of a published thermoeconomic optimisation

**Status: published.** The replication note is at `/notes/cascade-refrigeration/` and the
explorer at `/notes/cascade-explorer/`. The pipeline, the model, the gates, the circuit
tabulation, its equivalence test and the findings record all run.

## What this replicates

Nasruddin, Arnas, Faqih & Giannetti (2016), *"Thermoeconomic Optimization of Cascade
Refrigeration System Using Mixed Carbon Dioxide and Hydrocarbons at Low Temperature
Circuit"*, **Makara Journal of Technology 20(3) 132–138**,
doi:[10.7454/mst.v20i3.3068](https://doi.org/10.7454/mst.v20i3.3068).

A cascade refrigeration system reaching −80 °C for biomedical cold storage: propane in the
high-temperature circuit, a CO₂/hydrocarbon mixture in the low-temperature circuit,
optimised on two objectives — maximise exergetic efficiency, minimise total annual cost.

## Licences

| Thing | Licence |
|---|---|
| The source paper | **CC BY-NC-ND 4.0** — open access, no derivatives, non-commercial |
| Refrigerant properties | **CoolProp** 8.0.0, MIT |
| This implementation | MIT, like the rest of the repository |
| This case's published outputs | CC BY 4.0, like every other instrument |

**The ND clause is why this is a clean-room rebuild.** Copyright covers expression, not
facts or methods, so re-implementing a method from its published description and publishing
our own numbers is new work and not a derivative. But it means: no figure from the paper is
reproduced, no table is republished as a dataset, and its reported values appear only as
thresholds this implementation is scored against. That is ordinary scholarly comparison.

Nothing here needed the author's data, code or permission. Everything came from the PDF.

## Three reconstructions the paper does not state

The author confirmed he no longer has the first two.

**1 · Exergetic efficiency definition — resolved, and verified against his own axis.**
The paper maximises "exergy efficiency" and never defines it. Using the standard
second-law efficiency (its own ref [14], Bejan/Tsatsaronis):

```
eta_ex = Q_E * (T0/T_E - 1) / W_total  ==  COP / COP_carnot
```

At T_E = −80 °C and T₀ = 25 °C, COP_carnot = 1.839, so his reported COP of 0.65 gives
**35.4%** — and his ethylene Pareto front tops out near 35%. Read backwards, his 22–35%
axis implies COP 0.40–0.64, exactly the range of his Figures 3–5. The definition
reproduces the axis, so this is a verified reconstruction rather than a guess.

**2 · Compressor isentropic efficiency — not recoverable, so inverted instead.**
The paper says compression is "expressed as a function of pressure ratio" and never gives
the function. Rather than assume one, `invert_eta_is` asks what value his own reported COP
implies at his own reported optima:

| mixture | implied η_is |
|---|---|
| CO₂/propane | 0.642 |
| CO₂/ethane | 0.649 |
| CO₂/ethylene | 0.662 |

Three independent fluids agreeing to ±0.01 is the evidence. **His compressor ran near
η_is ≈ 0.65**, and that is now recoverable.

**3 · Cascade exchanger cost coefficient — adopted with the author's agreement, and
disclosed.** Eq. 8 is printed as `C = 23829 · A^0.68`. Implemented exactly as printed,
along with every other correlation as given, his three reported optima cost
**$19,873–21,924/yr against his own reported Pareto range of about $5,100–9,100/yr** — a
factor of 2.4, localising entirely to this one term (the cascade exchanger alone is
$87,031 of a $128,577 capital, because a 1.5 K approach means a large area).

Dividing this one coefficient by ten puts all three inside their published bands. That is
consistent with a lost decimal point, but **it is our inference**. Both values ship:
`CASCADE_COST_COEFF = 2382.9` is used, `CASCADE_COST_COEFF_AS_PRINTED = 23829.0` stays in
the code, and `gates.json` publishes both numbers.

## One modelling choice that decides everything

CO₂/hydrocarbon mixtures glide by tens of kelvin, so "the evaporation temperature" is
ambiguous for them. Three conventions were tested against the *shape* of his published
curves:

| convention | result |
|---|---|
| **dew** (vapour leaves the evaporator at T_evap) | reproduces the shape of all three figures |
| bubble | gives COP > 1 for propane, and inverts the ethylene trend |
| mean | inverts the ethylene trend |

`dew` is used. It also explains **Figure 3's negative COP**: the mid-composition region is
where the evaporator enthalpy rise collapses and the cycle stops closing. His model
returned negative numbers there and plotted them; ours bottoms out at 0.436 in the same
place and records those points as infeasible instead.

## Gates — thresholds fixed before the runs

| gate | hard | outcome | what it means |
|---|---|---|---|
| **G-1** optimum composition reproduced | hard | **FAIL** | Our COP maximum sits at a composition-range endpoint for all three mixtures; his are interior (0.94 / 0.64 / 0.37). He selects composition by "maximizing COP, provided that the carbon dioxide did not undergo crystallization and values capable of burning (flammability) of hydrocarbons were reduced" — **neither limit is quantified anywhere in the paper**, so the selection rule cannot be reproduced. His optima are constrained choices whose constraints are not published. Ideal-solubility freeze-out does not reproduce them either. |
| **G-2** COP within 0.05 | hard | PASS | worst deviation **0.015** |
| **G-3** implied η_is agrees across mixtures within 0.05 | soft | PASS | spread **0.021** |
| **G-4** annual cost inside the reported Pareto band | hard | PASS | 3 of 3, with reconstruction 3 |
| **G-5** mixture ranking survives a common T_EVAP | hard | **FAIL** | His three composition scans are captioned at **−80, −85 and −82 °C** (Figs 3, 4, 5), and he then compares those curves to select CO₂/ethylene. At a common −80 °C, **propane wins** (COP 0.675). |

Both failures are statements about **this run** against his reported numbers, not verdicts
on the original work (house rule J1).

## Open questions for the author

1. The U-value sentence lists 18.03, 6.85 and 64.87 W/m²K against evaporator, condenser and
   cascade exchanger with broken punctuation. We assume that mapping — an air-cooled
   condenser being the low one — but it is a guess.
2. The printed T_EVAP decision-variable range reads "80 °C to 90 °C"; Table 1 makes clear it
   is −80 to −90.

## The envelope is separable, so the browser recomputes it

The dashboard first sampled all 9,504 nodes of the four-dimensional envelope and
interpolated. That was unnecessary. In `solve()`, the low circuit depends on **two** of
the four decision variables and nothing else, and the high circuit's duty enters only as
a linear scale on mass flow:

```
w_H      = q_cond_L * k_w(T_CAS,E, T_COND)      k_w = (h2-h1)/((h1-h3)*eta_elmech)
q_cond_H = q_cond_L * r_q(T_CAS,E, T_COND)      r_q = (h2-h3)/(h1-h3)
```

So a 6×21 table per mixture plus one 55×11 propane table — 2,949 nodes at three
compressor efficiencies — reconstruct all **62,370** operating points in closed form,
with no interpolation. `verify.py` checks that reconstruction against `solve()` at random
points and agrees to **5×10⁻¹³**.

Two consequences worth having:

- **The economics become controls.** Electricity price, running hours, lifetime, interest
  rate and the disputed cascade-exchanger coefficient all enter after the thermodynamics,
  so a reader can move them.
- **The engineering units are recoverable.** Circuit pressures, both pressure ratios,
  mass flow and exchanger area were always computed and always discarded.

## Findings after the gates — `data/findings.json`

Not pre-registered; these surfaced when the envelope became recomputable.

| finding | number |
|---|---|
| Low-circuit suction below atmospheric | **33% of CO₂/propane's feasible points**, to 0.792 bar abs. The other two mixtures never. |
| Cost-minimising cascade approach, coefficient ÷10 | **6–9 K**, most often 7 K (45% of 3,630 combinations) |
| Cost-minimising cascade approach, as printed | **15 K in 100%** of them |
| Low-circuit pressure ratio across the envelope | **4.6 – 42.4**, all under one flat η_is = 0.65 |
| CoolProp 8.0.0 spurious saturated-liquid root | 6 CO₂/ethane nodes at T_CAS,C = −4 °C, h₃ = −465 kJ/kg against +243 one kelvin away |

The last of those had teeth: unfiltered it gave COP 1.73 at −80 °C — 94% of Carnot — and
those points sat at the top of the dashboard's published Pareto front, setting its axis.
Any circuit that comes out better than Carnot between its own two temperatures is now
refused, with the reason shown to the reader.

Note also that check G-5 says CO₂/propane wins on COP at a common evaporation
temperature — and CO₂/propane is the one that runs sub-atmospheric. A comparison on COP
alone would recommend the fluid with the design problem.

## Running it

```sh
uv venv && VIRTUAL_ENV=.venv uv pip install CoolProp numpy
.venv/bin/python pipeline/sweep.py      # composition scans -> data/composition_sweep.json
.venv/bin/python pipeline/validate.py   # gates             -> data/gates.json
.venv/bin/python pipeline/circuits.py   # circuit tables    -> data/circuits.json   (~100 s)
.venv/bin/python pipeline/verify.py     # closed form == solve(), to 1e-9
.venv/bin/python pipeline/findings.py   # findings          -> data/findings.json
```

`pipeline/grid.py` and `data/envelope.json` are the superseded sampled envelope. They are
kept because the interpolation claim on the first version of the page was made against
them, and deleting the evidence for a claim you have withdrawn is not a correction.

## Still not built

- The genetic algorithm producing the Pareto fronts. Both objectives compute, and the
  explorer draws the non-dominated set of the whole enumerated envelope instead, which
  for a four-variable problem on this grid is stronger than a GA anyway.
- η_is as a function of pressure ratio. This is the model's weakest assumption and the
  paper's function is unrecoverable; supplying a credible published correlation and
  republishing how the front moves would be a real contribution.
- Any comparison against a measured machine. There is no hardware validation here.
