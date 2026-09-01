"""Cascade refrigeration cycle: thermodynamics, exergy and annual cost.

Replication target
------------------
Nasruddin, Arnas, Faqih & Giannetti (2016), "Thermoeconomic Optimization of
Cascade Refrigeration System Using Mixed Carbon Dioxide and Hydrocarbons at Low
Temperature Circuit", Makara J. Technol. 20(3) 132-138, doi:10.7454/mst.v20i3.3068
(CC BY-NC-ND 4.0).

Nothing is copied from that paper. The cycle is rebuilt from the equations and
assumptions it states, and the numbers it reports are used only to score this
implementation against it. Two things the paper does not state had to be
reconstructed, and both are declared here rather than hidden:

RECONSTRUCTION 1 - exergetic efficiency.
  The paper maximises "exergy efficiency" but never defines it. The standard
  second-law efficiency for a refrigerator (its own ref [14], Bejan/Tsatsaronis)
  is the exergy of the cooling delivered over the work paid:

      eta_ex = Q_E * (T0/T_E - 1) / W_total  ==  COP / COP_carnot

  This is not a guess: at T_E = -80 C and T0 = 25 C, COP_carnot = 1.839, so the
  paper's reported COP of 0.65 gives 35.4% - and its ethylene Pareto front tops
  out at ~35%. Read backwards, its 22-35% axis implies COP 0.40-0.64, which is
  exactly the range of its own Figures 3-5. The definition reproduces the axis.

RECONSTRUCTION 2 - compressor isentropic efficiency.
  The paper says compression is "expressed as a function of pressure ratio" and
  never gives the function; the author no longer has it. So eta_is is a free
  parameter here, and `invert_eta_is` reports what value the paper's own
  reported optimum implies. That inversion is a finding, not an assumption.

Everything else - the four decision variables, the ranges, the constraints, the
cost correlations and coefficients, the U values, the economic assumptions - is
stated in the paper and used as stated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import CoolProp.CoolProp as CP

# ── constants the paper states ──────────────────────────────────────────────
T0_C = 25.0                 # dead state / ambient, C
P0 = 101_325.0              # dead state pressure, Pa
Q_EVAP_W = 500.0            # cooling capacity, W  ("0.5 kW")
T_DROP_K = 5.0              # cold room minus evaporation temperature, K
ETA_ELMECH = 0.93           # combined electrical + mechanical efficiency

# heat transfer coefficients, W/m2.K. The paper's sentence lists three values
# against three components with broken punctuation; this is the only physically
# coherent mapping (an air-cooled condenser is the low one, a refrigerant-to-
# refrigerant cascade exchanger the high one). Flagged in the README as a
# question for the author rather than silently assumed.
U_EVAP = 18.03
U_COND = 6.85
U_CASCADE = 64.87

# economic assumptions the paper states
LIFETIME_YEARS = 10
HOURS_PER_YEAR = 7000
COST_ELEC_PER_KWH = 0.12
INTEREST_RATE = 0.08
FAN_KW = 0.050              # condenser and evaporator fans, 50 W each

# RECONSTRUCTION 3 - the cascade exchanger cost coefficient.
# The paper prints eq. 8 as C = 23829 * A^0.68. Implemented as printed, together
# with every other correlation exactly as given, the three reported optima cost
# $19,873-21,924/yr against the paper's own reported Pareto range of about
# $5,100-9,100/yr - a factor of 2.4. Dividing this one coefficient by ten puts
# all three inside or within 0.6% of their published bands. That is consistent
# with a lost decimal point in the printed equation, but it is OUR inference and
# it is a gate, not a fix: CASCADE_COST_COEFF is switchable so the case can
# publish both numbers.
CASCADE_COST_COEFF_AS_PRINTED = 23829.0
CASCADE_COST_COEFF = 2382.9

# the three low-temperature-circuit mixtures the paper tests, as CO2 mass
# fraction / hydrocarbon mass fraction, with the optimum it reports
MIXTURES = {
    "propane": {"partner": "Propane", "reported_co2_mass_frac": 0.94},
    "ethane": {"partner": "Ethane", "reported_co2_mass_frac": 0.64},
    "ethylene": {"partner": "Ethylene", "reported_co2_mass_frac": 0.37},
}

HTC_FLUID = "Propane"       # high temperature circuit, per the paper


def crf(i: float = INTEREST_RATE, n: int = LIFETIME_YEARS) -> float:
    """Capital recovery factor, the paper's eq. 11."""
    return i * (1 + i) ** n / ((1 + i) ** n - 1)


