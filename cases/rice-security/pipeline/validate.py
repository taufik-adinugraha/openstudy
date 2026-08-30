"""Stage 9 · validate — gates G-I1..G-I5, with thresholds fixed before any result was seen.

Thresholds live in ``config`` and were set at spec time.  A gate that fails is published RED with
a diagnosis — the house precedent is Case C's G-C5 and Case H's G-H1, both of which shipped
failing and explained.  Thresholds are never moved to fit an outcome, and no threshold in this
file has been touched since the spec was written.

ONE SCOPE ADAPTATION, STATED RATHER THAN SMUGGLED
--------------------------------------------------
G-I1's first clause is written as "provincial annual harvested area within +/-10 %".  This build
measures six kabupaten, not three whole provinces, so a provincial claim is not ours to make.
The gate is therefore evaluated on the DEEP-SCOPE AGGREGATE — the sum of the six kabupaten
against the sum of the same six kabupaten's KSA — which is the same test at the largest unit the
evidence supports.  It is not the easier test: aggregating six units does not cancel a systematic
detector bias, it exposes it.  The kabupaten clause (R2 and MAPE) is unchanged.

Everything is reported twice, on the CALIBRATED and the UNCALIBRATED series, because a gate
applied only after fitting to the benchmark is not a gate.

OUTPUT: data/stats.json — every gate with threshold, computed value, pass/fail and a one-line
diagnosis when it fails.  The dashboard renders this table verbatim.
"""

from __future__ import annotations

import json

import config
import util
from util import log


def _r2(y, yh):
    import numpy as np

    y, yh = np.asarray(y, "float64"), np.asarray(yh, "float64")
    ok = np.isfinite(y) & np.isfinite(yh)
    if ok.sum() < 3:
        return None
    ss_res = float(((y[ok] - yh[ok]) ** 2).sum())
    ss_tot = float(((y[ok] - y[ok].mean()) ** 2).sum())
    return round(1 - ss_res / ss_tot, 4) if ss_tot else None


def _mape(y, yh):
    import numpy as np

    y, yh = np.asarray(y, "float64"), np.asarray(yh, "float64")
    ok = np.isfinite(y) & np.isfinite(yh) & (y > 0)
    return round(float(100 * np.abs((yh[ok] - y[ok]) / y[ok]).mean()), 2) if ok.sum() else None


def _peak_week(months, values):
    """The peak-harvest week of a monthly curve — argmax, refined parabolically on the calendar
    circle.

    The obvious implementation, an amplitude-weighted centroid of the top of the distribution,
    is WRONG on this data and wrong in a way that looks reasonable: a calendar is a circle and a
    linear centroid of a curve with mass in both December and January lands in June.  Java's
    harvest is genuinely bimodal — a main wet-season peak around March and a gadu peak around
    September — so that failure mode is the normal case here, not an edge case.

    So the peak is the largest month, refined by fitting a parabola through it and its two
    circular neighbours, which recovers sub-month resolution from a monthly series without
    inventing a shape.  Both series get exactly the same treatment, so the comparison is like
    for like and neither can be flattered by the choice.
    """
    import numpy as np

    v = np.zeros(12, "float64")
    for m_, val in zip(np.asarray(months, "int64"), np.asarray(values, "float64")):
        if 1 <= m_ <= 12 and np.isfinite(val):
            v[m_ - 1] += val
    if v.max() <= 0:
        return None
    i = int(np.argmax(v))
    a, b, c = v[(i - 1) % 12], v[i], v[(i + 1) % 12]
    denom = a - 2 * b + c
    shift = 0.5 * (a - c) / denom if denom != 0 else 0.0
    shift = float(np.clip(shift, -0.5, 0.5))
    month_pos = i + 0.5 + shift              # months from the start of the year, mid-month
    return month_pos * (52.0 / 12.0)


