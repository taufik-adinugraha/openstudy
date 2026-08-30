"""Stage 10 · validate — gates G-J1 … G-J5.

Thresholds were fixed in ``config`` before any result was seen, and they are never moved to fit
an outcome.  A gate that fails ships RED with a diagnosis — the house precedent is Case C's G-C5
and Case H's G-H1, both of which are on their pages today, failing and explained.  A gate whose
input has not arrived ships PENDING with the reason and the exact thing a human has to do; it is
never quietly recorded as a pass.

  G-J1  hotspot hygiene (hard).  Zero retained detections inside the static exclusion mask, on
        EVERY product including NRT — which is the whole point, since a ``type``-based filter
        passes this gate trivially while doing nothing to the live tail.  ** A removal share
        below 0.5 % also FAILS **: that is evidence the filter is broken, not evidence that
        Indonesia has no volcanoes.
  G-J2  ignition skill (hard).  AUC >= 0.80 and a Brier skill score > 0 against BOTH the per-cell
        day-of-year climatology AND the CEMS Canadian FWI, at every lead, for both paths.
  G-J3  transport direction.  Receptor-to-source bearing from the back-trajectory agrees with the
        forward run's implied bearing within +-30 deg on >= 70 % of episode days.  This gates the
        INTEGRATOR, not the physics, and says so in those words.
  G-J4  receptor correlation (hard at tier 1 only).  Spearman rho >= 0.5 at Singapore.  Every
        receptor is reported including the ones that fail, each with its tier.
  G-J5  anchor replay.  2015 and 2019, never trained on, must both land in the top decile of
        seasonal severity.  2015 has no Singapore ground truth, so its reference is FIRMS plus
        CAMS EAC4 — stated, not quietly substituted.

OUTPUT: ``data/stats.json`` — every gate with its threshold, its computed value, pass/fail, and a
one-line diagnosis when it fails.  The dashboard renders this table verbatim.
"""

from __future__ import annotations

import json
import math
from datetime import date

import config
import util
from util import log