def mass_to_mole_fractions(co2_mass: float, partner: str) -> tuple[float, float]:
    """CoolProp wants mole fractions; the paper specifies mass fractions."""
    m_co2 = CP.PropsSI("molar_mass", "", 0, "", 0, "CO2")
    m_p = CP.PropsSI("molar_mass", "", 0, "", 0, partner)
    n_co2 = co2_mass / m_co2
    n_p = (1.0 - co2_mass) / m_p
    total = n_co2 + n_p
    return n_co2 / total, n_p / total


def _mix_name(partner: str, co2_mole: float) -> str:
    return f"HEOS::CO2[{co2_mole:.6f}]&{partner}[{1 - co2_mole:.6f}]"


@dataclass
class Circuit:
    """One vapour-compression circuit: saturated cycle, isenthalpic expansion."""

    fluid: str
    t_evap_c: float
    t_cond_c: float
    eta_is: float
    q_evap_w: float
    # CO2/hydrocarbon mixtures glide by tens of kelvin, so "the evaporation
    # temperature" is ambiguous for them and the choice changes the answer.
    # "dew"    - vapour leaves the evaporator at t_evap (conservative)
    # "bubble" - liquid enters the evaporator at t_evap
    # "mean"   - t_evap is the mid-glide temperature
    glide: str = "dew"

    p_evap: float = field(init=False)
    p_cond: float = field(init=False)
    h1: float = field(init=False)        # evaporator outlet, saturated vapour
    h2: float = field(init=False)        # compressor outlet, real
    h3: float = field(init=False)        # condenser outlet, saturated liquid
    m_dot: float = field(init=False)     # kg/s
    w_comp_w: float = field(init=False)  # electrical power, W
    q_cond_w: float = field(init=False)

    def __post_init__(self) -> None:
        te, tc = self.t_evap_c + 273.15, self.t_cond_c + 273.15
        # A zeotropic mixture glides. Evaporation is pinned at its DEW point and
        # condensation at its BUBBLE point, which is the conservative reading of
        # "the evaporation temperature" for a mixture: the vapour really does
        # leave at T_evap, and the liquid really does leave at T_cond.
        if self.glide == "dew":
            self.p_evap = CP.PropsSI("P", "T", te, "Q", 1, self.fluid)
            self.p_cond = CP.PropsSI("P", "T", tc, "Q", 0, self.fluid)
        elif self.glide == "bubble":
            self.p_evap = CP.PropsSI("P", "T", te, "Q", 0, self.fluid)
            self.p_cond = CP.PropsSI("P", "T", tc, "Q", 1, self.fluid)
        elif self.glide == "mean":
            self.p_evap = 0.5 * (CP.PropsSI("P", "T", te, "Q", 0, self.fluid)
                                 + CP.PropsSI("P", "T", te, "Q", 1, self.fluid))
            self.p_cond = 0.5 * (CP.PropsSI("P", "T", tc, "Q", 0, self.fluid)
                                 + CP.PropsSI("P", "T", tc, "Q", 1, self.fluid))
        else:
            raise ValueError(f"unknown glide convention {self.glide!r}")

        self.h1 = CP.PropsSI("H", "P", self.p_evap, "Q", 1, self.fluid)
        s1 = CP.PropsSI("S", "P", self.p_evap, "Q", 1, self.fluid)
        h2s = CP.PropsSI("H", "P", self.p_cond, "S", s1, self.fluid)
        self.h2 = self.h1 + (h2s - self.h1) / self.eta_is
        self.h3 = CP.PropsSI("H", "P", self.p_cond, "Q", 0, self.fluid)

        # h4 == h3 (isenthalpic expansion), so evaporator duty is h1 - h3
        self.m_dot = self.q_evap_w / (self.h1 - self.h3)
        self.w_comp_w = self.m_dot * (self.h2 - self.h1) / ETA_ELMECH
        self.q_cond_w = self.m_dot * (self.h2 - self.h3)

    @property
    def pressure_ratio(self) -> float:
        return self.p_cond / self.p_evap


@dataclass
class CascadeResult:
    cop: float
    eta_ex: float
    annual_cost_usd: float
    w_total_kw: float
    capital_usd: float
    ltc: Circuit
    htc: Circuit


