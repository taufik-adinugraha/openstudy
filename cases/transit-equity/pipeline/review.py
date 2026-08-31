"""Review stage · the tests the build did not run, written for the adversarial review.

Nine pre-specified tests, all on the case's own published outputs. Nothing here tunes a
threshold; every result is written whether it flatters the case or not.

  A  threshold sweep      the whole distributional package at 30 / 45 / 60 min and under
                          exponential decay — does the story survive the cutoff choice?
  B  naive baseline       how much of the routed measure is reproduced by a circle drawn on
                          a map, with no network at all?
  C  opportunity swap     the same distribution measured on population and hospitals rather
                          than on the floorspace proxy
  D  MAUP ladder          Gini and Palma recomputed at kelurahan / kecamatan / kabupaten
  E  edge effects         access against distance from the study-area boundary
  F  rail chord deficit   why all three hand-encoded rail lines are scheduled fast
  G  poverty link         Case F's kecamatan poverty estimates against access — the equity
                          axis the build reported as pending
  H  export precision     how many kelurahan are published as exactly zero but are not
  I  rail incidence       who captures the access the rail layer adds
  J  surface vs volume    the same hour re-measured on GHS-BUILT-V (building volume) rather
                          than GHS-BUILT-S (building footprint), reusing the routed matrix

Output: web/src/data/review.json (imported by web/src/pages/article.astro).
"""

from __future__ import annotations

import json
import math
import zipfile

import numpy as np
import pandas as pd

import config
import equity as eq
import util
from util import log

OUT = config.CASE_DIR / "web" / "src" / "data" / "review.json"
MOLLWEIDE = "ESRI:54009"
CUTS = (30, 45, 60)


# ── shared helpers ─────────────────────────────────────────────────────────────────────────
def dist_pack(vals, wts) -> dict:
    """The published distributional package, computed by the case's own equity functions."""
    curve, gini, palma = eq.lorenz(np.asarray(vals, float), np.asarray(wts, float))
    v, w = np.asarray(vals, float), np.asarray(wts, float)
    return {"gini": None if gini is None else round(gini, 4),
            "palma": None if palma is None or not np.isfinite(palma) else round(palma, 3),
            "median": round(float(eq.wmedian(v, w)), 6),
            "mean": round(float(np.average(v, weights=w)), 6),
            "zero_units": int((v <= 0).sum()),
            "zero_pop_share": round(float(w[v <= 0].sum() / w.sum()), 4),
            "lorenz": curve}


def spearman(a, b) -> float:
    ra = pd.Series(a).rank().values
    rb = pd.Series(b).rank().values
    return float(np.corrcoef(ra, rb)[0, 1])


