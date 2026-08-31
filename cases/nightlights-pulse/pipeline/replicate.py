"""Three tests the review article asked for. Writes web/src/data/replication.json.

A · THE DECISIVE TEST. Gibson, Olivia, Boe-Gibson & Li (2021) found night lights
    essentially uninformative about rural Indonesian GDP (elasticity 0.086, R² 0.01)
    using VIIRS annual composites for 2015-16. This case finds ~0.68 / ~0.69 on
    Black Marble for 2018-2025. Two explanations: the sensor product, or a decade of
    rural electrification. Running the same specification on Black Marble ANNUAL
    composites for every year 2012-2025 separates them. If rural fit is already high
    in 2015-16 — the years Gibson used — the cause is the product. If it climbs
    through the decade, the cause is electrification.

B · THE SCALE HORSE-RACE. log(total PDRB) on log(sum of lights) puts an extensive
    quantity on each side, so some of the fit is arithmetic. Report what lights add
    over log population and log area.

C · DENSITY FORM. The same relationship per km², which removes area scale entirely.

Reads only annual rasters already on disk; never touches the monthly ledger.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import config

YEARS = list(range(2012, 2026))
POP_VAR, POP_TOTAL = 2790, 23          # Jumlah Penduduk menurut Kab/Kota — turvar 23 = TOTAL
OUT = config.CASE_DIR / "web" / "src" / "data" / "replication.json"
CACHE = config.DATA_DIR / "derived" / "annual_sol.parquet"


def product_for(year: int) -> str:
    """Suomi-NPP through 2017, NOAA-20 from 2018 — the archive's own split."""
    return "VNP46A4" if year < 2018 else "VJ146A4"


