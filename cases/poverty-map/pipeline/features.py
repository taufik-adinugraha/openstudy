"""Stage 2 · features — one derivation, applied identically to ADM2 and ADM3.

`ingest.py` already reduced every raster/vector layer to EXTENSIVE per-kecamatan
accumulators (counts, sums, sums-of-squares, histograms, pixel counts). This stage does
three things and nothing else:

  1. reconcile()  COD-AB's 2020-vintage ADM2 codes -> the current BPS codes, by P-code
                  first and then by name for the post-2020 pemekaran (the Papua splits).
                  Ported from cases/nightlights-pulse/pipeline/bps.py::recode_map, with the
                  name normaliser fixed so "Kota Sorong" cannot collide with "Sorong".
  2. rollup()     sum the extensive accumulators from ADM3 up to the reconciled ADM2.
  3. derive()     turn extensive accumulators into intensive features. THE SAME FUNCTION
                  runs on both levels, so the model never sees a feature at serve time that
                  was built by a different code path than at train time (no train/serve skew).

Feature families (spec F3): buildings (density, footprint share, roof-size distribution
from the 17-bin histogram), lights (mean radiance, per-capita, per-built-km², 5-year
trend), population (density, growth), land cover (WorldCover class shares).
Not present in this pass — GHSL, OSM roads, Sentinel-2 — see README decision 7.

Outputs
  data/features_adm2.parquet   one row per (bps_code, year), target joined
  data/features_adm3.parquet   one row per (pcode, year), same columns, no target
  data/adm2_reconciliation.csv rewritten with the resolution of every unmatched code
"""

from __future__ import annotations

import json
import re
import sys
import warnings

import numpy as np
import pandas as pd

import config

RAW_ADM3 = config.DATA_DIR / "features_raw_adm3.parquet"
RECODE_CSV = config.DATA_DIR / "adm2_recode.csv"
RECON_CSV = config.DATA_DIR / "adm2_reconciliation.csv"
AREA_CSV = config.DATA_DIR / "adm3_area.parquet"

YEARS = tuple(range(2016, 2026))

# WorldCover v200 class codes -> readable feature suffix (ESA WorldCover 2021 legend).
WC_NAMES = {10: "tree", 20: "shrub", 30: "grass", 40: "crop", 50: "built", 60: "bare",
            70: "snow", 80: "water", 90: "wetland", 95: "mangrove", 100: "moss"}
# Upper edges of the roof-size histogram written by ingest.ob_reduce (bin j covers
# [ROOF_EDGES[j-1], ROOF_EDGES[j]); bin 0 is < 20 m², bin 16 is >= 5000 m²).
ROOF_EDGES = [20, 30, 40, 50, 60, 80, 100, 130, 170, 220, 300, 400, 600, 1000, 2000, 5000]

# BPS province codes, current vintage (38 provinces after the 2022 Papua splits).
# Needed for the leave-one-province-out folds and for every province label on the page.
PROVINCES = {
    "11": "Aceh", "12": "Sumatera Utara", "13": "Sumatera Barat", "14": "Riau",
    "15": "Jambi", "16": "Sumatera Selatan", "17": "Bengkulu", "18": "Lampung",
    "19": "Kep. Bangka Belitung", "21": "Kep. Riau", "31": "DKI Jakarta",
    "32": "Jawa Barat", "33": "Jawa Tengah", "34": "DI Yogyakarta", "35": "Jawa Timur",
    "36": "Banten", "51": "Bali", "52": "Nusa Tenggara Barat", "53": "Nusa Tenggara Timur",
    "61": "Kalimantan Barat", "62": "Kalimantan Tengah", "63": "Kalimantan Selatan",
    "64": "Kalimantan Timur", "65": "Kalimantan Utara", "71": "Sulawesi Utara",
    "72": "Sulawesi Tengah", "73": "Sulawesi Selatan", "74": "Sulawesi Tenggara",
    "75": "Gorontalo", "76": "Sulawesi Barat", "81": "Maluku", "82": "Maluku Utara",
    "91": "Papua Barat", "92": "Papua Barat Daya", "94": "Papua", "95": "Papua Selatan",
    "96": "Papua Tengah", "97": "Papua Pegunungan",
}

# COD-AB carries polygons that are not census units at all (lakes, a reservoir, a forest
# block). They have no BPS code by construction — recorded as such, never counted as a
# reconciliation failure and never silently dropped.
NON_CENSUS_RE = re.compile(r"^(danau|waduk|wadung|hutan|perairan|laut)\b", re.I)