def _circ_diff_weeks(a, b):
    """Difference in weeks on a 52-week circle — a January peak is one week from December."""
    d = (a - b) % 52.0
    return d - 52.0 if d > 26.0 else d


def g_i1(panel, year_panel):
    import numpy as np

    out = {"id": "G-I1", "name": "KSA reconciliation (hard)",
           "threshold": {"aggregate_pct": config.GATE_PROV_PCT,
                         "kabupaten_r2": config.GATE_KAB_R2,
                         "kabupaten_mape": config.GATE_KAB_MAPE},
           "variants": {}}
    usable = year_panel[year_panel["ksa_ha"].notna() & year_panel["benchmark_usable"]]
    for variant, col in (("uncalibrated", "harvested_ha"), ("calibrated", "calibrated_ha")):
        agg = usable.groupby("year").agg(ours=(col, "sum"), ksa=("ksa_ha", "sum")).reset_index()
        agg["pct"] = 100 * (agg["ours"] / agg["ksa"] - 1)
        worst = float(agg["pct"].abs().max()) if len(agg) else None
        out["variants"][variant] = {
            "aggregate_by_year": [{"year": int(r.year), "ours_ha": round(r.ours, 0),
                                   "ksa_ha": round(r.ksa, 0), "diff_pct": round(r.pct, 2)}
                                  for r in agg.itertuples()],
            "worst_abs_diff_pct": round(worst, 2) if worst is not None else None,
            "aggregate_pass": bool(worst is not None and worst <= config.GATE_PROV_PCT),
            "kabupaten_r2": _r2(usable["ksa_ha"], usable[col]),
            "kabupaten_mape": _mape(usable["ksa_ha"], usable[col]),
            "n_kabupaten_years": int(len(usable)),
        }
        v = out["variants"][variant]
        v["kabupaten_pass"] = bool(
            v["kabupaten_r2"] is not None and v["kabupaten_r2"] >= config.GATE_KAB_R2
            and v["kabupaten_mape"] is not None and v["kabupaten_mape"] <= config.GATE_KAB_MAPE)
        v["pass"] = bool(v["aggregate_pass"] and v["kabupaten_pass"])
    out["pass"] = bool(out["variants"]["uncalibrated"]["pass"]
                       and out["variants"]["calibrated"]["pass"])
    return out