def solve(
    *,
    mixture: str,
    co2_mass_frac: float,
    t_evap_c: float,
    t_cas_c_c: float,
    t_cond_c: float,
    dt_cascade_k: float,
    eta_is: float,
    cascade_coeff: float = CASCADE_COST_COEFF,
    glide: str = "dew",
) -> CascadeResult:
    """One operating point. Returns COP, exergetic efficiency and annual cost.

    Decision variables are exactly the paper's four: t_evap_c, t_cas_c_c,
    t_cond_c, dt_cascade_k. Heat flows LTC -> HTC, so the high circuit must
    evaporate colder than the low circuit condenses: T_cas,E = T_cas,C - DT.
    """
    spec = MIXTURES[mixture]
    co2_mole, _ = mass_to_mole_fractions(co2_mass_frac, spec["partner"])
    ltc_fluid = _mix_name(spec["partner"], co2_mole)

    ltc = Circuit(
        fluid=ltc_fluid,
        t_evap_c=t_evap_c,
        t_cond_c=t_cas_c_c,
        eta_is=eta_is,
        q_evap_w=Q_EVAP_W,
        glide=glide,
    )
    htc = Circuit(
        fluid=HTC_FLUID,
        t_evap_c=t_cas_c_c - dt_cascade_k,
        t_cond_c=t_cond_c,
        eta_is=eta_is,
        q_evap_w=ltc.q_cond_w,
    )

    w_total = ltc.w_comp_w + htc.w_comp_w
    cop = Q_EVAP_W / w_total

    # RECONSTRUCTION 1, documented at the top of this file
    te_k = t_evap_c + 273.15
    t0_k = T0_C + 273.15
    cop_carnot = te_k / (t0_k - te_k)
    eta_ex = cop / cop_carnot

    # ── cost, the paper's eqs. 4-10 ────────────────────────────────────────
    w_h_kw = htc.w_comp_w / 1000.0
    w_l_kw = ltc.w_comp_w / 1000.0

    c_comp_h = 9624.2 * w_h_kw**0.46
    c_comp_l = 10167.5 * w_l_kw**0.46

    # A0 = Q / (U0 * dT), eq. 9
    a_cond = htc.q_cond_w / (U_COND * (t_cond_c - T0_C))
    a_evap = Q_EVAP_W / (U_EVAP * T_DROP_K)
    a_cascade = ltc.q_cond_w / (U_CASCADE * dt_cascade_k)

    c_cond = 1397 * a_cond**0.89 + 629.05 * FAN_KW**0.76
    c_evap = 1397 * a_evap**0.89 + 629.05 * FAN_KW**0.76
    c_cascade = cascade_coeff * a_cascade**0.68

    capital = c_comp_h + c_comp_l + c_cond + c_evap + c_cascade
    running_kw = w_h_kw + w_l_kw + 2 * FAN_KW
    annual = capital * crf() + COST_ELEC_PER_KWH * HOURS_PER_YEAR * running_kw

    return CascadeResult(
        cop=cop,
        eta_ex=eta_ex,
        annual_cost_usd=annual,
        w_total_kw=w_total / 1000.0,
        capital_usd=capital,
        ltc=ltc,
        htc=htc,
    )


def invert_eta_is(
    *,
    mixture: str,
    co2_mass_frac: float,
    t_evap_c: float,
    t_cas_c_c: float,
    t_cond_c: float,
    dt_cascade_k: float,
    target_cop: float,
    lo: float = 0.20,
    hi: float = 0.99,
) -> float | None:
    """What isentropic efficiency would reproduce a reported COP at this point?

    RECONSTRUCTION 2. The paper's compressor correlation is unrecoverable, so
    rather than assume one, this asks the paper's own reported COP what eta_is it
    implies. Bisection: COP rises monotonically with eta_is.
    """
    def f(e: float) -> float:
        return solve(
            mixture=mixture, co2_mass_frac=co2_mass_frac, t_evap_c=t_evap_c,
            t_cas_c_c=t_cas_c_c, t_cond_c=t_cond_c, dt_cascade_k=dt_cascade_k,
            eta_is=e,
        ).cop - target_cop

    try:
        f_lo, f_hi = f(lo), f(hi)
    except Exception:
        return None
    if f_lo * f_hi > 0:
        return None                     # target COP not reachable in [lo, hi]

    for _ in range(60):
        mid = 0.5 * (lo + hi)
        try:
            fm = f(mid)
        except Exception:
            return None
        if abs(fm) < 1e-6:
            return mid
        if f_lo * fm <= 0:
            hi = mid
        else:
            lo, f_lo = mid, fm
    return 0.5 * (lo + hi)