# ------------------------------------------------------------------ admin reconciliation
def _norm(name: str) -> str:
    """Normalise a regency name for cross-vintage matching.

    Ported from the flagship, with the collision its version had fixed: there the "kota"
    prefix was stripped before the kota/kabupaten flag survived into the key, so
    "Kota Sorong" (9171) matched plain "Sorong" (9202). Here the flag is part of the key.
    """
    n = (name or "").lower().strip()
    kota = bool(re.match(r"^(kota|kab\. kota)\b(?!\s*baru)", n))   # "Kota Baru" IS a kabupaten
    n = re.sub(r"^(kab\.|kabupaten|kota adm\.|kota administrasi|kota)\s*", "", n)
    n = n.replace("kepulauan", "kep").replace("kep.", "kep")
    return ("kota:" if kota else "") + re.sub(r"[^a-z]", "", n)


def reconcile(geo: pd.DataFrame, bps: pd.DataFrame) -> tuple[dict[str, str], pd.DataFrame]:
    """COD-AB ADM2 code -> current BPS code. Returns (map, audit rows).

    geo: columns bps_code (COD-AB P-code digits), name.  bps: bps_code, bps_name.
    Stage 1 is the exact P-code join (488 of 522). Stage 2 matches the residue by
    normalised name, restricted to the two unmatched sets so it can never steal a code
    that already matched. Anything still unresolved is returned for publication.
    """
    geo = geo.drop_duplicates("bps_code")
    bps = bps.drop_duplicates("bps_code")
    g_names = dict(zip(geo["bps_code"], geo["name"]))
    b_names = dict(zip(bps["bps_code"], bps["bps_name"]))

    mapping = {c: c for c in g_names if c in b_names}
    only_geo = sorted(set(g_names) - set(b_names))
    only_bps = sorted(set(b_names) - set(g_names))

    # name index over the BPS-only side; duplicates are refused rather than guessed at
    idx: dict[str, list[str]] = {}
    for c in only_bps:
        idx.setdefault(_norm(b_names[c]), []).append(c)

    audit: list[dict] = []
    claimed: set[str] = set()
    for c in only_geo:
        nm = g_names[c]
        if NON_CENSUS_RE.match(nm.strip()):
            audit.append({"side": "codab_only", "codab_code": c, "codab_name": nm,
                          "bps_code": "", "bps_name": "", "resolution": "non-census polygon"})
            continue
        hits = [h for h in idx.get(_norm(nm), []) if h not in claimed]
        if len(hits) == 1:
            mapping[c] = hits[0]
            claimed.add(hits[0])
            audit.append({"side": "codab_only", "codab_code": c, "codab_name": nm,
                          "bps_code": hits[0], "bps_name": b_names[hits[0]],
                          "resolution": "recoded by name (pemekaran)"})
        else:
            audit.append({"side": "codab_only", "codab_code": c, "codab_name": nm,
                          "bps_code": "", "bps_name": "",
                          "resolution": "ambiguous" if hits else "UNMATCHED"})
    for c in only_bps:
        if c not in claimed:
            audit.append({"side": "bps_only", "codab_code": "", "codab_name": "",
                          "bps_code": c, "bps_name": b_names[c],
                          "resolution": "UNMATCHED — no COD-AB geometry"})
    return mapping, pd.DataFrame(audit)


def recode(force: bool = False) -> tuple[dict[str, str], pd.DataFrame, dict]:
    """Run (or reload) the reconciliation. Depends only on the boundaries and the BPS
    series, so the map and the reconciliation table can ship before the model exists."""
    import geopandas as gpd

    if RECODE_CSV.exists() and RECON_CSV.exists() and not force:
        mp = pd.read_csv(RECODE_CSV, dtype=str)
        audit = pd.read_csv(RECON_CSV, dtype=str).fillna("")
        return dict(zip(mp["codab_code"], mp["bps_code"])), audit, _recode_summary(audit, mp)

    bps = pd.read_parquet(config.DATA_DIR / "bps_poverty.parquet")
    latest = bps[bps["year"] == bps["year"].max()][["bps_code", "bps_name"]]
    geo2 = gpd.read_parquet(config.BOUNDARIES_ADM2)[["bps_code", "name"]]
    mapping, audit = reconcile(geo2, latest)
    audit.to_csv(RECON_CSV, index=False)
    mp = pd.DataFrame({"codab_code": list(mapping), "bps_code": list(mapping.values())})
    mp.to_csv(RECODE_CSV, index=False)
    return mapping, audit, _recode_summary(audit, mp)