def g_i2(panel):
    """Peak-harvest week, ours against the monthly KSA panel, per kabupaten and CALENDAR year.

    The spec frames this per season, and a crop year (1 Jul - 30 Jun) is the right unit for
    everything else in the case.  It is the wrong unit HERE, and for a reason worth stating
    rather than working around: BPS's kabupaten monthly tables run to December 2025, so the
    hold-out season 2025/26 has only six of its twelve months published.  Scoring a peak week
    from half a season would not be a hard test, it would be a different test.  A calendar year
    contains Java's main harvest peak (February to April) whole, the benchmark is complete for
    every calendar year in the record, and calendar 2025 is outside the calibration years either
    way — so the hold-out property survives the change.
    """
    import numpy as np
    import pandas as pd

    rows = []
    p = panel[panel["ksa_ha"].notna() & panel["benchmark_usable"]]
    for (kab, season), g in p.groupby(["kabupaten", "year"], observed=True):
        g = g.sort_values(["year", "month"])
        if g["ksa_ha"].notna().sum() < 10:
            continue
        ours = _peak_week(g["month"], g["harvested_ha"])
        pred = _peak_week(g["month"], g["harvested_ha_pred"].fillna(0)) \
            if "harvested_ha_pred" in g else None
        theirs = _peak_week(g["month"], g["ksa_ha"].fillna(0))
        if ours is None or theirs is None:
            continue
        rows.append(dict(kabupaten=kab, season=int(season),
                         ours_week=round(ours, 2), predicted_week=(round(pred, 2) if pred else None),
                         ksa_week=round(theirs, 2),
                         error_weeks=round(_circ_diff_weeks(ours, theirs), 2),
                         pred_error_weeks=(round(_circ_diff_weeks(pred, theirs), 2)
                                           if pred else None)))
    df = pd.DataFrame(rows)
    out = {"id": "G-I2", "name": "Harvest-timing accuracy (hard)",
           "threshold": {"median_abs_error_weeks": config.GATE_TIMING_WEEKS,
                         "max_unit_bias_weeks": config.GATE_TIMING_BIAS_WEEKS},
           "n_kabupaten_seasons": int(len(df)),
           "unit": "kabupaten x calendar year",
           "note": ("evaluated on the monthly KSA panel West and East Java publish per regency; "
                    "Jawa Tengah is annual-only so Grobogan is excluded from this gate, and "
                    "said so on the page. The unit is the calendar year rather than the crop "
                    "year because BPS's kabupaten monthly tables end at December 2025 and half "
                    "a season is a different test, not a harder one")}
    if not len(df):
        out.update({"pass": False,
                    "diagnosis": "no kabupaten-year had enough monthly KSA to score"})
        return out, df
    mae = float(df["error_weeks"].abs().median())
    bias = df.groupby("kabupaten")["error_weeks"].mean()
    out.update(
        median_abs_error_weeks=round(mae, 2),
        unit_bias_weeks={k: round(float(v), 2) for k, v in bias.items()},
        worst_unit_bias_weeks=round(float(bias.abs().max()), 2),
        rows=df.to_dict("records"),
    )
    out["pass"] = bool(mae <= config.GATE_TIMING_WEEKS
                       and float(bias.abs().max()) <= config.GATE_TIMING_BIAS_WEEKS)
    if not out["pass"]:
        out["diagnosis"] = (
            f"median |error| {mae:.2f} wk against a {config.GATE_TIMING_WEEKS} wk threshold; "
            f"worst unit bias {bias.abs().max():.2f} wk "
            f"({bias.abs().idxmax()}) against {config.GATE_TIMING_BIAS_WEEKS} wk")
    return out, df


def g_i3(ci, full_years):
    # Only complete calendar years count.  The SAR record opens on 1 July 2022 and closes on
    # 31 August 2026, so 2022 and 2026 are half-years and their cycles-per-year is arithmetic
    # about the window, not about the crop.
    ci = ci[ci["year"].isin(full_years)]
    out = {"id": "G-I3", "name": "Cropping-intensity plausibility",
           "threshold": {"irrigated": list(config.GATE_CI_IRRIGATED),
                         "rainfed_max": config.GATE_CI_RAINFED},
           "full_years": [int(y) for y in full_years],
           "units": {}}
    ok = True
    for kab, meta in config.SCOPE_DEEP.items():
        g = ci[ci["kabupaten"] == kab]
        if not len(g):
            continue
        mean_ci = float(g["ci"].mean())
        irrigated = "irrigat" in str(meta.get("system", ""))
        if irrigated:
            passed = config.GATE_CI_IRRIGATED[0] <= mean_ci <= config.GATE_CI_IRRIGATED[1]
        else:
            passed = mean_ci < config.GATE_CI_RAINFED
        out["units"][kab] = {"system": meta.get("system"), "cycles_per_year": round(mean_ci, 2),
                             "by_year": {int(r.year): round(float(r.ci), 2)
                                         for r in g.itertuples()},
                             "pass": bool(passed)}
        ok &= passed
    out["pass"] = bool(ok and out["units"])
    return out


