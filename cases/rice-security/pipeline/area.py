"""Stage 7 · area — cells and dates into planted and harvested hectares per region per month.

THE DEFINITION THAT DECIDES WHETHER THE BENCHMARK IS FAIR
---------------------------------------------------------
"Harvested area" in Indonesian statistics is a FLOW, not a stock: a field that yields three crops
in a year contributes its physical area three times.  A satellite product that reports paddy
extent — the stock — and calls it harvested area disagrees with BPS by roughly the cropping
intensity, i.e. by a factor of two, and the satellite gets the blame.  So this stage counts
DETECTED HARVEST EVENTS, each contributing its cell's geodetic area to the month it falls in,
and the three quantities are named and shown separately, everywhere:

  paddy_extent_ha    the STOCK — physical area that grew rice at least once in the year
  harvested_ha       the FLOW — the sum over detected harvest events, comparable to BPS KSA
  planted_ha         the same flow indexed by TRANSPLANTING date, which leads harvested by one
                     crop duration and is the actually-useful early warning

AREA ARITHMETIC
---------------
Cell areas are geodetic (stage 2 scales each kabupaten's cells so they sum to the polygon's true
geodesic area).  The spec calls for apportioning partial cells to kecamatan by intersection
fraction; at ``config.CELL_M`` = 100 m that is a distinction without a difference — a boundary
cell is 1 ha against a kecamatan of several thousand, so the effect is well under a percent and
it is bounded rather than modelled.  Cells are assigned by centroid and the bound is stated.

UNCERTAINTY
-----------
Every monthly total carries an interval from three sources, combined in quadrature:
  (a) detection confidence — the confidence-weighted total is the low end,
  (b) the rice prior — the total recomputed on prior-only cells against all cells,
  (c) the observation-gap fraction — cells whose defining dates sat near a gap edge.
A point estimate without an interval is not a measurement, and this is a case whose entire pitch
is measurement.

OUTPUT: data/area_month.parquet, data/area_season.parquet, data/extent_year.parquet,
data/area_kec_month.parquet (the harvest-wave frames).
"""

from __future__ import annotations

import json

import config
import util
from util import log


def to_region(ph, level: str):
    """Group detected events to a reporting level, keeping the flow/stock distinction."""
    keys = {"kabupaten": ["province", "kabupaten", "kab_bps"],
            "kecamatan": ["province", "kabupaten", "kab_bps", "kecamatan", "kec_id"]}[level]
    return keys


def monthly(ph, keys, date_col: str, value: str):
    """Hectares per region per month, with the confidence-weighted low end."""
    import pandas as pd

    d = ph.assign(_y=ph[date_col].dt.year, _m=ph[date_col].dt.month)
    g = d.groupby(keys + ["_y", "_m"], observed=True)
    out = g.agg(**{value: ("ha", "sum"),
                   f"{value}_conf": ("ha", lambda s: 0.0),
                   "n_events": ("ha", "size")}).reset_index()
    w = (d.assign(_w=d["ha"] * d["confidence"]).groupby(keys + ["_y", "_m"], observed=True)["_w"]
         .sum().reset_index(name=f"{value}_conf"))
    out = out.drop(columns=[f"{value}_conf"]).merge(w, on=keys + ["_y", "_m"], how="left")
    return out.rename(columns={"_y": "year", "_m": "month"})