def ols_r2(X, y) -> tuple[float, np.ndarray, np.ndarray]:
    """OLS with intercept; returns (R2, coefficients, residuals)."""
    X = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    fit = X @ beta
    ss_res = float(((y - fit) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return 1.0 - ss_res / ss_tot, beta, y - fit


def haversine_km(lat1, lon1, lat2, lon2):
    r = np.pi / 180
    dlat = (lat2 - lat1) * r
    dlon = (lon2 - lon1) * r
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1 * r) * np.cos(lat2 * r) * np.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


# ── A · threshold sweep ────────────────────────────────────────────────────────────────────
def test_a(acc: pd.DataFrame) -> dict:
    rows = []
    ranks = {}
    for c in CUTS:
        a = acc[(acc.scenario == "all") & (acc.cutoff == c)]
        dki = a[a.dki], a[~a.dki]
        p = dist_pack(a.jobs_share.values, a["pop"].values)
        p["cutoff"] = c
        p["dki_median"] = round(float(eq.wmedian(dki[0].jobs_share.values, dki[0]["pop"].values)), 6)
        p["bodetabek_median"] = round(float(eq.wmedian(dki[1].jobs_share.values, dki[1]["pop"].values)), 6)
        p["ratio"] = (round(p["dki_median"] / p["bodetabek_median"], 1)
                      if p["bodetabek_median"] > 0 else None)
        rows.append(p)
        ranks[c] = a.set_index("id").jobs_share
    g = acc[(acc.scenario == "all") & (acc.cutoff == 60)]
    grav = dist_pack(g.gravity_share.values, g["pop"].values)
    grav["cutoff"] = "decay"
    grav["dki_median"] = round(float(eq.wmedian(g[g.dki].gravity_share.values, g[g.dki]["pop"].values)), 6)
    grav["bodetabek_median"] = round(float(eq.wmedian(g[~g.dki].gravity_share.values,
                                                      g[~g.dki]["pop"].values)), 6)
    grav["ratio"] = (round(grav["dki_median"] / grav["bodetabek_median"], 1)
                     if grav["bodetabek_median"] > 0 else None)
    j = ranks[30].to_frame("c30").join(ranks[45].to_frame("c45")).join(ranks[60].to_frame("c60"))
    j["grav"] = g.set_index("id").gravity_share
    return {"rows": rows, "gravity": grav,
            "rho_30_60": round(spearman(j.c30, j.c60), 4),
            "rho_45_60": round(spearman(j.c45, j.c60), 4),
            "rho_60_gravity": round(spearman(j.c60, j.grav), 4),
            "gini_range": [min(r["gini"] for r in rows), max(r["gini"] for r in rows)],
            "palma_range": [min(r["palma"] for r in rows), max(r["palma"] for r in rows)]}


# ── B · the naive baseline: a circle, not a network ────────────────────────────────────────
def test_b(acc: pd.DataFrame, dests: pd.DataFrame) -> dict:
    a = acc[(acc.scenario == "all") & (acc.cutoff == 60)].reset_index(drop=True)
    dlat = dests["lat"].values[None, :]
    dlon = dests["lon"].values[None, :]
    olat = a["lat"].values[:, None]
    olon = a["lon"].values[:, None]
    dist = haversine_km(olat, olon, dlat, dlon)          # 1,511 × 5,766 km
    nres = dests["nres_m2"].values[None, :]
    tot = float(dests["nres_m2"].sum())
    circles = {r: (nres * (dist <= r)).sum(axis=1) / tot for r in (2, 5, 10, 15)}
    # jobs-proxy-weighted centre of mass of the region — no landmark is hand-picked
    cw = dests["nres_m2"].values
    clat = float(np.average(dests["lat"].values, weights=cw))
    clon = float(np.average(dests["lon"].values, weights=cw))
    d_com = haversine_km(a["lat"].values, a["lon"].values, clat, clon)
    d_monas = haversine_km(a["lat"].values, a["lon"].values, -6.1754, 106.8272)

    a = a.assign(d_com=d_com, d_monas=d_monas, circle5=circles[5], circle10=circles[10])
    # Fit on the units that actually reach something: log of an exact zero is not a number,
    # and a floor turns the zero mass into an artefact of whatever floor you picked.
    pos = a[a.jobs_share > 0].copy()
    y = np.log(pos.jobs_share.values)
    models = {
        "distance to the centre of mass": ols_r2(np.log(pos.d_com + 0.5), y)[0],
        "distance to Monas": ols_r2(np.log(pos.d_monas + 0.5), y)[0],
        "floorspace within 5 km": ols_r2(np.log(pos.circle5 + 1e-9), y)[0],
        "floorspace within 10 km": ols_r2(np.log(pos.circle10 + 1e-9), y)[0],
        "distance + floorspace within 10 km": ols_r2(
            np.column_stack([np.log(pos.d_com + 0.5), np.log(pos.circle10 + 1e-9)]), y)[0],
    }
    r2_full, _, resid = ols_r2(
        np.column_stack([np.log(pos.d_com + 0.5), np.log(pos.circle10 + 1e-9)]), y)
    pos["resid"] = resid
    keep = ["adm4_name", "adm3_name", "adm2_name", "jobs_share", "circle10", "d_com", "resid", "pop"]
    # Network failures: units the circle says are well placed, that the network leaves at zero.
    med_circle = float(np.median(a.circle10))
    fail = a[(a.jobs_share <= 0) & (a.circle10 > med_circle)]
    return {"centre_of_mass": [round(clat, 4), round(clon, 4)],
            "n_positive": int(len(pos)),
            "models": {k: round(v, 4) for k, v in models.items()},
            "r2_naive": round(models["distance + floorspace within 10 km"], 4),
            "rho_circle_routed": round(spearman(a.circle10, a.jobs_share.values), 4),
            "median_circle10": round(med_circle, 6),
            "network_failures": int(len(fail)),
            "network_failure_pop": round(float(fail["pop"].sum()), 0),
            "network_failure_list": json.loads(
                fail.nlargest(8, "circle10")[[c for c in keep if c != "resid"]]
                .round(6).to_json(orient="records")),
            "over": json.loads(pos.nlargest(8, "resid")[keep].round(6).to_json(orient="records")),
            "under": json.loads(pos.nsmallest(8, "resid")[keep].round(6).to_json(orient="records")),
            "scatter": [{"x": round(float(x), 5), "y": round(float(yy), 9),
                         "n": str(n), "r": str(rr), "dki": bool(k)}
                        for x, yy, n, rr, k in zip(a.circle10, a.jobs_share.values, a.adm4_name,
                                                   a.adm2_name, a.dki)],
            "denominator": denominator_geography(dests, clat, clon)}


def denominator_geography(dests: pd.DataFrame, clat: float, clon: float) -> dict:
    """Where the thing being counted actually is. The measure's denominator is the whole
    region's floorspace, and most of the region's floorspace is not in the city."""
    d = haversine_km(dests["lat"].values, dests["lon"].values, clat, clon)
    tj, tp = float(dests["nres_m2"].sum()), float(dests["pop"].sum())
    rings = []
    for lo_, hi_ in ((0, 5), (5, 10), (10, 20), (20, 30), (30, 50), (50, 999)):
        s = (d >= lo_) & (d < hi_)
        rings.append({"lo": lo_, "hi": hi_,
                      "jobs": round(float(dests.loc[s, "nres_m2"].sum() / tj), 4),
                      "pop": round(float(dests.loc[s, "pop"].sum() / tp), 4),
                      "cells": int(s.sum())})
    return {"rings": rings, "total_nres_km2": round(tj / 1e6, 1),
            "beyond_20km_jobs_share": round(float(dests.loc[d >= 20, "nres_m2"].sum() / tj), 4)}


# ── C · opportunity swap ───────────────────────────────────────────────────────────────────
def test_c(acc: pd.DataFrame) -> dict:
    out = {}
    for name, col in (("job-dense floorspace", "jobs_share"), ("population", "pop_share")):
        rows = []
        for c in CUTS:
            a = acc[(acc.scenario == "all") & (acc.cutoff == c)]
            p = dist_pack(a[col].values, a["pop"].values)
            p["cutoff"] = c
            p.pop("lorenz")
            rows.append(p)
        out[name] = rows
    a = acc[(acc.scenario == "all") & (acc.cutoff == 60)]
    out["rho_jobs_pop"] = round(spearman(a.jobs_share.values, a.pop_share.values), 4)
    out["hospital_rows"] = [
        {"cutoff": c,
         "pop_share_no_hospital": round(float(
             acc[(acc.scenario == "all") & (acc.cutoff == c)]
             .pipe(lambda d: d.loc[d.hospitals == 0, "pop"].sum() / d["pop"].sum())), 4)}
        for c in CUTS]
    return out


# ── D · MAUP ladder ────────────────────────────────────────────────────────────────────────
def test_d(acc: pd.DataFrame, adm4) -> dict:
    a = acc[(acc.scenario == "all") & (acc.cutoff == 60)].copy()
    area = adm4.to_crs(MOLLWEIDE).area / 1e6
    ar = pd.DataFrame({"id": adm4["adm4_pcode"].values, "area_km2": area.values})
    a = a.merge(ar, on="id", how="left")
    a["adm3"] = a["id"].str[:-3]      # ID + 7 digits
    a["adm2"] = a["id"].str[:-6]      # ID + 4 digits
    rungs = []
    for label, key in (("kelurahan / desa", "id"), ("kecamatan", "adm3"), ("kabupaten / kota", "adm2")):
        g = a.groupby(key).apply(
            lambda d: pd.Series({"v": np.average(d.jobs_share, weights=d["pop"]) if d["pop"].sum() > 0
                                 else d.jobs_share.mean(), "pop": d["pop"].sum()}),
            include_groups=False)
        p = dist_pack(g.v.values, g["pop"].values)
        p.pop("lorenz")
        p["units"] = int(len(g))
        p["level"] = label
        rungs.append(p)
    ok = a.area_km2.notna() & (a.area_km2 > 0)
    return {"rungs": rungs,
            "study_area_km2": round(float(a.area_km2.sum()), 0),
            "dki_area_km2": round(float(a.loc[a.dki, "area_km2"].sum()), 0),
            "area_km2": {"min": round(float(a.area_km2.min()), 3),
                         "p50": round(float(a.area_km2.median()), 2),
                         "max": round(float(a.area_km2.max()), 1),
                         "ratio": round(float(a.area_km2.max() / a.area_km2.min()), 0)},
            "rho_area_access": round(spearman(a.loc[ok, "area_km2"], a.loc[ok, "jobs_share"]), 4),
            "pop_km2": {"p50": round(float((a["pop"] / a.area_km2).median()), 0)}}


# ── E · edge effects ───────────────────────────────────────────────────────────────────────
def test_e(acc: pd.DataFrame) -> dict:
    a = acc[(acc.scenario == "all") & (acc.cutoff == 60)].copy()
    w, s, e, n = config.BBOX
    la, lo = a["lat"].values, a["lon"].values
    d = np.minimum.reduce([haversine_km(la, lo, la, np.full_like(lo, w)),
                           haversine_km(la, lo, la, np.full_like(lo, e)),
                           haversine_km(la, lo, np.full_like(la, s), lo),
                           haversine_km(la, lo, np.full_like(la, n), lo)])
    a["edge_km"] = d
    bands = []
    for lo_, hi_ in ((0, 5), (5, 10), (10, 20), (20, 40), (40, 999)):
        sel = a[(a.edge_km >= lo_) & (a.edge_km < hi_)]
        if not len(sel):
            continue
        bands.append({"lo": lo_, "hi": hi_, "n": int(len(sel)),
                      "median": round(float(sel.jobs_share.median()), 6),
                      "zero_share": round(float((sel.jobs_share <= 0).mean()), 4),
                      "pop": round(float(sel["pop"].sum()), 0)})
    near = a[a.edge_km < 10]
    return {"bands": bands, "within_10km_units": int(len(near)),
            "within_10km_pop": round(float(near["pop"].sum()), 0),
            "within_10km_pop_share": round(float(near["pop"].sum() / a["pop"].sum()), 4),
            "bbox": list(config.BBOX)}


# ── F · rail chord deficit ─────────────────────────────────────────────────────────────────
PUBLISHED_KM = {"mrt_north_south": 15.7, "krl_bogor": 54.8, "lrt_jabodebek": 24.0}


def test_f(stats: dict) -> dict:
    with zipfile.ZipFile(config.RAIL_GTFS) as zf:
        stops = pd.read_csv(zf.open("stops.txt")).set_index("stop_id")
        st = pd.read_csv(zf.open("stop_times.txt"))
        trips = pd.read_csv(zf.open("trips.txt"))
    rows = []
    by_route = {o.get("route", "").replace("rail_", ""): o for o in stats["G-G1"]["ods"]}
    for key, pub_km in PUBLISHED_KM.items():
        if key not in by_route:
            continue
        t = trips[(trips.route_id == f"rail_{key}") & (trips.direction_id == 0)]
        if not len(t):
            continue
        seq = st[st.trip_id == t.trip_id.iloc[0]].sort_values("stop_sequence")
        ll = [(float(stops.loc[s, "stop_lat"]), float(stops.loc[s, "stop_lon"]))
              for s in seq.stop_id if s in stops.index]
        chord = float(sum(haversine_km(ll[i][0], ll[i][1], ll[i + 1][0], ll[i + 1][1])
                          for i in range(len(ll) - 1)))
        od = by_route[key]
        rows.append({"line": key, "published_km": pub_km, "chord_km": round(chord, 2),
                     "deficit": round(1 - chord / pub_km, 4),
                     "published_min": od["published_min"],
                     "scheduled_min": od["scheduled_in_vehicle_min"],
                     "observed_optimism": round(1 - od["scheduled_in_vehicle_min"] / od["published_min"], 4),
                     "stations": len(ll),
                     "router_min": od.get("router_door_to_door_min")})
    return {"rows": rows,
            "mean_optimism": round(float(np.mean([r["observed_optimism"] for r in rows])), 4),
            "mean_deficit": round(float(np.mean([r["deficit"] for r in rows])), 4)}


# ── G · the equity axis the build reported as pending ──────────────────────────────────────
def test_g(acc: pd.DataFrame) -> dict:
    p = config.CASE_F_ESTIMATES
    if not p.exists():
        return {"available": False, "reason": f"Case F estimates absent at {p}"}
    f = pd.read_parquet(p)
    year = int(f.year.max())
    f = f[f.year == year][["pcode", "p0_est", "pop"]].rename(
        columns={"pcode": "adm3", "p0_est": "poverty", "pop": "f_pop"})
    a = acc[(acc.scenario == "all") & (acc.cutoff == 60)].copy()
    a["adm3"] = a["id"].str[:-3]
    j = a.merge(f, on="adm3", how="inner")
    if len(j) < 100:
        return {"available": False, "reason": f"only {len(j)} kelurahan matched Case F",
                "matched": int(len(j))}
    rho = spearman(j.jobs_share.values, j.poverty.values)
    # population-weighted mean access by poverty quintile of the kelurahan's kecamatan
    j["q"] = pd.qcut(j.poverty, 5, labels=False, duplicates="drop")
    qs = []
    for q, g in j.groupby("q"):
        qs.append({"q": int(q) + 1,
                   "poverty_lo": round(float(g.poverty.min()), 2),
                   "poverty_hi": round(float(g.poverty.max()), 2),
                   "mean_access": round(float(np.average(g.jobs_share, weights=g["pop"])), 6),
                   "median_access": round(float(eq.wmedian(g.jobs_share.values, g["pop"].values)), 6),
                   "zero_share": round(float((g.jobs_share <= 0).mean()), 4),
                   "pop": round(float(g["pop"].sum()), 0),
                   "units": int(len(g))})
    # concentration index of access with respect to the poverty ranking
    o = j.sort_values("poverty", ascending=False)             # poorest first
    w = o["pop"].values
    r = (np.cumsum(w) - 0.5 * w) / w.sum()
    mu = np.average(o.jobs_share.values, weights=w)
    ci = float(2 * np.average((o.jobs_share.values - mu) * (r - 0.5), weights=w) / mu)
    lo_a = j.jobs_share.quantile(0.2)
    hi_p = j.poverty.quantile(0.8)
    dd = j[(j.jobs_share <= lo_a) & (j.poverty >= hi_p)]
    return {"available": True, "matched": int(len(j)),
            "vintage_year": year,
            "kecamatan_matched": int(j.adm3.nunique()),
            "distinct_poverty_values": int(j.poverty.nunique()),
            "poverty_p10": round(float(j.poverty.quantile(0.1)), 2),
            "poverty_p90": round(float(j.poverty.quantile(0.9)), 2),
            "spearman_rho": round(rho, 4),
            "concentration_index": round(ci, 4),
            "quintiles": qs,
            "ratio_q1_q5": (round(qs[0]["mean_access"] / qs[-1]["mean_access"], 1)
                            if qs[-1]["mean_access"] > 0 else None),
            "double_disadvantage_units": int(len(dd)),
            "double_disadvantage_pop": round(float(dd["pop"].sum()), 0),
            "double_disadvantage_pop_share": round(float(dd["pop"].sum() / j["pop"].sum()), 4),
            "top": json.loads(dd.nlargest(10, "pop")[
                ["adm4_name", "adm2_name", "jobs_share", "poverty", "pop"]]
                .round(6).to_json(orient="records")),
            "scatter": [{"x": round(float(pv), 2), "y": round(float(js), 6), "p": round(float(pp), 0)}
                        for pv, js, pp in zip(j.poverty, j.jobs_share, j["pop"])]}


# ── H · export precision ───────────────────────────────────────────────────────────────────
def test_h(acc: pd.DataFrame) -> dict:
    a = acc[(acc.scenario == "all") & (acc.cutoff == 60)]
    true_zero = int((a.jobs_share <= 0).sum())
    published_zero = int((a.jobs_share.round(4) <= 0).sum())
    lost = a[(a.jobs_share > 0) & (a.jobs_share.round(4) <= 0)]
    return {"true_zero": true_zero, "published_zero_at_4dp": published_zero,
            "quantised_to_zero": int(len(lost)),
            "quantised_pop": round(float(lost["pop"].sum()), 0),
            "quantised_pop_share": round(float(lost["pop"].sum() / a["pop"].sum()), 4),
            "median_value": round(float(eq.wmedian(a.jobs_share.values, a["pop"].values)), 6),
            "resolution_vs_median": round(1e-4 / float(eq.wmedian(a.jobs_share.values, a["pop"].values)), 3)}


# ── I · who captures what the rail layer adds ──────────────────────────────────────────────
def test_i(acc: pd.DataFrame) -> dict:
    piv = acc[acc.cutoff == 60].pivot_table(index="id", columns="scenario", values="jobs_share")
    meta = acc[(acc.scenario == "all") & (acc.cutoff == 60)].set_index("id")[["pop", "dki"]]
    j = piv.join(meta).dropna(subset=["pop"])
    j["rail"] = j["all"] - j["no_rail"]
    j["transit"] = j["all"] - j["walk"]
    tot_pop = j["pop"].sum()
    out = {}
    for layer in ("rail", "transit"):
        gain = (j[layer] * j["pop"])
        tot = float(gain.sum())
        srt = j.assign(g=gain).sort_values(layer)
        cum = srt["pop"].cumsum() / tot_pop
        top10 = float(srt.loc[cum > 0.9, "g"].sum() / tot)
        out[layer] = {
            "mean_delta": round(float(tot / tot_pop), 6),
            "dki_share_of_gain": round(float(gain[j.dki].sum() / tot), 4),
            "dki_share_of_pop": round(float(j.loc[j.dki, "pop"].sum() / tot_pop), 4),
            "top_decile_share_of_gain": round(top10, 4),
            "dki_mean_delta": round(float(np.average(j.loc[j.dki, layer], weights=j.loc[j.dki, "pop"])), 6),
            "bod_mean_delta": round(float(np.average(j.loc[~j.dki, layer], weights=j.loc[~j.dki, "pop"])), 6),
        }
    scen = {}
    for s in ("all", "no_rail", "walk"):
        if s not in piv.columns:
            continue
        p = dist_pack(j[s].values, j["pop"].values)
        p["scenario"] = s
        scen[s] = p
    out["scenarios"] = scen
    out["gini_added_by_rail"] = round(scen["all"]["gini"] - scen["no_rail"]["gini"], 4)
    out["gini_added_by_transit"] = round(scen["all"]["gini"] - scen["walk"]["gini"], 4)
    # decile incidence, poorest-access decile first, of the whole transit system
    srt = j.sort_values("walk")
    cum = srt["pop"].cumsum() / tot_pop
    dec = []
    for d in range(10):
        sel = srt[(cum > d / 10) & (cum <= (d + 1) / 10)]
        if not len(sel):
            continue
        dec.append({"d": d + 1,
                    "walk": round(float(np.average(sel["walk"], weights=sel["pop"])), 6),
                    "no_rail": round(float(np.average(sel["no_rail"], weights=sel["pop"])), 6),
                    "all": round(float(np.average(sel["all"], weights=sel["pop"])), 6)})
    out["deciles"] = dec
    return out


# ── J · the opportunity a floor plan misses: surface vs volume ─────────────────────────────
GHS_V_TILE = ("GHS_BUILT_V_GLOBE_R2023A/GHS_BUILT_V_NRES_E2020_GLOBE_R2023A_54009_100/"
              "V1-0/tiles/GHS_BUILT_V_NRES_E2020_GLOBE_R2023A_54009_100_V1_0_R10_C29.zip")


def volume_tile():
    """GHS-BUILT-V NRES: the same product family as the case's jobs proxy, but building
    volume (m³) rather than footprint (m²). One extra tile, same tiling scheme, CC BY 4.0."""
    import ingest
    tif = ingest.RAW / "ghsl" / "ghs_nres_vol_e2020_r10_c29.tif"
    if tif.exists():
        return tif
    z = tif.with_suffix(".zip")
    util.fetch(config.GHSL_FTP + GHS_V_TILE, z, key=tif.stem, min_bytes=500_000)
    with zipfile.ZipFile(z) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith(".tif"))
        tif.write_bytes(zf.read(member))
    z.unlink()
    util.manifest_put(tif.stem, licence="CC BY 4.0", source="JRC GHSL R2023A (BUILT-V NRES)",
                      bytes=tif.stat().st_size)
    return tif