def g_i4(ph, cells):
    """Detected paddy extent against Open-SEA-Rice-10 — measured, and the disagreement mapped."""
    import numpy as np

    out = {"id": "G-I4", "name": "Independent rice-map agreement",
           "threshold": {"agreement": config.GATE_MASK_AGREE}}
    if "mask_class" not in cells.columns or not (cells["mask_class"] > 0).any():
        out.update({"pass": None, "evaluated": False,
                    "diagnosis": "Open-SEA-Rice-10 not available in this run — gate NOT "
                                 "EVALUATED, and reported as not evaluated rather than a pass"})
        return out
    det = set(zip(ph["kabupaten"], ph["cell_i"]))
    key = list(zip(cells["kabupaten"], cells.groupby("kabupaten").cumcount()))
    is_det = np.array([k in det for k in key])
    prior = (cells["mask_class"].to_numpy() > 0)
    inter = int((is_det & prior).sum())
    out.update(
        cells_total=int(len(cells)),
        prior_rice_cells=int(prior.sum()),
        detected_cells=int(is_det.sum()),
        agreement_on_prior=round(float(inter / max(prior.sum(), 1)), 4),
        agreement_on_ours=round(float(inter / max(is_det.sum(), 1)), 4),
        prior_only_cells=int((prior & ~is_det).sum()),
        ours_only_cells=int((is_det & ~prior).sum()),
        classes={int(c): int((cells["mask_class"].to_numpy() == c).sum())
                 for c in np.unique(cells["mask_class"])},
        evaluated=True,
    )
    out["pass"] = bool(out["agreement_on_prior"] >= config.GATE_MASK_AGREE)
    if not out["pass"]:
        out["diagnosis"] = (f"we reproduce {out['agreement_on_prior']:.1%} of the published "
                            f"map's rice area against a {config.GATE_MASK_AGREE:.0%} threshold; "
                            f"the disagreement is mapped rather than absorbed")
    return out


def g_i5(g1, g2df, panel):
    """The hold-out season, scored once and reported whether or not it flatters."""
    import numpy as np

    out = {"id": "G-I5", "name": "Temporal hold-out",
           "holdout_season": config.HOLDOUT_SEASON,
           "note": "excluded from calibration entirely; scored once, before any tuning"}
    h = panel[panel["is_holdout"] & panel["ksa_ha"].notna() & panel["benchmark_usable"]]
    out["n_rows"] = int(len(h))
    if len(h):
        for variant, col in (("uncalibrated", "harvested_ha"), ("calibrated", "calibrated_ha")):
            out[variant] = {"r2": _r2(h["ksa_ha"], h[col]),
                            "mape": _mape(h["ksa_ha"], h[col]),
                            "sum_ours_ha": round(float(h[col].sum()), 0),
                            "sum_ksa_ha": round(float(h["ksa_ha"].sum()), 0)}
            out[variant]["diff_pct"] = round(
                100 * (out[variant]["sum_ours_ha"] / out[variant]["sum_ksa_ha"] - 1), 2) \
                if out[variant]["sum_ksa_ha"] else None
    if len(g2df):
        # calendar 2025 — the last complete benchmark year, and outside CAL_YEARS
        hh = g2df[g2df["season"] == int(config.HOLDOUT_SEASON[:4])]
        out["timing_year"] = int(config.HOLDOUT_SEASON[:4])
        out["timing"] = {"n": int(len(hh)),
                         "median_abs_error_weeks": (round(float(hh["error_weeks"].abs().median()), 2)
                                                    if len(hh) else None)}
    ok_area = (out.get("uncalibrated", {}).get("mape") is not None
               and out["uncalibrated"]["mape"] <= config.GATE_KAB_MAPE)
    ok_time = (out.get("timing", {}).get("median_abs_error_weeks") is not None
               and out["timing"]["median_abs_error_weeks"] <= config.GATE_TIMING_WEEKS)
    out["pass"] = bool(ok_area and ok_time)
    return out