def _recode_summary(audit: pd.DataFrame, mp: pd.DataFrame) -> dict:
    by_name = int((audit["resolution"] == "recoded by name (pemekaran)").sum())
    matched = int(mp["bps_code"].nunique())
    unresolved = audit[audit["resolution"].astype(str).str.startswith(("UNMATCHED", "ambiguous"))]
    return {"matched": matched, "by_pcode": matched - by_name, "by_name": by_name,
            "non_census": int((audit["resolution"] == "non-census polygon").sum()),
            "unresolved": unresolved.to_dict("records")}


# ------------------------------------------------------------------------------- geometry
def adm3_area() -> pd.DataFrame:
    """Equal-area (EPSG:6933) km² per kecamatan — the denominator of every density."""
    import geopandas as gpd

    if AREA_CSV.exists():
        return pd.read_parquet(AREA_CSV)
    gdf = gpd.read_parquet(config.BOUNDARIES_ADM3)[["pcode", "geometry"]]
    area = gdf.to_crs(6933).area / 1e6
    out = pd.DataFrame({"pcode": gdf["pcode"].values, "area_km2": area.values})
    out.to_parquet(AREA_CSV, index=False)
    return out


# ---------------------------------------------------------------------------- derivation
def _hist_quantile(hist: np.ndarray, q: float) -> np.ndarray:
    """Quantile of roof area from the 17-bin histogram, linearly interpolated inside the
    hit bin. Open top bin is reported at its lower edge (never invented)."""
    lo = np.array([0.0] + ROOF_EDGES)
    hi = np.array(ROOF_EDGES + [ROOF_EDGES[-1] * 1.5])
    tot = hist.sum(axis=1)
    cum = np.cumsum(hist, axis=1)
    target = q * tot
    out = np.full(len(hist), np.nan)
    ok = tot > 0
    if not ok.any():
        return out
    j = (cum[ok] < target[ok, None]).sum(axis=1).clip(0, hist.shape[1] - 1)
    rows = np.arange(len(hist))[ok]
    before = np.where(j > 0, cum[rows, np.maximum(j - 1, 0)], 0.0)
    within = hist[rows, j]
    frac = np.where(within > 0, (target[ok] - before) / np.maximum(within, 1e-9), 0.0)
    out[ok] = lo[j] + frac.clip(0, 1) * (hi[j] - lo[j])
    return out


def _safe(num, den):
    num = np.asarray(num, dtype="float64")
    den = np.asarray(den, dtype="float64")
    return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)