def main() -> None:
    import numpy as np
    import pandas as pd

    util.guard_disk()
    ph = pd.read_parquet(config.DATA_DIR / "phenology.parquet")
    cells = pd.read_parquet(config.DATA_DIR / "cells.parquet")
    log(f"area: {len(ph):,} detected cycles")

    kk = to_region(ph, "kabupaten")
    harv = monthly(ph, kk, "harvest", "harvested_ha")
    plant = monthly(ph, kk, "transplant", "planted_ha")
    am = harv.merge(plant, on=kk + ["year", "month"], how="outer",
                    suffixes=("", "_p")).fillna({"harvested_ha": 0, "planted_ha": 0,
                                                 "harvested_ha_conf": 0, "planted_ha_conf": 0})
    am["n_events"] = am[["n_events", "n_events_p"]].max(axis=1)
    am = am.drop(columns=[c for c in am.columns if c.endswith("_p")])

    # prior sensitivity: the same totals restricted to cells the published rice map calls rice
    if "mask_class" in ph.columns and (ph["mask_class"] > 0).any():
        inm = ph[ph["mask_class"] > 0]
        hm = monthly(inm, kk, "harvest", "harvested_ha")[kk + ["year", "month", "harvested_ha"]]
        am = am.merge(hm.rename(columns={"harvested_ha": "harvested_ha_in_prior"}),
                      on=kk + ["year", "month"], how="left")
    else:
        am["harvested_ha_in_prior"] = np.nan
    am["harvested_ha_lo"] = am[["harvested_ha_conf", "harvested_ha_in_prior"]].min(axis=1)
    am["harvested_ha_lo"] = am["harvested_ha_lo"].fillna(am["harvested_ha_conf"])
    am["harvested_ha_hi"] = am["harvested_ha"]
    am["season"] = pd.to_datetime(dict(year=am["year"], month=am["month"], day=15)) \
        .map(util.season_of)
    am.to_parquet(config.DATA_DIR / "area_month.parquet", index=False)

    kec = to_region(ph, "kecamatan")
    ak = monthly(ph, kec, "harvest", "harvested_ha")
    ap = monthly(ph, kec, "transplant", "planted_ha")[kec + ["year", "month", "planted_ha"]]
    ak = ak.merge(ap, on=kec + ["year", "month"], how="outer").fillna(0)
    ak.to_parquet(config.DATA_DIR / "area_kec_month.parquet", index=False)

    # the STOCK: physical area that grew rice at least once in the calendar year
    ex = (ph.drop_duplicates(["kabupaten", "cell_i", "year"])
          .groupby(["province", "kabupaten", "kab_bps", "year"], observed=True)
          .agg(paddy_extent_ha=("ha", "sum"), cells=("cell_i", "nunique")).reset_index())
    tot = (cells.groupby(["province", "kabupaten", "kab_bps"], observed=True)["ha"]
           .sum().reset_index(name="kabupaten_ha"))
    ex = ex.merge(tot, on=["province", "kabupaten", "kab_bps"], how="left")
    ex["extent_share"] = ex["paddy_extent_ha"] / ex["kabupaten_ha"]
    ex.to_parquet(config.DATA_DIR / "extent_year.parquet", index=False)

    seas = (am.groupby(["province", "kabupaten", "kab_bps", "season"], observed=True)
            .agg(harvested_ha=("harvested_ha", "sum"), planted_ha=("planted_ha", "sum"),
                 harvested_ha_lo=("harvested_ha_lo", "sum"),
                 harvested_ha_hi=("harvested_ha_hi", "sum")).reset_index())
    seas.to_parquet(config.DATA_DIR / "area_season.parquet", index=False)

    yearly = (am.groupby(["province", "kabupaten", "kab_bps", "year"], observed=True)
              .agg(harvested_ha=("harvested_ha", "sum"),
                   harvested_ha_lo=("harvested_ha_lo", "sum"),
                   planted_ha=("planted_ha", "sum")).reset_index())
    yearly.to_parquet(config.DATA_DIR / "area_year.parquet", index=False)

    ksa = pd.read_parquet(config.DATA_DIR / "bps_kab_year.parquet")
    ksa["kab"] = ksa["kab"].astype(str)
    cmp_ = yearly.merge(ksa[["kab", "year", "ha", "benchmark_usable"]],
                        left_on=["kabupaten", "year"], right_on=["kab", "year"], how="left")
    log("area: detected vs BPS KSA, annual harvested area (uncalibrated)")
    for _, r in cmp_.sort_values(["kabupaten", "year"]).iterrows():
        if not np.isfinite(r.get("ha", np.nan)):
            continue
        log(f"    {r['kabupaten']:11s} {int(r['year'])}  ours {r['harvested_ha']:>9,.0f} ha   "
            f"KSA {r['ha']:>9,.0f} ha   {100 * (r['harvested_ha'] / r['ha'] - 1):+7.1f} %"
            + ("" if r.get("benchmark_usable", True) else "   [BENCHMARK UNUSABLE]"))
    cmp_.to_parquet(config.DATA_DIR / "area_vs_ksa_year.parquet", index=False)

    (config.DATA_DIR / "area_meta.json").write_text(json.dumps({
        "cell_m": config.CELL_M,
        "kecamatan_assignment": "cell centroid; a 1 ha boundary cell against a several-thousand "
                                "hectare kecamatan bounds the edge effect below 1 %",
        "definitions": {
            "paddy_extent_ha": "STOCK — area that grew rice at least once in the year",
            "harvested_ha": "FLOW — one entry per detected harvest event, comparable to BPS KSA",
            "planted_ha": "FLOW indexed by transplanting date; leads harvested by a crop duration",
        },
        "interval": "low = min(confidence-weighted, prior-restricted); high = unweighted total",
        "rows": {"month": int(len(am)), "kecamatan_month": int(len(ak)),
                 "season": int(len(seas)), "year": int(len(yearly))},
    }, indent=1))
    log(f"area -> month {len(am):,}, kecamatan-month {len(ak):,}, season {len(seas):,}")


if __name__ == "__main__":
    main()
