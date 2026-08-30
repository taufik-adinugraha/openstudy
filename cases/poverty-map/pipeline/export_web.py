"""Stage 6 · export — view-models for web/public/data, each NaN-safe and under budget.

Every writer is independent and failure-tolerant: whatever exists at the time of the run is
exported, everything else is recorded in manifest.json as "pending" and the page renders its
designed pending state instead of an empty chart. That is what lets the whole dashboard be
built and reviewed while ingest is still streaming.

Geometry budget (spec F7: ADM3 ≤ 3 MB): GeoJSON at full COD-AB precision is ~50 MB for
7,069 kecamatan, so geometry ships quantised — every coordinate snapped to one shared
integer lattice, then delta-encoded. Snapping to a SHARED lattice is what keeps the map
sliver-free: two neighbours' copies of a boundary vertex are the same input point and
therefore land on the same lattice node. Points that become collinear are dropped with a
symmetric triangle-area test (the same triple is judged identically from either side of a
shared edge), and islands below a share of their unit's area are dropped. The lattice is
coarsened until the file fits, and the resolution actually achieved is written into the
payload so the page can state it.

Files
  manifest.json     what is ready, what is pending, and why
  adm2.geo.json     514 regencies, quantised
  adm3.geo.json     7,069 kecamatan, quantised
  ledger.json       BPS series + model prediction + SHAP top-5 per regency
  estimates.json    kecamatan estimates by year, intervals for the latest year
  stats.json        gates, skill, coverage, caveats (copy of data/stats.json)
  contrasts.json    kecamatan pairs inside one regency with non-overlapping intervals
"""

from __future__ import annotations

import json
import math
import shutil
import traceback
from datetime import date

import numpy as np
import pandas as pd

import config

OUT = config.WEB_DATA
ADM3_BUDGET = 2_950_000    # spec F7: ADM3 geometry <= 3 MB
ADM2_BUDGET = 1_500_000    # loaded on first paint, so held tighter than the ADM3 budget

# Feature families for the SHAP bar chart — plain words, the page shows these labels.
FAMILIES = {
    "roof_": "Roof size & shape", "bld_": "Building density", "lights": "Night lights",
    "pop": "Population", "log_pop": "Population", "lc_": "Land cover",
    "is_": "Urban / regional context", "area": "Area",
}
FAMILY_ORDER = ["Roof size & shape", "Building density", "Night lights", "Population",
                "Land cover", "Urban / regional context", "Area"]
# Plain-language reading of each feature for the regency drilldown.
PLAIN = {
    "roof_share_lt40": ("a high share of roofs under 40 m²", "few small roofs"),
    "roof_share_lt60": ("many roofs under 60 m²", "few small roofs"),
    "roof_share_gt300": ("few large roofs", "many large roofs"),
    "roof_p50_m2": ("a small median roof", "a large median roof"),
    "roof_p90_m2": ("small roofs even at the top end", "large roofs at the top end"),
    "roof_mean_m2": ("a small mean roof", "a large mean roof"),
    "roof_cv": ("uniform roof sizes", "very mixed roof sizes"),
    "roof_area_share": ("little of the land under roof", "much of the land under roof"),
    "bld_per_km2": ("sparse building cover", "dense building cover"),
    "bld_per_capita": ("few buildings per person", "many buildings per person"),
    "roof_m2_per_capita": ("little roof area per person", "generous roof area per person"),
    "lights_mean": ("dim nights", "bright nights"),
    "lights_per_capita": ("little light per person", "plenty of light per person"),
    "lights_per_km2": ("a dark landscape", "a lit landscape"),
    "log_lights_per_capita": ("little light per person", "plenty of light per person"),
    "lights_per_built_km2": ("dim built-up land", "brightly lit built-up land"),
    "lights_trend_5y": ("lights that have not grown", "fast-growing lights"),
    "pop_density": ("low population density", "high population density"),
    "log_pop_density": ("low population density", "high population density"),
    "pop_growth_5y": ("slow population growth", "fast population growth"),
    "lc_crop": ("mostly cropland", "little cropland"),
    "lc_tree": ("heavy tree cover", "little tree cover"),
    "lc_built": ("little built-up land", "much built-up land"),
    "lc_water": ("much water", "little water"),
    "lc_mangrove": ("mangrove coast", "no mangrove"),
    "lc_shrub": ("shrubland", "no shrubland"),
    "lc_grass": ("grassland", "no grassland"),
    "lc_bare": ("bare ground", "no bare ground"),
    "lc_wetland": ("wetland", "no wetland"),
    "is_kota": ("a rural kabupaten", "an urban kota"),
    "is_java": ("outside Java", "on Java"),
}