def test_j(acc: pd.DataFrame, dests: pd.DataFrame) -> dict:
    """Re-measure the identical hour with volume-weighted opportunity.

    Nothing is re-routed: the travel-time matrix is the one the case published. Only the
    weight attached to each destination cell changes, from built surface to built volume.
    """
    import geopandas as gpd
    import ingest
    import matrix

    tif = volume_tile()
    g = gpd.read_parquet(ingest.ADM4)
    gx, gy, vol, _, _ = util.grid_sums(tif, g, config.DEST_GRID_M)
    vdf = pd.DataFrame({"x": gx, "y": gy, "nres_m3": vol})
    d = dests.merge(vdf, on=["x", "y"], how="left")
    d["nres_m3"] = d["nres_m3"].fillna(0.0)
    tot_v, tot_s = float(d["nres_m3"].sum()), float(d["nres_m2"].sum())
    if tot_v <= 0:
        return {"available": False, "reason": "volume tile carried no value over the region"}
    m = matrix.load("all")
    m = m[m.tt <= 60].join(d.set_index("id")[["nres_m3", "nres_m2"]], on="to_id")
    gsum = m.groupby("from_id")[["nres_m3", "nres_m2"]].sum()
    base = acc[(acc.scenario == "all") & (acc.cutoff == 60)].set_index("id")
    j = base.join(gsum).fillna({"nres_m3": 0.0, "nres_m2": 0.0})
    j["vol_share"] = j["nres_m3"] / tot_v
    pack_s = dist_pack(j.jobs_share.values, j["pop"].values); pack_s.pop("lorenz")
    pack_v = dist_pack(j.vol_share.values, j["pop"].values)
    lorenz_v = pack_v.pop("lorenz")
    dki = j[j.dki], j[~j.dki]
    for p, col in ((pack_s, "jobs_share"), (pack_v, "vol_share")):
        p["dki_median"] = round(float(eq.wmedian(dki[0][col].values, dki[0]["pop"].values)), 6)
        p["bodetabek_median"] = round(float(eq.wmedian(dki[1][col].values, dki[1]["pop"].values)), 6)
        p["ratio"] = (round(p["dki_median"] / p["bodetabek_median"], 1)
                      if p["bodetabek_median"] > 0 else None)
    # where the two denominators sit
    dd = d[d["nres_m2"] > 0]
    storeys = float((dd["nres_m3"].sum() / dd["nres_m2"].sum()) / 3.0)   # 3 m per storey (GHSL)
    return {"available": True,
            "total_volume_km3": round(tot_v / 1e9, 3),
            "total_surface_km2": round(tot_s / 1e6, 1),
            "mean_storeys": round(storeys, 2),
            "surface": pack_s, "volume": pack_v, "lorenz_volume": lorenz_v,
            "rho_surface_volume": round(spearman(j.jobs_share.values, j.vol_share.values), 4),
            "mean_lift": round(float(np.average(j.vol_share, weights=j["pop"]))
                               / float(np.average(j.jobs_share, weights=j["pop"])), 3),
            "source": ("JRC GHSL GHS-BUILT-V NRES E2020 R2023A, tile R10_C29, "
                       "same 100 m grid and licence as the surface product")}