def _nan_safe(o):
    if isinstance(o, dict):
        return {k: _nan_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_nan_safe(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if hasattr(o, "item"):
        try:
            return _nan_safe(o.item())
        except Exception:                                   # noqa: BLE001
            return str(o)
    return o


def _read(name):
    import pandas as pd
    p = config.DATA_DIR / name
    return pd.read_parquet(p) if p.exists() else None


def _json(name):
    p = config.DATA_DIR / name
    return json.loads(p.read_text()) if p.exists() else None


# ── G-J1 ──────────────────────────────────────────────────────────────────────────────
def gate_j1() -> dict:
    import numpy as np
    import pandas as pd
    import ingest_fires as F
    fires = _read("fires.parquet")
    mask = _read("static_mask.parquet")
    audit = _json("fires_audit.json")
    if fires is None or mask is None or audit is None:
        return {"gate": "G-J1", "hard": True, "pass": None, "status": "PENDING",
                "reason": "the fires stage has not produced fires.parquet / static_mask.parquet"}
    mask_ids = set(mask["cell"].astype("int64").tolist())
    keys = F.fine_cells(fires["lat"].to_numpy(), fires["lon"].to_numpy())
    inside = pd.Series(keys).isin(mask_ids)
    per_product = {}
    for prod, g in fires.groupby("product"):
        k = F.fine_cells(g["lat"].to_numpy(), g["lon"].to_numpy())
        per_product[str(prod)] = {"rows": int(len(g)),
                                  "retained_inside_mask": int(pd.Series(k).isin(mask_ids).sum())}
    share = float(audit["removed_share"])
    leaked = int(inside.sum())
    ok_leak = leaked == 0
    ok_share = share >= config.GATE_REMOVED_MIN_SHARE
    reasons = []
    if not ok_leak:
        reasons.append(f"{leaked:,} retained detections fall inside the static mask")
    if not ok_share:
        reasons.append(f"removal share {share:.2%} is below the {config.GATE_REMOVED_MIN_SHARE:.1%} "
                       f"floor — the filter is not biting, which is a broken filter, not a "
                       f"country without volcanoes")
    return {
        "gate": "G-J1", "hard": True, "pass": bool(ok_leak and ok_share),
        "threshold": {"retained_inside_mask": 0,
                      "min_removed_share": config.GATE_REMOVED_MIN_SHARE},
        "retained_inside_mask": leaked,
        "removed_share": share,
        "removed_composition": audit["removed_composition"],
        "per_product": per_product,
        "nrt_removed_share": audit["nrt_removed_share"],
        "nrt_rows": audit["nrt_rows"],
        "type_present_share": audit["type_present_share"],
        "mask_cells": audit["mask_cells"], "mask_core_cells": audit["mask_core_cells"],
        "mask_sources": audit.get("mask_sources", {}),
        "why_it_matters": (
            "the FIRMS `type` field is absent from every NRT product, so a type filter removes "
            f"0 rows from the {audit['nrt_rows']:,}-row live tail; the static mask removes "
            f"{audit['nrt_removed_share']:.2%} of it"),
        "reason": "; ".join(reasons) or None,
    }


# ── G-J2 ──────────────────────────────────────────────────────────────────────────────
def gate_j2() -> dict:
    meta = _json("risk_meta.json")
    if meta is None:
        return {"gate": "G-J2", "hard": True, "pass": None, "status": "PENDING",
                "reason": "the risk stage has not run"}
    per_lead, fails = {}, []
    for lead in config.LEAD_DAYS:
        entry = meta["leads"].get(str(lead), {})
        row = {}
        for path in ("forecast", "reanalysis"):
            m = entry.get(path, {})
            auc = m.get("auc")
            bss_c = m.get("bss_vs_climatology")
            ok_auc = isinstance(auc, (int, float)) and auc >= config.GATE_AUC
            ok_bss = isinstance(bss_c, (int, float)) and bss_c > config.GATE_BSS
            row[path] = {"auc": auc, "bss_vs_climatology": bss_c,
                         "bss_vs_persistence": m.get("bss_vs_persistence"),
                         "brier": m.get("brier"), "base_rate": m.get("base_rate"),
                         "auc_pass": ok_auc, "bss_climatology_pass": ok_bss}
            if not ok_auc:
                fails.append(f"lead {lead}d {path}: AUC {auc:.3f} < {config.GATE_AUC}"
                             if isinstance(auc, (int, float))
                             else f"lead {lead}d {path}: no AUC")
            if not ok_bss:
                fails.append(f"lead {lead}d {path}: BSS vs climatology {bss_c:+.3f} <= 0"
                             if isinstance(bss_c, (int, float))
                             else f"lead {lead}d {path}: no BSS")
        row["foresight_gap_auc"] = entry.get("foresight_gap_auc")
        per_lead[str(lead)] = row

    fwi = meta.get("fwi", {})
    fwi_status = fwi.get("status")
    fwi_block = {}
    if fwi_status == "ok":
        for lead, m in (fwi.get("per_lead") or {}).items():
            b = m.get("bss_vs_fwi")
            ok = b is not None and b > config.GATE_BSS
            fwi_block[lead] = {**m, "pass": ok}
            if not ok:
                fails.append(f"lead {lead}d: BSS vs the CEMS FWI "
                             f"{b:+.3f} <= 0" if b is not None else
                             f"lead {lead}d: no FWI overlap")
    else:
        fwi_block = {"status": "PENDING", "reason": fwi.get("reason"),
                     "url": fwi.get("url") or fwi.get("urls")}

    half_pending = fwi_status != "ok"
    return {
        "gate": "G-J2", "hard": True,
        "pass": None if half_pending else (len(fails) == 0),
        "status": "PENDING (FWI half)" if half_pending else None,
        "threshold": {"auc": config.GATE_AUC, "bss": "> 0 vs climatology AND vs the CEMS FWI"},
        "per_lead": per_lead, "vs_fwi": fwi_block,
        "folds": meta.get("n_folds"), "fold_caveat": meta.get("fold_caveat"),
        "era5_years": meta.get("era5_years"),
        "climatology_half_pass": all(
            per_lead.get(str(L), {}).get(p, {}).get(k) is True
            for L in config.LEAD_DAYS
            for p in ("forecast", "reanalysis")
            for k in ("auc_pass", "bss_climatology_pass")),
        "note": ("the forecast path is compared against the FWI at ANALYSIS time, because no open "
                 "medium-range FWI forecast exists — that flatters us, and the size of the "
                 "flattery is the reanalysis-vs-forecast gap printed beside it"),
        "reason": "; ".join(fails) or None,
    }


# ── G-J3 ──────────────────────────────────────────────────────────────────────────────
def gate_j3() -> dict:
    import numpy as np
    bear = _read("bearing_check.parquet")
    meta = _json("transport_meta.json")
    if bear is None or bear.empty:
        return {"gate": "G-J3", "hard": False, "pass": None, "status": "PENDING",
                "reason": "the transport stage has not produced bearing checks"}
    share = float((bear["diff_deg"] <= config.GATE_BEARING_DEG).mean())
    ok = share >= config.GATE_BEARING_SHARE
    per_rec = {r: {"n": int(len(g)),
                   "share_within": float((g["diff_deg"] <= config.GATE_BEARING_DEG).mean()),
                   "median_diff_deg": float(g["diff_deg"].median())}
               for r, g in bear.groupby("receptor")}
    return {
        "gate": "G-J3", "hard": False, "pass": bool(ok),
        "threshold": {"within_deg": config.GATE_BEARING_DEG,
                      "min_share": config.GATE_BEARING_SHARE},
        "episode_days": int(len(bear)), "share_within": share,
        "median_diff_deg": float(bear["diff_deg"].median()),
        "per_receptor": per_rec,
        "gfas_height_share": (meta or {}).get("gfas_height_share"),
        "plume_rise_fallback_share": (meta or {}).get("plume_rise_fallback_share"),
        "scope": ("this gates the INTEGRATOR, not the physics: it asks whether the same engine "
                  "run forwards and backwards agrees about where the smoke came from.  The "
                  "physics check is the CAMS comparison, published as a divergence chart rather "
                  "than as a score we claim to win"),
        "cams_comparison": (meta or {}).get("cams_comparison"),
        "reason": None if ok else (
            f"bearings agree within {config.GATE_BEARING_DEG:.0f} deg on only {share:.1%} of "
            f"episode days, below the {config.GATE_BEARING_SHARE:.0%} threshold"),
    }


# ── G-J4 ──────────────────────────────────────────────────────────────────────────────
def gate_j4() -> dict:
    import numpy as np
    import pandas as pd
    exp = _read("receptor_exposure.parquet")
    ground = _read("ground.parquet")
    if exp is None or ground is None:
        return {"gate": "G-J4", "hard": True, "pass": None, "status": "PENDING",
                "reason": "transport or ground has not run"}
    ground["day"] = pd.to_datetime(ground["day"])
    exp["day"] = pd.to_datetime(exp["day"])
    per_rec = {}
    for name, m in config.RECEPTORS.items():
        g = ground[ground["receptor"] == name]
        e = exp[exp["receptor"] == name]
        j = g.merge(e, on="day", how="inner")
        j = j[j["day"].dt.month.isin(config.FIRE_SEASON_MONTHS)]
        if len(j) < 30:
            per_rec[name] = {"tier": m["tier"], "n": int(len(j)), "rho": None,
                             "status": "insufficient overlap",
                             "kind": "instrument" if m["tier"] in (1, 2) else "model",
                             "coverage": m.get("coverage")}
            continue
        rho = float(j[["pm25", "exposure"]].corr(method="spearman").iloc[0, 1])
        per_rec[name] = {
            "tier": m["tier"], "n": int(len(j)), "rho": rho,
            "kind": "instrument" if m["tier"] in (1, 2) else "model (CAMS EAC4 reanalysis)",
            "comparison": ("model vs instrument" if m["tier"] in (1, 2)
                           else "MODEL VS MODEL — labelled, never called an observation"),
            "coverage": m.get("coverage"), "note": m.get("note"),
            "pass": rho >= config.GATE_RHO,
            "first": str(j["day"].min().date()), "last": str(j["day"].max().date()),
        }
    sg = per_rec.get("singapore", {})
    rho = sg.get("rho")
    ok = rho is not None and rho >= config.GATE_RHO
    return {
        "gate": "G-J4", "hard": True, "pass": bool(ok) if rho is not None else None,
        "status": None if rho is not None else "PENDING",
        "threshold": {"spearman_rho": config.GATE_RHO, "hard_at": "singapore (tier 1)"},
        "singapore_rho": rho, "per_receptor": per_rec,
        "licence": config.NEA_LICENCE,
        "unmonitored": ("OpenAQ has zero PM2.5 locations in Riau and zero in all of Kalimantan; "
                        "tier-3 receptors are compared against CAMS EAC4 and labelled model vs "
                        "model"),
        "reason": None if ok else (
            f"Singapore Spearman rho {rho:.3f} < {config.GATE_RHO}" if rho is not None
            else "no Singapore overlap"),
    }


# ── G-J5 ──────────────────────────────────────────────────────────────────────────────
def gate_j5() -> dict:
    import numpy as np
    import pandas as pd
    nat = _read("risk_national.parquet")
    fires = _read("fires_daily.parquet")
    if nat is None or fires is None:
        return {"gate": "G-J5", "hard": False, "pass": None, "status": "PENDING",
                "reason": "risk or fires has not run"}
    lead = config.LEAD_DAYS[0]
    n = nat[nat["lead"] == lead].copy()
    n["day"] = pd.to_datetime(n["day"])
    n = n[n["day"].dt.month.isin(config.FIRE_SEASON_MONTHS)]
    modelled = n.groupby(n["day"].dt.year)["risk_mean"].mean()

    f = fires.copy()
    f["day"] = pd.to_datetime(f["day"])
    f = f[f["day"].dt.month.isin(config.FIRE_SEASON_MONTHS)]
    observed = f.groupby(f["day"].dt.year)["n_fire"].sum()

    def pct_rank(s, y):
        if y not in s.index or len(s) < 3:
            return None
        # percentile rank in [0, 1]: the largest value scores 1.0
        return float((s.rank(pct=True)[y] * len(s) - 1) / (len(s) - 1))

    res, fails = {}, []
    for a in config.ANCHOR_YEARS:
        pm = pct_rank(modelled, a)
        po = pct_rank(observed, a)
        ok = pm is not None and pm >= config.GATE_ANCHOR_DECILE
        res[str(a)] = {
            "modelled_seasonal_risk": float(modelled.get(a, np.nan)),
            "modelled_percentile": pm,
            "observed_detections": int(observed.get(a, 0)),
            "observed_percentile": po,
            "pass": ok,
            "reference": ("FIRMS detections + CAMS EAC4 — the NEA record begins 2016-03, so this "
                          "anchor has NO Singapore instrument reference and the gate says so"
                          if a == 2015 else "FIRMS detections + Singapore NEA"),
            "trained_on": False,
        }
        if not ok:
            rank = (int(modelled.rank(ascending=False)[a]) if a in modelled.index else None)
            fails.append(f"{a}: modelled seasonal severity at percentile "
                         + (f"{pm:.2f}" if pm is not None else "n/a")
                         + f", below {config.GATE_ANCHOR_DECILE}"
                         + (f" (ranked {rank} of {len(modelled)} modelled seasons; "
                            f"observed it is {po:.2f})" if rank and po is not None else ""))

    # THE SHAPE OF THE FAILURE MATTERS, AND IT IS ARITHMETIC BEFORE IT IS MODELLING.
    # A percentile rank over N seasons can only take N distinct values, so a >= 0.90 threshold
    # admits ceil(N/10) of them — with a short modelled record that is exactly ONE season, and
    # two anchors cannot both occupy it however well the model ranks them.  The threshold is NOT
    # moved; the arithmetic is published beside it so the reader can tell "the model cannot rank
    # the crises" from "the record is not yet long enough for this test to be satisfiable".
    n_seasons = int(len(modelled))
    admits = max(1, int(round(n_seasons * (1 - config.GATE_ANCHOR_DECILE))))
    structural = admits < len(config.ANCHOR_YEARS)
    return {
        "gate": "G-J5", "hard": False, "pass": len(fails) == 0,
        "threshold": {"percentile": config.GATE_ANCHOR_DECILE,
                      "scored_on": "the model's own seasonal mean risk, never trained on these "
                                   "years"},
        "anchors": res,
        "seasons_in_record": n_seasons,
        "seasons_admitted_by_threshold": admits,
        "structurally_unsatisfiable": structural,
        "structural_note": (
            f"the modelled series is {n_seasons} seasons long — bounded by the Copernicus ERA5 "
            f"queue, not by the method — and a {config.GATE_ANCHOR_DECILE:.0%} percentile "
            f"threshold over {n_seasons} values admits {admits} season(s).  Two anchors cannot "
            f"both occupy one slot, so this gate cannot be satisfied at this record length "
            f"however well the model ranks them.  The threshold is not being moved; lengthening "
            f"the ERA5 record is what makes it answerable."
            if structural else None),
        "observed_reference": (
            "against the FULL 15-season FIRMS record the two anchors sit at percentile "
            f"{res.get('2015', {}).get('observed_percentile')} and "
            f"{res.get('2019', {}).get('observed_percentile')} — which is the ordering the model "
            "is being asked to reproduce"),
        "seasonal_series_modelled": {int(k): float(v) for k, v in modelled.items()},
        "seasonal_series_observed": {int(k): int(v) for k, v in observed.items()},
        "reason": "; ".join(fails) or None,
    }


def main() -> None:
    import pandas as pd
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    gates = {}
    for fn in (gate_j1, gate_j2, gate_j3, gate_j4, gate_j5):
        try:
            g = fn()
        except Exception as exc:                            # noqa: BLE001
            name = fn.__name__.replace("gate_j", "G-J")
            g = {"gate": name, "pass": None, "status": "ERROR",
                 "reason": f"{type(exc).__name__}: {exc}"}
            log(f"{name}: ERROR {type(exc).__name__} {exc}")
        gates[g["gate"]] = g
        flag = ("PASS" if g.get("pass") is True else
                "FAIL" if g.get("pass") is False else g.get("status") or "PENDING")
        log(f"{g['gate']}: {flag}" + (f" — {g['reason']}" if g.get("reason") else ""))

    hard = [g for g in gates.values() if g.get("hard")]
    stats = {
        "case": "J", "title": "Fire & Haze Early Warning",
        "generated": pd.Timestamp.utcnow().isoformat(),
        "gates": gates,
        "gates_passed": sum(1 for g in gates.values() if g.get("pass") is True),
        "gates_total": len(gates),
        "hard_gates_passed": sum(1 for g in hard if g.get("pass") is True),
        "hard_gates_total": len(hard),
        "policy": {"ads": "accepted (verified by live submission 2026-08-30)",
                   "ewds": "accepted (verified by live submission 2026-08-30)",
                   "earthdata_ges_disc": "NOT USED in this build; the S5P route was rejected on "
                                         "licence and volume grounds before it was needed"},
        "manifest": util.manifest_read(),
        "risk_meta": _json("risk_meta.json"),
        "panel_meta": _json("panel_meta.json"),
        "indices_meta": _json("indices_meta.json"),
        "transport_meta": _json("transport_meta.json"),
        "cams_meta": _json("cams_meta.json"),
        "ground_meta": _json("ground_meta.json"),
        "fires_audit": _json("fires_audit.json"),
    }
    config.STATS_JSON.write_text(json.dumps(_nan_safe(stats), indent=1, default=str))
    log(f"validate: {stats['gates_passed']}/{stats['gates_total']} gates pass "
        f"({stats['hard_gates_passed']}/{stats['hard_gates_total']} hard) -> stats.json")


if __name__ == "__main__":
    main()