def annual_sol(refresh: bool = False) -> pd.DataFrame:
    """Sum of lights per regency per year, from the annual composites."""
    if CACHE.exists() and not refresh:
        return pd.read_parquet(CACHE)

    import geopandas as gpd
    from exactextract import exact_extract
    import zonal

    gdf = gpd.read_file(config.BOUNDARIES)[[config.REGION_ID, config.REGION_NAME, "geometry"]]
    # equal-area for a defensible km²; EPSG:6933 is the standard global choice
    area_km2 = gdf.to_crs(6933).area / 1e6

    frames = []
    for y in YEARS:
        prod, month = product_for(y), f"{y}-01"
        raw = config.DATA_DIR / "raw" / "bm" / month / f"{prod}_radiance.tif"
        if not raw.exists():
            print(f"[rep] {y}: no {prod} radiance — skipped")
            continue
        masked, health = zonal.apply_masks(month, prod)
        res = exact_extract(str(masked), gdf, ["sum", "mean", "count"], output="pandas")
        frames.append(pd.DataFrame({
            "region_id": gdf[config.REGION_ID].values,
            "region_name": gdf[config.REGION_NAME].values,
            "area_km2": area_km2.values,
            "sol": res["sum"].values, "mean_rad": res["mean"].values,
            "n_px": res["count"].values, "year": y, "product": prod,
        }))
        print(f"[rep] {y} {prod}: masked {health['masked_share']:.1%}, "
              f"national SOL {res['sum'].sum():,.0f}")

    out = pd.concat(frames, ignore_index=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(CACHE, index=False)
    return out


def population() -> pd.DataFrame:
    """Total population per kabupaten/kota, from BPS var 2790."""
    import bps
    rows = []
    for th, year in sorted(bps.years(POP_VAR).items()):
        payload = bps.fetch_year(POP_VAR, th)
        content = payload.get("datacontent", {})
        for vv in payload.get("vervar", []):
            key = f"{vv['val']}{POP_VAR}{POP_TOTAL}{th}0"
            if key in content:
                rows.append({"bps_code": str(vv["val"]), "year": year,
                             "pop": float(content[key])})
    df = pd.DataFrame(rows)
    print(f"[rep] population: {len(df)} rows, {df['year'].nunique()} years, "
          f"{df['bps_code'].nunique()} codes")
    return df


def ols(X: np.ndarray, y: np.ndarray) -> dict:
    """OLS with intercept; returns coefficients and R²."""
    A = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    r2 = 1 - resid @ resid / ((y - y.mean()) @ (y - y.mean()))
    return {"coef": [float(b) for b in beta], "r2": float(r2), "n": int(len(y))}


def main() -> int:
    refresh = "--refresh" in sys.argv
    sol = annual_sol(refresh)

    xw = pd.read_csv(config.CROSSWALK.parent / "region_crosswalk.csv", dtype={"bps_code": str})
    xw = xw[xw["bps_code"].notna() & (xw["bps_code"] != "")][["shapeID", "bps_code"]]
    sol = sol.merge(xw, left_on="region_id", right_on="shapeID", how="inner")

    import bps
    pdrb = bps.build()
    annual = pdrb[pdrb["quarter"] == 0].dropna(subset=["xw_code", "pdrb"])
    annual = annual[["xw_code", "year", "pdrb"]].rename(columns={"xw_code": "bps_code"})
    annual["bps_code"] = annual["bps_code"].astype(str)

    d = sol.merge(annual, on=["bps_code", "year"], how="inner")
    d = d[(d["sol"] > 0) & (d["pdrb"] > 0) & (d["area_km2"] > 0)].copy()
    d["kota"] = d["region_name"].str.startswith("Kota")
    d["lL"], d["lP"] = np.log(d["sol"]), np.log(d["pdrb"])
    d["lA"] = np.log(d["area_km2"])
    print(f"[rep] merged panel: {len(d)} regency-years, {d['year'].nunique()} years")

    # ── A · levels by year and group, on one consistent product family ──────────
    testA = []
    for y, g in d.groupby("year"):
        row = {"year": int(y), "product": g["product"].iloc[0]}
        for name, sub in (("all", g), ("kota", g[g["kota"]]), ("kabupaten", g[~g["kota"]])):
            f = ols(sub[["lL"]].to_numpy(), sub["lP"].to_numpy())
            row[name] = {"slope": round(f["coef"][1], 4), "r2": round(f["r2"], 4), "n": f["n"]}
        testA.append(row)
        print(f"[rep] A {y} {row['product']}: all R² {row['all']['r2']:.3f} "
              f"β {row['all']['slope']:.3f} | kab R² {row['kabupaten']['r2']:.3f} "
              f"β {row['kabupaten']['slope']:.3f} | kota R² {row['kota']['r2']:.3f}")

    # ── B · what do lights add over population and area? ────────────────────────
    pop = population()
    dp = d.merge(pop, on=["bps_code", "year"], how="inner")
    dp = dp[dp["pop"] > 0].copy()
    dp["lN"] = np.log(dp["pop"])
    testB = []
    for y, g in dp.groupby("year"):
        y_ = g["lP"].to_numpy()
        m = {
            "lights": ols(g[["lL"]].to_numpy(), y_),
            "pop": ols(g[["lN"]].to_numpy(), y_),
            "pop_area": ols(g[["lN", "lA"]].to_numpy(), y_),
            "pop_area_lights": ols(g[["lN", "lA", "lL"]].to_numpy(), y_),
        }
        row = {"year": int(y), "n": int(len(g)),
               "r2": {k: round(v["r2"], 4) for k, v in m.items()},
               "beta_lights_full": round(m["pop_area_lights"]["coef"][3], 4),
               "increment": round(m["pop_area_lights"]["r2"] - m["pop_area"]["r2"], 4)}
        testB.append(row)
        print(f"[rep] B {y}: lights alone {row['r2']['lights']:.3f} · pop {row['r2']['pop']:.3f} "
              f"· pop+area {row['r2']['pop_area']:.3f} · +lights {row['r2']['pop_area_lights']:.3f} "
              f"(increment {row['increment']:+.4f}, β {row['beta_lights_full']:.3f})")

    # ── C · density form: per km², which removes area scale on both sides ───────
    testC = []
    for y, g in d.groupby("year"):
        dl = np.log(g["sol"] / g["area_km2"]).to_numpy()
        dp_ = np.log(g["pdrb"] / g["area_km2"]).to_numpy()
        row = {"year": int(y)}
        for name, mask in (("all", np.ones(len(g), bool)), ("kota", g["kota"].to_numpy()),
                           ("kabupaten", ~g["kota"].to_numpy())):
            f = ols(dl[mask].reshape(-1, 1), dp_[mask])
            row[name] = {"slope": round(f["coef"][1], 4), "r2": round(f["r2"], 4), "n": f["n"]}
        testC.append(row)
        print(f"[rep] C {y} density: all R² {row['all']['r2']:.3f} β {row['all']['slope']:.3f} "
              f"| kab R² {row['kabupaten']['r2']:.3f}")

    out = {
        "generated": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
        "years": [int(y) for y in sorted(d["year"].unique())],
        "sensorSplit": 2018,
        "gibson": {"years": [2015, 2016], "all": {"b": 0.179, "r2": 0.05},
                   "kabupaten": {"b": 0.086, "r2": 0.01}, "kota": {"b": 0.936, "r2": 0.68},
                   "source": "Gibson, Olivia, Boe-Gibson & Li (2021) JDE 149:102602, Table 1, VIIRS columns"},
        "testA": testA, "testB": testB, "testC": testC,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=1)
    print(f"[rep] -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