# ── K · the denominator makes the headline — a city-scale measure for comparison ───────────
DKI_PREFIXES = ("ID3101", "ID3171", "ID3172", "ID3173", "ID3174", "ID3175")


def test_k(acc: pd.DataFrame, dests: pd.DataFrame) -> dict:
    """The published measure asks what share of a 6,000 km² REGION's floorspace a resident can
    reach. Every published benchmark asks what share of a CITY's. Recompute the second, on the
    same routed matrix: DKI residents, DKI destinations, DKI denominator.
    """
    import matrix
    d = dests.copy()
    d["dki"] = d["adm4_pcode"].astype(str).str.startswith(DKI_PREFIXES)
    tot_city = float(d.loc[d.dki, "nres_m2"].sum())
    tot_reg = float(d["nres_m2"].sum())
    m = matrix.load("all")
    m = m[m.tt <= 60].join(d.set_index("id")[["nres_m2", "dki"]], on="to_id")
    city = m[m.dki].groupby("from_id")["nres_m2"].sum() / tot_city
    base = acc[(acc.scenario == "all") & (acc.cutoff == 60)].set_index("id")
    j = base.join(city.rename("city_share")).fillna({"city_share": 0.0})
    jd = j[j.dki]
    pack = dist_pack(jd.city_share.values, jd["pop"].values); pack.pop("lorenz")
    reg = dist_pack(jd.jobs_share.values, jd["pop"].values); reg.pop("lorenz")
    return {"city_denominator_share_of_region": round(tot_city / tot_reg, 4),
            "dki_on_city_denominator": pack,
            "dki_on_region_denominator": reg,
            "lift": round(pack["mean"] / reg["mean"], 2),
            "note": "DKI residents only, DKI destinations only, DKI floorspace as the denominator; "
                    "identical travel-time matrix, nothing re-routed."}