def family_of(col: str) -> str:
    for pre, fam in FAMILIES.items():
        if col.startswith(pre):
            return fam
    return "Other"


def jnum(v, nd=3):
    """JSON-safe number: NaN/inf become null, everything else is rounded."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(f) else round(f, nd)


def write(name: str, payload) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    (OUT / name).write_text(txt)
    return len(txt)


# ------------------------------------------------------------------------ geometry codec
def _polyline(values) -> str:
    """Google-polyline varint encoding: zig-zag, 5 bits per character, offset 63.
    A one-lattice-step delta costs a single character, against ~4 for "12," in a JSON
    number array — which is what buys the extra map resolution inside the 3 MB budget."""
    out = []
    for v in values:
        v = int(v)
        v = ~(v << 1) if v < 0 else (v << 1)
        while v >= 0x20:
            out.append(chr((0x20 | (v & 0x1F)) + 63))
            v >>= 5
        out.append(chr(v + 63))
    return "".join(out)


def _encode(gdf, ids, names, parents, budget: int, note: str) -> dict:
    """Quantise → prune → varint-encode, coarsening the lattice until it fits `budget`."""
    bounds = gdf.total_bounds
    bbox = [float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3])]
    span = max(bbox[2] - bbox[0], bbox[3] - bbox[1])
    payload = None
    tried = []
    for q in (1 << 16, 1 << 15, 1 << 14, 1 << 13, 1 << 12, 1 << 11):
        step = span / q
        shapes = [_encode_one(geom, bbox, step) for geom in gdf.geometry]
        payload = {"codec": "polyline5-63", "bbox": [round(v, 6) for v in bbox], "step": step,
                   "res_m": round(step * 111_320, 1), "note": note,
                   "ids": ids, "names": names, "parents": parents, "shapes": shapes}
        size = len(json.dumps(payload, separators=(",", ":")))
        tried.append((round(step * 111_320), size))
        if size <= budget:
            payload["bytes"] = size
            payload["ladder"] = tried
            return payload
    payload["bytes"] = len(json.dumps(payload, separators=(",", ":")))
    payload["ladder"] = tried
    return payload


def _encode_one(geom, bbox, step) -> list:
    """One unit → list of delta-encoded rings on the shared lattice."""
    if geom is None or geom.is_empty:
        return []
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    areas = [p.area for p in polys]
    biggest = max(areas) if areas else 0.0
    rings = []
    for poly, a in zip(polys, areas):
        # islands under 1.5 % of the unit's largest part cost bytes and read as noise
        if biggest > 0 and a < 0.015 * biggest and len(polys) > 1:
            continue
        for ring in [poly.exterior] + list(poly.interiors):
            enc = _encode_ring(ring, bbox, step)
            if enc:
                rings.append(enc)
    return rings


def _encode_ring(ring, bbox, step) -> list | None:
    xs, ys = ring.coords.xy
    ix = np.round((np.asarray(xs) - bbox[0]) / step).astype("int64")
    iy = np.round((np.asarray(ys) - bbox[1]) / step).astype("int64")
    keep = np.ones(len(ix), bool)
    keep[1:] = (ix[1:] != ix[:-1]) | (iy[1:] != iy[:-1])
    ix, iy = ix[keep], iy[keep]
    if len(ix) > 3 and ix[0] == ix[-1] and iy[0] == iy[-1]:
        ix, iy = ix[:-1], iy[:-1]
    if len(ix) < 3:
        return None
    # Symmetric collinearity drop: twice the triangle area of (prev, this, next) in lattice
    # units. The criterion depends only on the three points, so a shared edge is judged
    # identically from either side and the border stays welded — no slivers. Repeated until
    # it stops paying, which is safe because both sides start each pass from the same points.
    for _ in range(4):
        if len(ix) <= 8:
            break
        px, py = np.roll(ix, 1), np.roll(iy, 1)
        nx, ny = np.roll(ix, -1), np.roll(iy, -1)
        cross = np.abs((ix - px) * (ny - py) - (iy - py) * (nx - px))
        drop = cross <= 1
        if drop.sum() == 0 or (len(ix) - drop.sum()) < 8:
            break
        ix, iy = ix[~drop], iy[~drop]
    vals = [int(ix[0]), int(iy[0])]
    vals += np.stack([np.diff(ix), np.diff(iy)], 1).ravel().astype(int).tolist()
    return _polyline(vals)


def export_geometry(manifest: dict) -> None:
    import geopandas as gpd

    import features as F

    m, _, _ = F.recode()
    names = {}
    if (config.DATA_DIR / "bps_poverty.parquet").exists():
        b = pd.read_parquet(config.DATA_DIR / "bps_poverty.parquet")
        b = b[b["year"] == b["year"].max()]
        names = dict(zip(b["bps_code"], b["bps_name"]))

    g2 = gpd.read_parquet(config.BOUNDARIES_ADM2)[["bps_code", "name", "geometry"]].copy()
    g2["code"] = g2["bps_code"].map(m)
    g2 = g2[g2["code"].notna()].dissolve(by="code").reset_index().sort_values("code")
    payload = _encode(g2, g2["code"].tolist(),
                      [names.get(c, n) for c, n in zip(g2["code"], g2["name"])],
                      [c[:2] for c in g2["code"]], ADM2_BUDGET,
                      "COD-AB 2020-04, BPS-derived, CC BY-IGO · reconciled to the current BPS codes")
    manifest["adm2_geo"] = {"status": "ready", "units": len(g2),
                            "bytes": write("adm2.geo.json", payload),
                            "res_m": payload["res_m"]}

    g3 = gpd.read_parquet(config.BOUNDARIES_ADM3)[["pcode", "name", "adm2_code", "geometry"]].copy()
    g3["code"] = g3["adm2_code"].map(m)
    g3 = g3.sort_values("pcode")
    payload = _encode(g3, g3["pcode"].tolist(), g3["name"].tolist(),
                      [c if isinstance(c, str) else "" for c in g3["code"]], ADM3_BUDGET,
                      "COD-AB 2020-04 kecamatan · CC BY-IGO")
    manifest["adm3_geo"] = {"status": "ready", "units": len(g3),
                            "bytes": write("adm3.geo.json", payload),
                            "res_m": payload["res_m"]}


# --------------------------------------------------------------------------- data payloads
def export_ledger(manifest: dict) -> None:
    bps = pd.read_parquet(config.DATA_DIR / "bps_poverty.parquet")
    years = sorted(bps["year"].unique().tolist())
    fm = json.loads((config.DATA_DIR / "features_meta.json").read_text()) \
        if (config.DATA_DIR / "features_meta.json").exists() else {}

    cv = pd.read_parquet(config.CV_PREDICTIONS) if config.CV_PREDICTIONS.exists() else None
    shap = pd.read_parquet(config.DATA_DIR / "shap_adm2.parquet") \
        if (config.DATA_DIR / "shap_adm2.parquet").exists() else None
    top = {}
    if shap is not None:
        cols = [c for c in shap.columns if c not in ("bps_code", "_base")]
        arr = shap[cols].to_numpy("float64")
        order = np.argsort(-np.abs(arr), axis=1)[:, :5]
        for i, code in enumerate(shap["bps_code"].values):
            top[code] = [[cols[j], jnum(arr[i, j], 3),
                          PLAIN.get(cols[j], (cols[j], cols[j]))[0 if arr[i, j] > 0 else 1]]
                         for j in order[i]]

    regs = {}
    for code, grp in bps.groupby("bps_code"):
        s = grp.set_index("year")
        row = {"n": str(grp["bps_name"].iloc[-1]), "p": str(code)[:2]}
        for key, col in (("p0", "p0_pct"), ("p1", "p1_gap"), ("p2", "p2_severity"),
                         ("line", "poverty_line_idr")):
            row[key] = [jnum(s[col].get(y), 3 if key != "line" else 0) for y in years]
        regs[code] = row
    if cv is not None:
        latest = int(cv["year"].max())
        cl = cv[cv["year"] == latest].set_index("bps_code")
        for code, row in regs.items():
            if code in cl.index:
                r = cl.loc[code]
                row["pred"] = jnum(r.get("pred_lopo"), 2)
                row["pop"] = jnum(r.get("pop"), 0)
                row["j"] = int(r.get("is_java") or 0)
                row["k"] = int(r.get("is_kota") or 0)
                row["lon"] = jnum(r.get("lon"), 4)
                row["lat"] = jnum(r.get("lat"), 4)
                row["adm3"] = int(r.get("n_adm3") or 0)
            if code in top:
                row["shap"] = top[code]

    scatter = None
    if cv is not None:
        latest = int(cv["year"].max())
        c = cv[(cv["year"] == latest)]
        scatter = {"year": latest, "rows": [
            [r.bps_code, jnum(getattr(r, config.TARGET), 2), jnum(r.pred_lopo, 2),
             jnum(r.pred_random, 2), jnum(r.pred_block, 2), jnum(r.pred_lopo_ridge, 2),
             int(r.is_java or 0), int(r.is_kota or 0)]
            for r in c.itertuples() if np.isfinite(getattr(r, config.TARGET))]}
        tem = cv[cv["year"].isin(config.TEMPORAL_HOLDOUT_YEARS)]
        scatter["temporal"] = [[int(r.year), jnum(getattr(r, config.TARGET), 2),
                                jnum(r.pred_temporal, 2), jnum(r.pred_temporal_strict, 2),
                                r.bps_code]
                               for r in tem.itertuples()
                               if r.pred_temporal is not None and np.isfinite(r.pred_temporal)]

    fam = None
    ms_path = config.DATA_DIR / "model_stats.json"
    if ms_path.exists():
        ms = json.loads(ms_path.read_text())
        acc: dict[str, float] = {}
        if shap is not None:
            cols = [c for c in shap.columns if c not in ("bps_code", "_base")]
            mean_abs = shap[cols].abs().mean()
            for c in cols:
                acc[family_of(c)] = acc.get(family_of(c), 0.0) + float(mean_abs[c])
            unit = "mean |SHAP| (pp of poverty rate)"
        else:
            for c, g in ms["gain_importance"].items():
                acc[family_of(c)] = acc.get(family_of(c), 0.0) + float(g)
            unit = "split gain (relative)"
        tot = sum(acc.values()) or 1.0
        fam = {"unit": unit, "rows": [[k, jnum(acc.get(k, 0.0), 4), jnum(acc.get(k, 0.0) / tot, 4)]
                                      for k in FAMILY_ORDER if k in acc]}
        if shap is not None:
            cols = [c for c in shap.columns if c not in ("bps_code", "_base")]
            ma = shap[cols].abs().mean().sort_values(ascending=False)
            fam["features"] = [[c, jnum(ma[c], 4), family_of(c),
                                PLAIN.get(c, (c, c))[0]] for c in ma.index[:14]]

    payload = {"years": years, "regencies": regs, "scatter": scatter, "families": fam,
               "provinces": fm.get("provinces", {}), "vintage": str(date.today())}
    manifest["ledger"] = {"status": "ready" if cv is not None else "partial",
                          "regencies": len(regs), "bytes": write("ledger.json", payload)}


def export_estimates(manifest: dict) -> None:
    est = pd.read_parquet(config.ESTIMATES_ADM3)
    years = sorted(int(y) for y in est["year"].unique())
    latest = years[-1]
    units = est[["pcode", "name", "bps_code", "prov_code"]].drop_duplicates("pcode") \
        .sort_values("pcode").reset_index(drop=True)
    idx = {p: i for i, p in enumerate(units["pcode"])}
    n = len(units)

    def column(df, col, nd=2):
        out = [None] * n
        for p, v in zip(df["pcode"], df[col]):
            i = idx.get(p)
            if i is not None:
                out[i] = jnum(v, nd)
        return out

    by_year = {}
    for y in years:
        s = est[est["year"] == y]
        by_year[str(y)] = column(s, "p0_est", 2)
    last = est[est["year"] == latest]
    payload = {
        "years": years, "latest": latest,
        "pcodes": units["pcode"].tolist(),
        "names": units["name"].tolist(),
        "parents": units["bps_code"].fillna("").tolist(),
        "est": by_year,
        "lo": column(last, "p0_lo", 2), "hi": column(last, "p0_hi", 2),
        "pop": column(last, "pop", 0),
        "area_km2": column(last, "area_km2", 1),
        "official": column(last, "official_p0", 2),
        "factor": column(last, "benchmark_factor", 3),
        # "what the satellite sees" — the hero's left-hand state, raw and unmodelled
        "sees": {
            "roof_share_lt40": column(last, "roof_share_lt40", 4),
            "lights_per_capita": column(last, "lights_per_capita", 4),
            "bld_per_km2": column(last, "bld_per_km2", 2),
            "pop_density": column(last, "pop_density", 1),
            "lc_built": column(last, "lc_built", 4),
        },
    }
    manifest["estimates"] = {"status": "ready", "units": n, "years": years,
                             "bytes": write("estimates.json", payload)}


def export_contrasts(manifest: dict) -> None:
    """Kecamatan pairs inside one regency whose intervals do not overlap — the sharpest
    honest statement the model can make below the survey line."""
    est = pd.read_parquet(config.ESTIMATES_ADM3)
    latest = int(est["year"].max())
    e = est[(est["year"] == latest) & np.isfinite(est["p0_est"]) &
            np.isfinite(est["p0_lo"]) & np.isfinite(est["p0_hi"]) &
            np.isfinite(est["pop"]) & (est["pop"] > 500)]
    rows, within = [], []
    for code, g in e.groupby("bps_code"):
        if len(g) < 2:
            continue
        g = g.sort_values("p0_est")
        lo_u, hi_u = g.iloc[-1], g.iloc[0]
        within.append({"code": code, "spread": float(g["p0_est"].max() - g["p0_est"].min()),
                       "official": jnum(g["official_p0"].iloc[0], 2), "n": int(len(g))})
        if lo_u["p0_lo"] > hi_u["p0_hi"]:
            rows.append({
                "code": code, "official": jnum(g["official_p0"].iloc[0], 2),
                "prov": str(g["prov_name"].iloc[0]),
                "high": {"name": str(lo_u["name"]), "pcode": str(lo_u["pcode"]),
                         "est": jnum(lo_u["p0_est"], 1), "lo": jnum(lo_u["p0_lo"], 1),
                         "hi": jnum(lo_u["p0_hi"], 1), "pop": jnum(lo_u["pop"], 0)},
                "low": {"name": str(hi_u["name"]), "pcode": str(hi_u["pcode"]),
                        "est": jnum(hi_u["p0_est"], 1), "lo": jnum(hi_u["p0_lo"], 1),
                        "hi": jnum(hi_u["p0_hi"], 1), "pop": jnum(hi_u["pop"], 0)},
                "gap": jnum(float(lo_u["p0_est"] - hi_u["p0_est"]), 1)})
    rows.sort(key=lambda r: -(r["gap"] or 0))
    spreads = np.array([w["spread"] for w in within], dtype="float64")
    payload = {
        "year": latest, "pairs": rows[:18],
        "n_separable": len(rows), "n_regencies": len(within),
        "spread_median": jnum(float(np.median(spreads)) if len(spreads) else None, 2),
        "spread_p90": jnum(float(np.percentile(spreads, 90)) if len(spreads) else None, 2),
        "spread_max": jnum(float(spreads.max()) if len(spreads) else None, 2),
        "widest": sorted(within, key=lambda w: -w["spread"])[:10],
    }
    for w in payload["widest"]:
        w["spread"] = jnum(w["spread"], 2)
    manifest["contrasts"] = {"status": "ready", "bytes": write("contrasts.json", payload)}


def export_stats(manifest: dict) -> None:
    stats = json.loads(config.STATS_JSON.read_text())
    manifest["stats"] = {"status": "ready", "ships": stats.get("ships"),
                         "bytes": write("stats.json", stats)}


def export_recon(manifest: dict) -> None:
    """The admin-code reconciliation, published in full — including anything unresolved."""
    import features as F

    _, audit, summary = F.recode()
    fm_path = config.DATA_DIR / "features_meta.json"
    fm = json.loads(fm_path.read_text()) if fm_path.exists() else {}
    payload = {**summary, "rows": audit.fillna("").to_dict("records"),
               "codab_adm2": 522, "codab_adm3": 7069,
               "adm2_units": fm.get("adm2_units"), "adm3_units": fm.get("adm3_units")}
    manifest["reconciliation"] = {"status": "ready", "bytes": write("reconciliation.json", payload),
                                  "matched": summary["matched"],
                                  "unresolved": len(summary["unresolved"])}


STEPS = [
    ("reconciliation", export_recon, [lambda: config.BOUNDARIES_ADM2,
                                      lambda: (config.DATA_DIR / "bps_poverty.parquet")]),
    ("geometry", export_geometry, [lambda: config.BOUNDARIES_ADM3,
                                   lambda: config.BOUNDARIES_ADM2]),
    ("ledger", export_ledger, [lambda: (config.DATA_DIR / "bps_poverty.parquet")]),
    ("estimates", export_estimates, [lambda: config.ESTIMATES_ADM3]),
    ("contrasts", export_contrasts, [lambda: config.ESTIMATES_ADM3]),
    ("stats", export_stats, [lambda: config.STATS_JSON]),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"generated": str(date.today()), "case": "poverty-map"}
    for name, fn, needs in STEPS:
        missing = [n().name for n in needs if not n().exists()]
        if missing:
            manifest[name] = {"status": "pending", "waiting_on": missing}
            print(f"[export] {name}: PENDING (waiting on {', '.join(missing)})", flush=True)
            continue
        try:
            fn(manifest)
            print(f"[export] {name}: ok", flush=True)
        except Exception as err:
            manifest[name] = {"status": "error", "error": str(err)[:300]}
            print(f"[export] {name} FAILED: {err}", flush=True)
            traceback.print_exc()
    write("manifest.json", manifest)
    total = sum(v.get("bytes", 0) for v in manifest.values() if isinstance(v, dict))
    print(f"[export] manifest -> {OUT}; {total/1e6:.2f} MB across "
          f"{sum(1 for v in manifest.values() if isinstance(v, dict) and v.get('status')=='ready')}"
          f" ready view-models", flush=True)
    brief = config.CASE_DIR / "web" / "src" / "content" / "brief.md"
    if brief.exists():
        shutil.copy(brief, OUT / "brief.md")


if __name__ == "__main__":
    main()