def derive(ext: pd.DataFrame, key: str) -> pd.DataFrame:
    """Extensive accumulators -> intensive features, long by year.

    `ext` must carry, per unit: area_km2, ob_count, ob_area_sum, ob_area_sq, ob_h00..h16,
    wc_<class>, and per year pop_<y>, lights_sol_<y>, lights_px_<y>. Identical for ADM2
    and ADM3 — this function is the single definition of a feature in this case.
    """
    n = len(ext)
    static = pd.DataFrame({key: ext[key].values})
    area = ext["area_km2"].to_numpy("float64")
    static["area_km2"] = area

    # ---- buildings: density, footprint share, roof-size distribution
    cnt = ext["ob_count"].to_numpy("float64")
    asum = ext["ob_area_sum"].to_numpy("float64")
    asq = ext["ob_area_sq"].to_numpy("float64")
    static["bld_count"] = cnt
    static["bld_per_km2"] = _safe(cnt, area)
    static["roof_area_share"] = _safe(asum, area * 1e6)          # dimensionless 0..1
    mean = _safe(asum, cnt)
    static["roof_mean_m2"] = mean
    var = _safe(asq, cnt) - mean ** 2
    static["roof_cv"] = _safe(np.sqrt(np.clip(var, 0, None)), mean)

    hcols = sorted(c for c in ext.columns if re.fullmatch(r"ob_h\d\d", c))
    if hcols:
        hist = ext[hcols].to_numpy("float64")
        tot = hist.sum(axis=1)
        # bins 0..2 are < 40 m²: the small-roof share is the strongest single welfare
        # proxy in the literature (Jean 2016, Chi 2022) — poor households, small roofs.
        static["roof_share_lt40"] = _safe(hist[:, :3].sum(axis=1), tot)
        static["roof_share_lt60"] = _safe(hist[:, :5].sum(axis=1), tot)
        static["roof_share_gt300"] = _safe(hist[:, 11:].sum(axis=1), tot)
        static["roof_p50_m2"] = _hist_quantile(hist, 0.50)
        static["roof_p90_m2"] = _hist_quantile(hist, 0.90)

    # ---- land cover: class shares of the classified pixels
    wcols = [c for c in ext.columns if re.fullmatch(r"wc_\d+", c)]
    if wcols:
        wc = ext[wcols].to_numpy("float64")
        wtot = wc.sum(axis=1)
        for c in wcols:
            code = int(c.split("_")[1])
            static[f"lc_{WC_NAMES.get(code, code)}"] = _safe(ext[c].to_numpy("float64"), wtot)
        static["lc_pixels"] = wtot

    # ---- annual layers
    rows = []
    built_share = static.get("lc_built", pd.Series(np.full(n, np.nan))).to_numpy("float64")
    for year in YEARS:
        pop = ext.get(f"pop_{year}", pd.Series(np.full(n, np.nan))).to_numpy("float64")
        sol = ext.get(f"lights_sol_{year}", pd.Series(np.full(n, np.nan))).to_numpy("float64")
        px = ext.get(f"lights_px_{year}", pd.Series(np.full(n, np.nan))).to_numpy("float64")
        base_sol = ext.get(f"lights_sol_{year - 5}", pd.Series(np.full(n, np.nan))).to_numpy("float64")
        base_pop = ext.get(f"pop_{year - 5}", pd.Series(np.full(n, np.nan))).to_numpy("float64")
        d = pd.DataFrame({key: ext[key].values, "year": year})
        d["pop"] = pop
        d["pop_density"] = _safe(pop, area)
        d["log_pop_density"] = np.log1p(np.clip(d["pop_density"], 0, None))
        d["bld_per_capita"] = _safe(cnt, pop)
        d["roof_m2_per_capita"] = _safe(asum, pop)
        d["lights_mean"] = _safe(sol, px)
        d["lights_per_capita"] = _safe(sol, pop)
        d["lights_per_km2"] = _safe(sol, area)
        d["log_lights_per_capita"] = np.log1p(np.clip(d["lights_per_capita"], 0, None))
        d["lights_per_built_km2"] = _safe(sol, built_share * area)
        # 5-year log change: the only genuinely annual signal in the stack (lights + pop);
        # roofs and land cover are single-vintage, which the methodology states plainly.
        d["lights_trend_5y"] = np.log1p(np.clip(sol, 0, None)) - np.log1p(np.clip(base_sol, 0, None))
        d["pop_growth_5y"] = np.log1p(np.clip(pop, 0, None)) - np.log1p(np.clip(base_pop, 0, None))
        rows.append(d)
    annual = pd.concat(rows, ignore_index=True)
    return annual.merge(static, on=key, how="left")


# --------------------------------------------------------------------------------- rollup
EXT_PREFIX = ("ob_", "wc_", "pop_", "lights_sol_", "lights_px_")


def _extensive_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns
            if any(c.startswith(p) for p in EXT_PREFIX) or c == "area_km2"]


