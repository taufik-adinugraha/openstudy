"""Stage 11 · export — view-models for the dashboard.

BUDGET RULE: first paint is under 3 MB, and everything the signature interaction needs is
PRECOMPUTED so it responds without a server.  "Blame the wind" has to feel instant, and an
instant back-trajectory fan is not a computation you do in the browser — it is a file you already
wrote.

TWO DESTINATIONS, AND THEY MEAN DIFFERENT THINGS
  ``web/src/data/summary.json``   imported at BUILD time: headline numbers, the gate table,
                                  vintages, licences.  Changing it needs a rebuild.
  ``web/public/data/*``           fetched at RUN time: grids, trajectories, series.  A pipeline
                                  rerun refreshes the page without rebuilding the site.

NaN DISCIPLINE.  ``json.dumps`` happily emits bare ``NaN``, which is not JSON and which
``JSON.parse`` rejects — the classic way a dashboard ships blank.  ``sanitise`` walks every
structure and turns non-finite floats into ``null``, and every writer here goes through it.

FILES
-----
summary.json            headline numbers, gate table, vintages, licences  (build-time)
grid.json               the model grid: one [lat, lon] per land cell, written once
days.json               which days have a risk surface and a trajectory set, plus severity
risk/<date>.json        risk surface for that day at each lead, byte-quantised, plus its fires
back/<rec>/<date>.json  the back-trajectory ensemble and its province attribution
receptors.json          per-receptor observed vs modelled series with the tier badge
skill.json              per-lead skill against climatology, persistence and the CEMS FWI
mask.json               the static exclusion mask, so the filter is inspectable
fires_year.json         detections and bulk file size per year — the ENSO story before any model
stats.json              the full gate table, verbatim
"""

from __future__ import annotations

import json
import math
import shutil
from datetime import date
from pathlib import Path

import config
import util
from util import log

WEB = config.WEB_DATA
SRC = config.WEB_SRC_DATA
HERO_EPISODES = 90          # (receptor, day) pairs the picker offers; each is one small file