def main() -> None:
    import numpy as np
    import pandas as pd

    util.guard_disk()
    D = config.DATA_DIR
    panel = pd.read_parquet(D / "model.parquet")
    ci = pd.read_parquet(D / "cropping_intensity.parquet")
    ph = pd.read_parquet(D / "phenology.parquet")
    cells = pd.read_parquet(D / "cells.parquet")
    ksa_y = pd.read_parquet(D / "bps_kab_year.parquet")

    year_panel = (panel.groupby(["province", "kabupaten", "year"], observed=True)
                  .agg(harvested_ha=("harvested_ha", "sum"),
                       calibrated_ha=("calibrated_ha", "sum"),
                       planted_ha=("planted_ha", "sum")).reset_index()
                  .merge(ksa_y[["kab", "year", "ha", "benchmark_usable"]]
                         .rename(columns={"kab": "kabupaten", "ha": "ksa_ha"}),
                         on=["kabupaten", "year"], how="left"))
    year_panel["benchmark_usable"] = year_panel["benchmark_usable"].fillna(False)
    # a partial year at either end of the SAR record is not a full year of harvests
    full = [y for y in sorted(year_panel["year"].unique())
            if y >= pd.Timestamp(config.SAR_START).year + 1
            and y <= pd.Timestamp(config.SAR_END).year - 1]
    year_panel["full_year"] = year_panel["year"].isin(full)
    year_panel = year_panel[year_panel["full_year"]]

    gates = []
    gates.append(g_i1(panel, year_panel))
    g2, g2df = g_i2(panel)
    gates.append(g2)
    gates.append(g_i3(ci, full))
    gates.append(g_i4(ph, cells))
    gates.append(g_i5(gates[0], g2df, panel))

    # threshold sensitivity — the detector's constants are literature values, not fitted, and
    # the honest thing is to publish how much every headline moves when each one moves.
    sens = {}
    try:
        import backscatter  # noqa: F401
        import phenology as PH

        base_ha = float(ph["ha"].sum())
        for name, kw in (
                (f"flood drop {config.FLOOD_DROP_DB - 1:.0f} dB (looser)",
                 {"flood_drop_db": config.FLOOD_DROP_DB - 1}),
                (f"flood drop {config.FLOOD_DROP_DB + 1:.0f} dB (stricter)",
                 {"flood_drop_db": config.FLOOD_DROP_DB + 1}),
                (f"VH rise {config.RISE_DB - 1:.0f} dB (looser)",
                 {"rise_db": config.RISE_DB - 1}),
                (f"VH rise {config.RISE_DB + 1:.0f} dB (stricter)",
                 {"rise_db": config.RISE_DB + 1}),
                ("literature -17 dB absolute, unchanged",
                 {"flood_drop_db": 0.0, "flood_db_absolute": config.FLOOD_DB})):
            tot = 0.0
            for kab in list(config.SCOPE_DEEP)[:2]:      # two units is enough to size the effect
                f = D / "bs" / f"{kab}.npz"
                if not f.exists():
                    continue
                z = np.load(f)
                ck = cells[cells["kabupaten"] == kab]

                def deq(a):
                    b = a.astype("float32")
                    b[a == -32768] = np.nan
                    return b / 100.0
                vv, vh = deq(z["vv"]), deq(z["vh"])
                near = z["nearest"].astype("float32")
                n = 0
                for c0 in range(0, vv.shape[0], 30_000):
                    c1 = min(c0 + 30_000, vv.shape[0])
                    r = PH.detect_cycles(vv[c0:c1], vh[c0:c1], near, config.STEP_DAYS, kw)
                    n += len(r["cell"])
                tot += n * float(ck["ha"].iloc[0])
            sens[name] = round(tot, 0)
        sens["_baseline_two_units_ha"] = round(
            float(ph[ph["kabupaten"].isin(list(config.SCOPE_DEEP)[:2])]["ha"].sum()), 0)
    except Exception as exc:                                   # noqa: BLE001
        sens = {"error": f"{type(exc).__name__}: {exc}"}

    stats = {
        "case": "rice-security",
        "generated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": {"provinces": list(config.SCOPE_PROVINCES),
                  "deep_kabupaten": list(config.SCOPE_DEEP),
                  "cells": int(len(cells)), "cell_m": config.CELL_M,
                  "kabupaten_ha": round(float(cells["ha"].sum()), 0),
                  "sar_window": [config.SAR_START, config.SAR_END],
                  "seasons": list(config.SEASONS)},
        "gates": gates,
        "gates_passed": sum(1 for g in gates if g.get("pass") is True),
        "gates_total": len(gates),
        "detector_thresholds": {
            "flood_drop_db": config.FLOOD_DROP_DB,
            "flood_baseline_pctl": config.FLOOD_BASELINE_PCTL,
            "flood_db": config.FLOOD_DB, "rise_db": config.RISE_DB,
            "rise_window_days": config.RISE_WINDOW_DAYS,
            "heading_window_days": config.HEADING_WINDOW_DAYS,
            "head_to_harvest_days": config.HEAD_TO_HARVEST_DAYS,
            "min_cycle_days": config.MIN_CYCLE_DAYS, "max_gap_days": config.MAX_GAP_DAYS,
            "note": "literature values, not fitted — fitting them on the benchmark would "
                    "guarantee agreement and destroy the gate",
            "restatement": (
                "One threshold had to be restated, and it is the only definition this build "
                "changed. The literature's VV < -17 dB is a SINGLE-PLOT value. At a 100 m "
                "analysis cell it detects 0.01 cycles per cell — a measurement of our own cell "
                "size, not of Indonesian rice: over Indramayu's rice-prior cells the deepest VV "
                "the whole record reaches has a median of -13.1 dB and only 2.4 % ever cross "
                "-17 dB, while the VH seasonal range is a healthy 8.1 dB, so the crop signal is "
                "intact and it is the absolute level that does not survive the scale. A flooding "
                "event is therefore a fall of at least the drop threshold below the cell's OWN "
                "non-flooded baseline. The constant was set from physics before any gate was "
                "evaluated (the single-plot specular drop is 6-10 dB; roughly half a 100 m "
                "cell is in synchronised transplanting, so half the drop is the scale-adapted "
                "equivalent) and was NOT fitted to KSA. The absolute value is still recorded per "
                "detected event, and the sensitivity table below includes the unchanged "
                "literature rule so the cost of the restatement is visible rather than argued."),
            "share_events_also_below_literature_flood_db": {
                k: v.get("share_events_below_literature_flood_db")
                for k, v in json.loads(
                    (D / "phenology_diag.json").read_text()).items()},
        },
        "threshold_sensitivity_ha": sens,
        "year_panel": year_panel.to_dict("records"),
    }
    config.STATS_JSON.write_text(json.dumps(stats, indent=1, default=str))
    log(f"validate -> {config.STATS_JSON}")
    for g in gates:
        mark = {True: "PASS", False: "FAIL", None: "NOT EVALUATED"}[g.get("pass")]
        log(f"    {g['id']}  {mark:13s} {g['name']}")
        if g["id"] == "G-I1":
            for v in ("uncalibrated", "calibrated"):
                d = g["variants"][v]
                log(f"        {v:12s} worst-year {d['worst_abs_diff_pct']}% "
                    f"R2={d['kabupaten_r2']} MAPE={d['kabupaten_mape']}%")
        if g["id"] == "G-I2" and "median_abs_error_weeks" in g:
            log(f"        median |err| {g['median_abs_error_weeks']} wk, "
                f"worst unit bias {g['worst_unit_bias_weeks']} wk")
        if g["id"] == "G-I3":
            for k, v in g["units"].items():
                log(f"        {k:11s} {v['cycles_per_year']} cycles/yr ({v['system']})")
        if g["id"] == "G-I4" and g.get("evaluated"):
            log(f"        agreement on the prior {g['agreement_on_prior']:.1%}, "
                f"on ours {g['agreement_on_ours']:.1%}")


if __name__ == "__main__":
    main()