def build() -> tuple[pd.DataFrame, pd.DataFrame]:
    import geopandas as gpd

    if not RAW_ADM3.exists():
        sys.exit(f"[features] {RAW_ADM3.name} missing — ingest's merge stage has not run yet")
    raw = pd.read_parquet(RAW_ADM3)
    raw = raw.merge(adm3_area(), on="pcode", how="left")

    # ingest.merge keeps lights_mean (intensive); recover the pixel count so the rollup can
    # re-derive a correct area-weighted mean instead of averaging averages.
    # The Black Marble grid is identical every year, so a unit's pixel count is constant:
    # take it from any year that has light and reuse it. Doing this per year instead would
    # turn a genuinely DARK year into a missing value — and "dark" is the signal, not a gap.
    per_year = {}
    for year in range(2012, 2026):
        s, m = f"lights_sol_{year}", f"lights_mean_{year}"
        if s in raw.columns and m in raw.columns:
            mm = raw[m].to_numpy("float64")
            per_year[year] = np.where(mm > 0, raw[s].to_numpy("float64") / np.where(mm > 0, mm, 1.0),
                                      np.nan)
    if per_year:
        with warnings.catch_warnings():        # a unit dark in every year is expected here
            warnings.simplefilter("ignore", RuntimeWarning)
            px = np.nanmedian(np.vstack(list(per_year.values())), axis=0)
        px = np.where(np.isfinite(px) & (px > 0), np.round(px), np.nan)
        for year in per_year:
            raw[f"lights_px_{year}"] = px
        dark = int(np.isnan(px).sum())
        print(f"[features] lights: pixel count recovered for {len(px) - dark} ADM3 units; "
              f"{dark} have no lit pixel in any year and keep a missing radiance", flush=True)

    bps = pd.read_parquet(config.DATA_DIR / "bps_poverty.parquet")
    mapping, audit, summary = recode(force=True)
    n_named, n_noncensus = summary["by_name"], summary["non_census"]
    unresolved = pd.DataFrame(summary["unresolved"])

    raw["bps_code"] = raw["adm2_code"].map(mapping)
    matched_units = raw["bps_code"].notna()
    n_match = raw.loc[matched_units, "bps_code"].nunique()
    print(f"[features] ADM2 reconciliation: {n_match} BPS regencies matched "
          f"({n_match - n_named} by P-code + {n_named} recoded by name), "
          f"{n_noncensus} non-census COD-AB polygons (lakes/reservoir/forest), "
          f"{len(unresolved)} unresolved -> {RECON_CSV.name}", flush=True)
    if len(unresolved):
        print("[features] UNRESOLVED (published on the page, never dropped):\n"
              + unresolved.to_string(index=False), flush=True)

    raw["prov_code"] = raw["bps_code"].str[:2]
    raw["prov_name"] = raw["prov_code"].map(PROVINCES)
    raw["is_kota"] = (pd.to_numeric(raw["bps_code"].str[2:], errors="coerce") >= 71).astype(float)
    raw["is_java"] = raw["prov_code"].isin(config.JAVA_PROVINCE_CODES).astype(float)

    ext_cols = _extensive_cols(raw)
    adm3_ext = raw[["pcode"] + ext_cols].copy()
    f3 = derive(adm3_ext, "pcode")
    f3 = f3.merge(raw[["pcode", "name", "adm2_code", "bps_code", "prov_code", "prov_name",
                       "is_kota", "is_java"]], on="pcode", how="left")

    keep = raw[raw["bps_code"].notna()]
    adm2_ext = keep.groupby("bps_code", as_index=False)[ext_cols].sum(min_count=1)
    f2 = derive(adm2_ext, "bps_code")
    meta2 = keep.groupby("bps_code", as_index=False).agg(
        prov_code=("prov_code", "first"), prov_name=("prov_name", "first"),
        is_kota=("is_kota", "first"), is_java=("is_java", "first"),
        n_adm3=("pcode", "nunique"))
    f2 = f2.merge(meta2, on="bps_code", how="left")
    f2 = f2.merge(bps, on=["bps_code", "year"], how="left")

    f2.to_parquet(config.FEATURES_ADM2, index=False)
    f3.to_parquet(config.FEATURES_ADM3, index=False)
    (config.DATA_DIR / "features_meta.json").write_text(json.dumps({
        "adm2_rows": len(f2), "adm2_units": int(f2["bps_code"].nunique()),
        "adm3_rows": len(f3), "adm3_units": int(f3["pcode"].nunique()),
        "years": list(YEARS), "feature_cols": feature_columns(f2),
        "recon": summary,
        "provinces": PROVINCES,
    }, indent=1))
    print(f"[features] ADM2 {len(f2)} rows / {f2['bps_code'].nunique()} units, "
          f"ADM3 {len(f3)} rows / {f3['pcode'].nunique()} units, "
          f"{len(feature_columns(f2))} model features", flush=True)
    return f2, f3


DROP_FROM_MODEL = {
    "year", "pop", "area_km2", "bld_count", "lc_pixels", "lc_snow", "lc_moss",
    "p0_pct", "p1_gap", "p2_severity", "poverty_line_idr", "n_adm3",
}


def feature_columns(df: pd.DataFrame) -> list[str]:
    """Model inputs. Raw coordinates and unit identifiers are deliberately excluded: under
    leave-one-province-out they would let the model memorise geography rather than learn the
    signal, which is exactly what the spatial CV design is there to prevent."""
    out = []
    for c in df.columns:
        if c in DROP_FROM_MODEL or df[c].dtype.kind not in "fi":
            continue
        out.append(c)
    return out


def main() -> None:
    build()


if __name__ == "__main__":
    main()