def sanitise(o):
    """Every float that reaches JSON is finite or null.  No bare NaN ever leaves this module."""
    if isinstance(o, dict):
        return {str(k): sanitise(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [sanitise(v) for v in o]
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else round(o, 6)
    if hasattr(o, "item"):
        try:
            return sanitise(o.item())
        except Exception:                                   # noqa: BLE001
            return str(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()[:10]
    return o


def write(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(sanitise(obj), separators=(",", ":"), allow_nan=False)
    path.write_text(txt)
    return len(txt)


def _read(name):
    import pandas as pd
    p = config.DATA_DIR / name
    return pd.read_parquet(p) if p.exists() else None


def _json(name):
    p = config.DATA_DIR / name
    return json.loads(p.read_text()) if p.exists() else None


def main() -> None:
    import numpy as np
    import pandas as pd
    WEB.mkdir(parents=True, exist_ok=True)
    SRC.mkdir(parents=True, exist_ok=True)
    stats = _json("stats.json") or {}
    total = 0

    # ── the grid, written once ────────────────────────────────────────────────────────
    static = _read("cell_static.parquet")
    util.require(static is not None, "cell_static.parquet missing — run static first")
    land = static[static["is_land"]].reset_index(drop=True)
    cell_index = {int(c): i for i, c in enumerate(land["cell"])}
    total += write(WEB / "grid.json", {
        "cells": [[float(r.clat), float(r.clon)] for r in land.itertuples()],
        "province": land["adm1_name"].fillna("").tolist(),
        "country": land["country"].fillna("").tolist(),
        "peat_m": land["peat_m"].fillna(0).round(2).tolist(),
        "lc": {c[3:]: land[c].fillna(0).round(3).tolist()
               for c in land.columns if c.startswith("lc_")},
        "deg": config.GRID_DEG,
        "bbox": list(config.AOI),
    })

    # ── provinces, simplified, for the 2-D map ────────────────────────────────────────
    adm_p = config.DATA_DIR / "adm1.parquet"
    if adm_p.exists():
        import geopandas as gpd
        adm = gpd.read_parquet(adm_p)
        adm["geometry"] = adm.geometry.simplify(0.02).buffer(0)
        gj = json.loads(adm.to_json())
        for f in gj["features"]:
            f["properties"] = {k: f["properties"].get(k)
                               for k in ("adm1_name", "adm1_iso", "country")}
        total += write(WEB / "adm1.geo.json", gj)

    # ── chapter 01: what is actually burning ──────────────────────────────────────────
    audit = _json("fires_audit.json") or {}
    mask = _read("static_mask.parquet")
    if mask is not None:
        core = mask[mask["is_core"]]
        total += write(WEB / "mask.json", {
            "cells": [[round(float(r.lat), 3), round(float(r.lon), 3)]
                      for r in core.itertuples()],
            "n_core": int(len(core)), "n_total": int(len(mask)),
            "sources": audit.get("mask_sources", {}),
            "cell_deg": 0.01,
        })
    total += write(WEB / "fires_year.json", {
        "detections": audit.get("detections_by_year", {}),
        "bulk_mb": audit.get("bulk_mb_by_year", {}),
        "removed_share": audit.get("removed_share"),
        "removed_composition": audit.get("removed_composition", {}),
        "nrt_removed_share": audit.get("nrt_removed_share"),
        "nrt_rows": audit.get("nrt_rows"),
        "type_present_share": audit.get("type_present_share"),
    })

    # ── chapters 02/03: the risk surface, one file per day ────────────────────────────
    rd = _read("risk_days.parquet")
    nat = _read("risk_national.parquet")
    days_meta = []
    if rd is not None and len(rd):
        rd["day"] = pd.to_datetime(rd["day"])
        rd["idx"] = rd["cell"].map(cell_index)
        rd = rd[rd["idx"].notna()]
        rd["idx"] = rd["idx"].astype(int)
        shutil.rmtree(WEB / "risk", ignore_errors=True)
        natl = nat[nat["lead"] == config.LEAD_DAYS[0]].copy() if nat is not None else None
        if natl is not None:
            natl["day"] = pd.to_datetime(natl["day"])
            sev = dict(zip(natl["day"], natl["fires"]))
        else:
            sev = {}
        for day, g in rd.groupby("day"):
            obj = {"day": day.date().isoformat(), "leads": {}}
            for lead, gl in g.groupby("lead"):
                arr = np.zeros(len(land), dtype=np.uint8)
                # byte-quantised: 0-255 over [0, 1].  A probability surface drawn on a screen
                # cannot show more than 256 levels anyway, and this is a fifth of the bytes.
                arr[gl["idx"].to_numpy()] = np.clip(
                    np.rint(gl["p"].to_numpy() * 255), 0, 255).astype(np.uint8)
                obj["leads"][str(int(lead))] = arr.tolist()
            f = g[g["lead"] == config.LEAD_DAYS[0]]
            f = f[f["n_fire"] > 0]
            obj["fires"] = [[int(i), int(n)] for i, n in
                            zip(f["idx"].to_numpy(), f["n_fire"].to_numpy())]
            total += write(WEB / "risk" / f"{day.date().isoformat()}.json", obj)
            days_meta.append({"day": day.date().isoformat(),
                              "fires": int(sev.get(day, 0)),
                              "year": int(day.year),
                              "anchor": int(day.year) in config.ANCHOR_YEARS})
        log(f"  risk surfaces: {len(days_meta)} days")

    if nat is not None:
        n = nat.copy()
        n["day"] = pd.to_datetime(n["day"])
        series = {}
        for lead, g in n.groupby("lead"):
            g = g.sort_values("day")
            series[str(int(lead))] = {
                "day": [d.date().isoformat() for d in g["day"]],
                "risk_mean": g["risk_mean"].round(5).tolist(),
                "risk_max": g["risk_max"].round(4).tolist(),
                "cells_hi": g["cells_hi"].astype(int).tolist(),
                "fires": g["fires"].astype(int).tolist(),
            }
        total += write(WEB / "national.json", series)

    # ── the signature interaction: back-trajectories + attribution ────────────────────
    back = _read("back_traj.parquet")
    attr = _read("attribution.parquet")
    episodes = []
    if back is not None and len(back):
        back["day"] = pd.to_datetime(back["day"])
        if attr is not None:
            attr["day"] = pd.to_datetime(attr["day"])
        exp = _read("receptor_exposure.parquet")
        if exp is not None:
            exp["day"] = pd.to_datetime(exp["day"])
        # pick the episodes worth offering: the worst days per receptor, plus the anchors
        pairs = (back.groupby(["receptor", "day"]).size().reset_index(name="n"))
        if exp is not None:
            pairs = pairs.merge(exp[["receptor", "day", "exposure"]],
                                on=["receptor", "day"], how="left")
        else:
            pairs["exposure"] = 0.0
        pairs["anchor"] = pairs["day"].dt.year.isin(config.ANCHOR_YEARS)
        pick = pd.concat([
            pairs[pairs["anchor"]].sort_values("exposure", ascending=False)
                 .groupby("receptor").head(8),
            pairs.sort_values("exposure", ascending=False).groupby("receptor").head(8),
        ]).drop_duplicates(["receptor", "day"]).head(HERO_EPISODES)
        shutil.rmtree(WEB / "back", ignore_errors=True)
        # the fires that were burning UNDER the back-trajectories.  Without these the hero is a
        # fan of lines going nowhere; with them it is the sentence the case is built on.
        fdaily = _read("fires_daily.parquet")
        if fdaily is not None:
            fdaily["day"] = pd.to_datetime(fdaily["day"])
        for r in pick.itertuples():
            g = back[(back["receptor"] == r.receptor) & (back["day"] == r.day)]
            parcels = [[[round(float(a), 3), round(float(b), 3)] for a, b in zip(row.lat, row.lon)]
                       for row in g.itertuples()]
            a = {}
            if attr is not None:
                sub = attr[(attr["receptor"] == r.receptor) & (attr["day"] == r.day)]
                for row in sub.itertuples():
                    a[str(row.province)] = {
                        "share": float(row.share),
                        # how many of the ensemble's parcels actually crossed this province's
                        # fires — the difference between a confident share and a lucky one
                        "agreement": float(getattr(row, "agreement", float("nan"))),
                        "n_parcels": int(getattr(row, "n_parcels", 0) or 0)}
            fires_win = []
            if fdaily is not None:
                w = fdaily[(fdaily["day"] >= r.day - pd.Timedelta(days=3))
                           & (fdaily["day"] <= r.day)]
                w = (w.groupby(["clat", "clon"], as_index=False)
                      .agg(frp=("frp_sum", "sum"), n=("n_fire", "sum"))
                      .nlargest(700, "frp"))
                fires_win = [[round(float(x.clat), 3), round(float(x.clon), 3),
                              round(float(x.frp), 1), int(x.n)] for x in w.itertuples()]
            total += write(WEB / "back" / f"{r.receptor}" / f"{r.day.date().isoformat()}.json", {
                "receptor": r.receptor, "day": r.day.date().isoformat(),
                "parcels": parcels, "step_hours": 3,
                "attribution": a, "fires": fires_win,
                "fires_window_days": 3,
                "exposure": float(getattr(r, "exposure", 0) or 0),
            })
            episodes.append({"receptor": r.receptor, "day": r.day.date().isoformat(),
                             "exposure": float(getattr(r, "exposure", 0) or 0),
                             "anchor": bool(r.anchor),
                             "top_province": (max(a, key=lambda k: a[k]["share"])
                                              if a else None),
                             "top_share": (max(v["share"] for v in a.values())
                                           if a else None),
                             "top_agreement": (
                                 a[max(a, key=lambda k: a[k]["share"])]["agreement"]
                                 if a else None)})
        log(f"  back-trajectory episodes: {len(episodes)}")

    # ── the same engine, flipped: forward polylines for the episode days ──────────────
    fwd = _read("fwd_traj.parquet")
    fwd_days = []
    if fwd is not None and len(fwd):
        fwd["day"] = pd.to_datetime(fwd["day"])
        want = {pd.Timestamp(e["day"]) for e in episodes}
        shutil.rmtree(WEB / "fwd", ignore_errors=True)
        for day, g in fwd[fwd["day"].isin(want)].groupby("day"):
            total += write(WEB / "fwd" / f"{day.date().isoformat()}.json", {
                "day": day.date().isoformat(), "step_hours": 3,
                "parcels": [{"src": [round(float(r.src_lat), 3), round(float(r.src_lon), 3)],
                             "w": round(float(r.weight), 2),
                             "path": [[round(float(a), 3), round(float(b), 3)]
                                      for a, b in zip(r.lat, r.lon)]}
                            for r in g.itertuples()]})
            fwd_days.append(day.date().isoformat())
        log(f"  forward trajectory days: {len(fwd_days)}")

    total += write(WEB / "days.json", {"risk_days": days_meta, "episodes": episodes,
                                       "forward_days": fwd_days})

    # ── chapter 05: receptors, each with its tier ─────────────────────────────────────
    ground = _read("ground.parquet")
    exp = _read("receptor_exposure.parquet")
    gmeta = _json("ground_meta.json") or {}
    rec_out = {}
    for name, m in config.RECEPTORS.items():
        row = {"lat": m["lat"], "lon": m["lon"], "tier": m["tier"],
               "country": m["country"], "source": m["source"],
               "kind": "instrument" if m["tier"] in (1, 2) else "model (CAMS EAC4 reanalysis)",
               "coverage": m.get("coverage"), "note": m.get("note"),
               "meta": (gmeta.get("receptors") or {}).get(name, {})}
        if ground is not None:
            g = ground[ground["receptor"] == name].copy()
            if len(g):
                g["day"] = pd.to_datetime(g["day"])
                g = g.sort_values("day")
                row["observed"] = {"day": [d.date().isoformat() for d in g["day"]],
                                   "pm25": g["pm25"].round(1).tolist()}
        if exp is not None:
            e = exp[exp["receptor"] == name].copy()
            if len(e):
                e["day"] = pd.to_datetime(e["day"])
                e = e.sort_values("day")
                row["modelled"] = {"day": [d.date().isoformat() for d in e["day"]],
                                   "exposure": e["exposure"].round(3).tolist()}
        gj4 = (stats.get("gates", {}).get("G-J4", {}).get("per_receptor") or {}).get(name)
        row["rho"] = (gj4 or {}).get("rho")
        row["comparison"] = (gj4 or {}).get("comparison")
        rec_out[name] = row
    total += write(WEB / "receptors.json",
                   {"receptors": rec_out, "licences": gmeta.get("licences", {}),
                    "rejected": gmeta.get("rejected", {}),
                    "unmonitored_note": (
                        "OpenAQ has zero PM2.5 locations in Riau and zero in all of Kalimantan. "
                        "The cities this case is about have never had an open sensor, so tier 3 "
                        "substitutes the CAMS EAC4 reanalysis and calls it a model.")})

    # ── chapter 03: the skill table ───────────────────────────────────────────────────
    rmeta = _json("risk_meta.json") or {}
    total += write(WEB / "skill.json", {
        "leads": rmeta.get("leads", {}),
        "fwi": rmeta.get("fwi", {}),
        "folds": rmeta.get("n_folds"), "fold_caveat": rmeta.get("fold_caveat"),
        "era5_years": rmeta.get("era5_years"),
        "shap": rmeta.get("shap_families", {}),
        "importance": rmeta.get("importance", {}),
        "anchor_scores": rmeta.get("anchor_scores", {}),
        "folds": rmeta.get("folds", []),
        "neg_sample_rate": rmeta.get("neg_sample_rate"),
        "panel": _json("panel_meta.json") or {},
    })

    # ── the gate table, verbatim ──────────────────────────────────────────────────────
    total += write(WEB / "stats.json", stats)

    # ── build-time summary ────────────────────────────────────────────────────────────
    tmeta = _json("transport_meta.json") or {}
    imeta = _json("indices_meta.json") or {}
    cmeta = _json("cams_meta.json") or {}
    man = util.manifest_read()
    lead0 = str(config.LEAD_DAYS[0])
    fc = (rmeta.get("leads", {}).get(lead0, {}) or {}).get("forecast", {})
    summary = {
        "case": "J", "title": "Fire & Haze Early Warning",
        "generated": stats.get("generated"),
        "headline": {
            "detections_kept": audit.get("rows_kept"),
            "detections_removed": audit.get("rows_removed"),
            "removed_share": audit.get("removed_share"),
            "nrt_removed_share": audit.get("nrt_removed_share"),
            "auc_lead1": fc.get("auc"),
            "bss_climatology_lead1": fc.get("bss_vs_climatology"),
            "singapore_rho": stats.get("gates", {}).get("G-J4", {}).get("singapore_rho"),
            "bearing_share": tmeta.get("bearing_agreement_share"),
            "gfas_height_share": tmeta.get("gfas_height_share"),
            "cells": int(len(land)),
            "years": (_json("panel_meta.json") or {}).get("years", []),
        },
        "gates": {k: {"pass": v.get("pass"), "status": v.get("status"),
                      "reason": v.get("reason"), "hard": v.get("hard"),
                      "threshold": v.get("threshold")}
                  for k, v in (stats.get("gates") or {}).items()},
        "gates_passed": stats.get("gates_passed"), "gates_total": stats.get("gates_total"),
        "vintage": {
            "firms": audit.get("seam", {}),
            "era5": {"sl_years": man.get("era5", {}).get("sl_years"),
                     "tp_years": man.get("era5", {}).get("tp_years"),
                     "pl_years": man.get("era5", {}).get("pl_years"),
                     "lag_days": config.ERA5_LAG_DAYS},
            "chirps": imeta.get("chirps", {}),
            "fwi": imeta.get("fwi", {}),
            "cams": cmeta.get("coverage_notes", {}),
            "ground": {k: {"first": v.get("first"), "last": v.get("last"),
                           "tier": v.get("tier"), "status": v.get("status")}
                       for k, v in (gmeta.get("receptors") or {}).items()},
        },
        "attribution_granularity": {
            "level": "province (ADM1)",
            "decision": ("Province-level attribution is PUBLISHED, with the trajectory "
                         "uncertainty shown beside it.  Island level was the conservative "
                         "alternative and was rejected: 'Sumatra' is not an answer anyone can "
                         "act on, and the whole commercial premise of this case is that the "
                         "answer is actionable.  Every province share carries the ensemble "
                         "spread and an explicit 'no attributable source' outcome."),
        },
        "licences": {
            "FIRMS": "NASA — public domain / free and open",
            "ERA5 / CAMS / CEMS": "Copernicus CC BY 4.0",
            "CHIRPS": config.CHIRPS_LICENCE,
            "ESA WorldCover v200": config.WORLDCOVER_LICENCE,
            "PEATGRIDS": f"CC BY 4.0 — {config.PEATGRIDS['cite']}, {config.PEATGRIDS['doi']}",
            "Singapore NEA": config.NEA_LICENCE,
            "OpenAQ": "CC BY 4.0",
            "geoBoundaries": "CC BY 4.0",
            "OpenStreetMap (volcano nodes)": "ODbL",
            "CPC / NOAA PSL": "US Government public domain",
            "Long Paddock SOI": "CC BY 4.0 (State of Queensland)",
        },
        "excluded": {
            "AQICN / Malaysia APIMS": "forbids commercial use verbatim — Malaysia is a genuine "
                                      "hole, so the transboundary claim is Indonesia -> "
                                      "Singapore only",
            "Indonesia ISPU": "server-rendered, no API, no stated licence",
            "SPEIbase": "ODbL share-alike — viral-licence risk on a commercial deliverable",
            "DLR S5P UV Aerosol Index": "the collection's licence STRING says CC-BY-4.0 while the "
                                        "URL in that same field and the rel:license link both "
                                        "point at CC BY-NC 4.0 — held back pending written "
                                        "confirmation from DLR EOC",
            "KLHK land cover": "reachable and richer than WorldCover, but no open licence — "
                               "WorldCover is the primary and KLHK is not stored",
        },
        "budget_bytes": total,
    }
    write(SRC / "summary.json", summary)
    log(f"export: {total/1e6:.2f} MB across web/public/data + summary.json")
    util.manifest_put("export", bytes=total, risk_days=len(days_meta),
                      episodes=len(episodes))


if __name__ == "__main__":
    main()