def main() -> None:
    acc = pd.read_parquet(config.ACCESS_ADM4)
    acc["dki"] = acc.adm1_name.str.contains("Jakarta", case=False, na=False)
    import geopandas as gpd
    import ingest
    import points
    adm4 = gpd.read_parquet(ingest.ADM4)
    dests = pd.DataFrame(points.build_destinations().drop(columns="geometry"))
    stats = json.loads(config.STATS_JSON.read_text())

    out = {
        "generated": pd.Timestamp.utcnow().isoformat(timespec="seconds"),
        "vintage": stats["generated"][:10],
        "window": stats["window"],
        "origins": int(acc.id.nunique()),
        "destinations": int(len(dests)),
        "A_threshold": test_a(acc),
        "B_naive": test_b(acc, dests),
        "C_opportunity": test_c(acc),
        "D_maup": test_d(acc, adm4),
        "E_edge": test_e(acc),
        "F_rail": test_f(stats),
        "G_poverty": test_g(acc),
        "H_precision": test_h(acc),
        "I_incidence": test_i(acc),
        "J_volume": test_j(acc, dests),
        "K_denominator": test_k(acc, dests),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, allow_nan=False, separators=(",", ":")))
    log("review →", OUT, f"{OUT.stat().st_size/1e6:.2f} MB")

    # A few scalars the case page states in prose. Published as their own small file so the
    # page never hand-types a number this stage computed, and never pays for the full file.
    jv, dm, fr = out["J_volume"], out["D_maup"], out["F_rail"]
    summary = {
        "generated": out["generated"], "vintage": out["vintage"],
        "volume": {"dki_median_surface": jv["surface"]["dki_median"],
                   "dki_median_volume": jv["volume"]["dki_median"],
                   "ratio_surface": jv["surface"]["ratio"], "ratio_volume": jv["volume"]["ratio"],
                   "rho": jv["rho_surface_volume"], "mean_storeys": jv["mean_storeys"]},
        "maup": {"levels": [{"level": r["level"], "units": r["units"], "gini": r["gini"],
                             "palma": r["palma"]} for r in dm["rungs"]],
                 "area_min": dm["area_km2"]["min"], "area_max": dm["area_km2"]["max"],
                 "area_ratio": dm["area_km2"]["ratio"]},
        "rail_optimism": {"min": min(r["observed_optimism"] for r in fr["rows"]),
                          "max": max(r["observed_optimism"] for r in fr["rows"]),
                          "mean": fr["mean_optimism"], "chord_deficit": fr["mean_deficit"]},
        "denominator": {"city_mean": out["K_denominator"]["dki_on_city_denominator"]["mean"],
                        "region_mean": out["K_denominator"]["dki_on_region_denominator"]["mean"],
                        "lift": out["K_denominator"]["lift"],
                        "study_area_km2": dm["study_area_km2"]},
        "naive_r2": out["B_naive"]["r2_naive"],
        "network_failures": out["B_naive"]["network_failures"],
        "network_failure_pop": out["B_naive"]["network_failure_pop"],
        "precision": {"quantised": out["H_precision"]["quantised_to_zero"],
                      "quantised_pop": out["H_precision"]["quantised_pop"]},
    }
    pub = config.WEB_DATA / "review_summary.json"
    pub.parent.mkdir(parents=True, exist_ok=True)
    pub.write_text(json.dumps(summary, allow_nan=False, separators=(",", ":")))
    log("review summary →", pub, f"{pub.stat().st_size/1e3:.1f} kB")
    a = out["A_threshold"]
    log(f"  A · Gini {a['rows'][0]['gini']} → {a['rows'][-1]['gini']} across 30→60 min; "
        f"Palma {a['rows'][0]['palma']} → {a['rows'][-1]['palma']}; "
        f"DKI/Bodetabek ratio {a['rows'][0]['ratio']} → {a['rows'][-1]['ratio']}")
    log(f"  B · naive circle+distance R2 {out['B_naive']['r2_naive']}, "
        f"rho(circle, routed) {out['B_naive']['rho_circle_routed']}")
    log(f"  D · Gini by level " + " ".join(f"{r['level']}:{r['gini']}" for r in out["D_maup"]["rungs"]))
    log(f"  F · mean scheduled optimism {out['F_rail']['mean_optimism']:.1%} vs "
        f"chord deficit {out['F_rail']['mean_deficit']:.1%}")
    g = out["G_poverty"]
    log(f"  G · poverty link {'AVAILABLE' if g['available'] else 'unavailable: ' + g.get('reason','')}"
        + (f" rho {g['spearman_rho']} CI {g['concentration_index']}" if g["available"] else ""))
    log(f"  H · {out['H_precision']['quantised_to_zero']} kelurahan published as zero but are not")
    log(f"  I · rail gain to DKI {out['I_incidence']['rail']['dki_share_of_gain']:.0%} of a "
        f"{out['I_incidence']['rail']['dki_share_of_pop']:.0%} population; "
        f"Gini +{out['I_incidence']['gini_added_by_rail']}")
    jv = out["J_volume"]
    if jv.get("available"):
        log(f"  J · volume vs surface: DKI median {jv['surface']['dki_median']:.4f} → "
            f"{jv['volume']['dki_median']:.4f}, Gini {jv['surface']['gini']} → {jv['volume']['gini']}, "
            f"mean lift ×{jv['mean_lift']}, rho {jv['rho_surface_volume']}")
    else:
        log("  J · volume test unavailable:", jv.get("reason"))
    k = out["K_denominator"]
    log(f"  K · DKI mean access on a region denominator {k['dki_on_region_denominator']['mean']:.4f} → "
        f"on a city denominator {k['dki_on_city_denominator']['mean']:.4f} (×{k['lift']}), "
        f"Gini {k['dki_on_region_denominator']['gini']} → {k['dki_on_city_denominator']['gini']}")
    log(f"  study area {out['D_maup']['study_area_km2']:.0f} km², DKI {out['D_maup']['dki_area_km2']:.0f} km²")


if __name__ == "__main__":
    main()
