"""Stage 7 · features — the cell-day panel, built so that it cannot cheat.

Every 0.25 deg land cell, every day of the record, with the weather, the drought, the fuel, the
ocean state and the cell's own fire history attached.  ~2,900 land cells x ~5,300 days is roughly
15 million rows, which is why this stage is DuckDB and not pandas: the window functions that
build the antecedent-dryness and fire-history features stream over parquet and spill to disk
instead of materialising a 2.4 GB frame inside a 3 GB memory cap.

THE FOUR RULES THIS STAGE ENFORCES, EACH OF WHICH IS A WAY A FIRE MODEL NORMALLY CHEATS

1.  EVERY PREDICTOR IS LAGGED, AND THE LAG IS ASSERTED.
    The label is "was there a detection in this cell on day t+L".  The issue day is t.  Every
    rolling window therefore ends at ``t - FEATURE_LAG_DAYS + 1`` inclusive, never at t+L, and
    ``assert_no_leakage`` re-derives one window from raw and compares.  A fire-history feature
    that accidentally includes day t+L is not a subtle bias — it is a model that reads the
    answer, scores 0.99 AUC, and is worthless.

2.  THE SEASON IS A CIRCLE, NOT A NUMBER.
    Day-of-year enters as sin/cos.  A tree splitting on "doy > 250" learns the September peak as
    a cliff and cannot represent that 31 December and 1 January are adjacent — which matters here
    because Riau's first burning season straddles February-March and Kalimantan's runs to
    November.

3.  TWO INFORMATION SETS, NOT TWO PRODUCTS.
    The spec asks for a reanalysis path and a forecast path scored separately.  The honest
    implementation is a difference in what the model is ALLOWED TO KNOW, not in which file the
    numbers came from:
      forecast path    features from days <= t only.  The model has to carry the weather forward
                       itself, which is what an operational system without a weather forecast
                       actually faces.
      reanalysis path  the same, plus the weather actually observed over t+1..t+L — a perfect
                       forecast.  It is an upper bound, not a product.
    The gap between them is the real cost of forecasting rather than hindcasting, and it is
    published.  ** This is a deliberate deviation from the spec's wording, which names
    CHIRPS-GEFS as the forecast driver. **  There is no open GEFS reforecast archive covering
    2012-2024, so training on today's forecast product is impossible; pretending otherwise would
    be precisely the train/serve skew the spec warns about elsewhere.  CHIRPS-GEFS is still
    ingested and drives the live refresh panel, labelled as what it is.

4.  THE ANCHORS ARE REMOVED HERE, NOT AT SCORING TIME.
    2015 and 2019 get an ``is_anchor`` flag and risk.py refuses to train on them.  Holding them
    out at scoring time instead is the kind of arrangement that survives one refactor and then
    quietly stops being true.

OUTPUT: ``data/panel/<year>.parquet`` (partitioned so no stage ever holds the whole panel) and
``data/panel_meta.json`` with the feature families SHAP is aggregated to.
"""

from __future__ import annotations

import json
from datetime import date

import config
import util
from util import log

PANEL_DIR = config.DATA_DIR / "panel"
META_OUT = config.DATA_DIR / "panel_meta.json"

# SHAP is aggregated to these families so chapter 02 can say *why* a cell is red in words a
# ministry can act on, rather than listing 40 column names.
FAMILIES = {
    "dryness": ["rain_1d", "rain_7d", "rain_30d", "rain_90d", "dry_days", "spi1", "spi3", "spi6"],
    "atmosphere": ["t2m_max", "rh_min", "vpd_max", "vpd_7d", "rh_min_7d", "blh_min", "blh_mean",
                   "ws10_mean", "ws10_max"],
    "soil": ["swvl1", "swvl2", "swvl3", "swvl1_30d"],
    "fuel": ["peat_m", "peat_frac", "lc_tree", "lc_crop", "lc_wetland", "lc_shrub", "lc_grass",
             "lc_built", "lc_water", "lc_mangrove", "lai_hv", "lai_lv"],
    "fire_history": ["fire_7d", "fire_30d", "fire_365d", "nbr_fire_7d", "nbr_fire_30d",
                     "days_since_fire", "frp_30d"],
    "season": ["doy_sin", "doy_cos"],
    "ocean": ["oni", "nino34_anom", "soi_30d", "dmi"],
    "foresight": ["fc_rain_lead", "fc_vpd_lead", "fc_rh_lead"],   # reanalysis path only
}
FORECAST_DROP = FAMILIES["foresight"]


def _con():
    import duckdb
    con = duckdb.connect()
    con.execute("PRAGMA threads=3")                 # shared 4-vCPU box; leave one core
    con.execute("PRAGMA memory_limit='2GB'")
    con.execute(f"PRAGMA temp_directory='{config.DATA_DIR / 'duckdb_tmp'}'")
    return con


SQL = """
CREATE OR REPLACE TEMP VIEW land AS
  SELECT cell, clat, clon, adm1_name, adm1_iso, country,
         COALESCE(peat_m, 0) AS peat_m, COALESCE(peat_frac, 0) AS peat_frac,
         {lc_select}
  FROM read_parquet('{static}') WHERE is_land;

-- The sl and tp parts are separate CDS jobs on the same grid, so this is a LEFT join and its
-- coverage is printed: an inner join would silently shorten the record to whichever product
-- happened to have drained further.
CREATE OR REPLACE TEMP VIEW wx AS
  SELECT w.cell, CAST(w.day AS DATE) AS day, w.t2m_max, w.rh_min, w.vpd_max,
         w.ws10_mean, w.ws10_max, w.u10_mean, w.v10_mean,
         w.blh_mean, w.blh_min, w.swvl1, w.swvl2, w.swvl3, w.lai_hv, w.lai_lv,
         {rain_expr}
  FROM read_parquet('{era5_sl}') w
  JOIN land l USING (cell)
  {tp_join};

CREATE OR REPLACE TEMP VIEW fd AS
  SELECT cell, CAST(day AS DATE) AS day, n_fire, frp_sum
  FROM read_parquet('{fires}');

-- the 8-neighbourhood, expressed on the cell key: neighbours are cell + di*100000 + dj
CREATE OR REPLACE TEMP VIEW nbr AS
  SELECT f.day, f.cell + o.di * 100000 + o.dj AS cell,
         SUM(f.n_fire) AS nbr_fire
  FROM fd f
  CROSS JOIN (SELECT * FROM (VALUES (-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1))
              AS t(di, dj)) o
  GROUP BY 1, 2;

CREATE OR REPLACE TEMP VIEW base AS
  SELECT w.*, COALESCE(f.n_fire, 0) AS n_fire, COALESCE(f.frp_sum, 0) AS frp_sum,
         COALESCE(n.nbr_fire, 0) AS nbr_fire
  FROM wx w
  LEFT JOIN fd  f ON f.cell = w.cell AND f.day = w.day
  LEFT JOIN nbr n ON n.cell = w.cell AND n.day = w.day;
"""

# Every rolling frame ends at ``{lag}`` rows PRECEDING, which is the whole leakage discipline in
# one number: with FEATURE_LAG_DAYS = 1 the window is [t-7, t-1] and day t itself is excluded
# from every antecedent feature, so a same-day detection can never leak into its own predictor.
ROLL_SQL = """
CREATE OR REPLACE TEMP VIEW rolled AS
SELECT *,
  SUM(rain_mm)  OVER w1  AS rain_1d,
  SUM(rain_mm)  OVER w7  AS rain_7d,
  SUM(rain_mm)  OVER w30 AS rain_30d,
  SUM(rain_mm)  OVER w90 AS rain_90d,
  AVG(vpd_max)  OVER w7  AS vpd_7d,
  AVG(rh_min)   OVER w7  AS rh_min_7d,
  AVG(swvl1)    OVER w30 AS swvl1_30d,
  SUM(n_fire)   OVER w7  AS fire_7d,
  SUM(n_fire)   OVER w30 AS fire_30d,
  SUM(n_fire)   OVER w365 AS fire_365d,
  SUM(frp_sum)  OVER w30 AS frp_30d,
  SUM(nbr_fire) OVER w7  AS nbr_fire_7d,
  SUM(nbr_fire) OVER w30 AS nbr_fire_30d,
  -- labels: a detection in this cell L days AFTER the issue day
  LEAD(n_fire, 1) OVER cw AS y1_n,
  LEAD(n_fire, 3) OVER cw AS y3_n,
  LEAD(n_fire, 7) OVER cw AS y7_n,
  -- reanalysis-path foresight: the weather actually observed over the lead window
  AVG(rain_mm) OVER f7 AS fc_rain_lead,
  AVG(vpd_max) OVER f7 AS fc_vpd_lead,
  AVG(rh_min)  OVER f7 AS fc_rh_lead
FROM base
WINDOW
  cw AS (PARTITION BY cell ORDER BY day),
  w1  AS (PARTITION BY cell ORDER BY day ROWS BETWEEN {lag} PRECEDING AND {lag} PRECEDING),
  w7  AS (PARTITION BY cell ORDER BY day ROWS BETWEEN {w7} PRECEDING AND {lag} PRECEDING),
  w30 AS (PARTITION BY cell ORDER BY day ROWS BETWEEN {w30} PRECEDING AND {lag} PRECEDING),
  w90 AS (PARTITION BY cell ORDER BY day ROWS BETWEEN {w90} PRECEDING AND {lag} PRECEDING),
  w365 AS (PARTITION BY cell ORDER BY day ROWS BETWEEN {w365} PRECEDING AND {lag} PRECEDING),
  f7  AS (PARTITION BY cell ORDER BY day ROWS BETWEEN 1 FOLLOWING AND 7 FOLLOWING);
"""


def build(con) -> None:
    import pandas as pd
    static_p = config.DATA_DIR / "cell_static.parquet"
    parts = config.DATA_DIR / "era5_parts"
    fires_p = config.DATA_DIR / "fires_daily.parquet"
    for p in (static_p, fires_p):
        util.require(p.exists(), f"missing input {p} — run its stage first")
    sl = sorted(parts.glob("sl_*.parquet"))
    tp = sorted(parts.glob("tp_*.parquet"))
    util.require(bool(sl), "no ERA5 single-level parts — run era5 first")
    if not tp:
        log("  WARNING: no precipitation parts yet — every rain feature will be NaN (never 0), "
            "so the model simply has no daily rainfall.  SPI-1/3/6 from 46 years of CHIRPS still "
            "carries the drought signal at monthly scale, but the daily dryness family is gone; "
            "rerun `make era5` then `make features risk` once tp has drained.")
    log(f"  era5 coverage: sl {[p.stem[3:] for p in sl]}")
    log(f"                 tp {[p.stem[3:] for p in tp]}")

    lc_cols = [c for c in pd.read_parquet(static_p, columns=None).columns if c.startswith("lc_")]
    lc_select = ", ".join(f"COALESCE({c}, 0) AS {c}" for c in lc_cols) or "0 AS lc_none"
    if tp:
        # NOT COALESCE(..., 0).  A precipitation year that has not drained yet is missing, and
        # zero is the single most dangerous value to substitute for it: it reads as "no rain",
        # which is the strongest possible fire signal.  NULL propagates instead, the rolling
        # sums ignore it, and write_parts masks the whole family to NaN — which LightGBM handles
        # natively as missing rather than as a drought.
        rain_expr = "r.rain_mm AS rain_mm, r.rain_mm IS NULL AS rain_missing"
        tp_join = (f"LEFT JOIN read_parquet('{parts / 'tp_*.parquet'}') r "
                   "ON r.cell = w.cell AND CAST(r.day AS DATE) = CAST(w.day AS DATE)")
    else:
        # a partial build: the panel still assembles so the DAG can be smoke-tested end to end,
        # but rain_mm is explicitly NULL rather than 0 so nothing downstream can mistake
        # "no data yet" for "no rain"
        rain_expr = "CAST(NULL AS DOUBLE) AS rain_mm, TRUE AS rain_missing"
        tp_join = ""
    con.execute(SQL.format(static=static_p, fires=fires_p, lc_select=lc_select,
                           era5_sl=str(parts / "sl_*.parquet"),
                           rain_expr=rain_expr, tp_join=tp_join))
    con.execute(ROLL_SQL.format(lag=config.FEATURE_LAG_DAYS,
                                w7=6 + config.FEATURE_LAG_DAYS,
                                w30=29 + config.FEATURE_LAG_DAYS,
                                w90=89 + config.FEATURE_LAG_DAYS,
                                w365=364 + config.FEATURE_LAG_DAYS))
    return lc_cols


def assert_no_leakage(con) -> None:
    """Re-derive one rolling window from raw and check it against the SQL frame.

    Cheap, and it is the only thing standing between this panel and the classic fire-model
    result: 0.99 AUC produced by a feature that contains the label.
    """
    q = """
    WITH probe AS (
      SELECT cell, day, fire_7d, n_fire FROM rolled
      WHERE fire_7d IS NOT NULL AND n_fire > 0
      ORDER BY random() LIMIT 200
    )
    SELECT p.cell, p.day, p.fire_7d, p.n_fire,
           (SELECT SUM(b.n_fire) FROM base b
             WHERE b.cell = p.cell
               AND b.day BETWEEN p.day - INTERVAL 7 DAY AND p.day - INTERVAL 1 DAY) AS recomputed
    FROM probe p
    """
    df = con.execute(q).fetchdf()
    if df.empty:
        log("  leakage probe: no rows to check (panel is empty?)")
        return
    bad = df[df["fire_7d"].fillna(-1) != df["recomputed"].fillna(-1)]
    util.require(bad.empty,
                 f"LEAKAGE/WINDOW MISMATCH on {len(bad)} of {len(df)} probes — the antecedent "
                 f"window does not equal the independently recomputed [t-7, t-1] sum")
    same_day = con.execute(
        "SELECT count(*) FROM rolled WHERE n_fire > 0 AND fire_7d IS NOT NULL "
        "AND fire_7d < 0").fetchone()[0]
    log(f"  leakage probe: {len(df)} windows re-derived from raw and matched exactly "
        f"(and {same_day} impossible negatives)")


def write_parts(con, lc_cols) -> dict:
    import numpy as np
    import pandas as pd
    PANEL_DIR.mkdir(parents=True, exist_ok=True)
    enso_p = config.DATA_DIR / "enso.parquet"
    spi_p = config.DATA_DIR / "spi.parquet"
    enso = pd.read_parquet(enso_p) if enso_p.exists() else pd.DataFrame(columns=["day"])
    spi = pd.read_parquet(spi_p) if spi_p.exists() else pd.DataFrame(columns=["cell", "month"])
    if len(enso):
        enso["day"] = pd.to_datetime(enso["day"]).dt.date
        enso["soi_30d"] = enso["soi"].rolling(30, min_periods=10).mean().astype("float32")
    if len(spi):
        spi["month"] = pd.to_datetime(spi["month"])

    yrs = [r[0] for r in con.execute(
        "SELECT DISTINCT year(day) AS y FROM rolled ORDER BY y").fetchall()]
    stats = {"rows": 0, "positives": {}, "years": []}
    static = pd.read_parquet(config.DATA_DIR / "cell_static.parquet")
    static = static[static["is_land"]]
    keep_static = ["cell", "clat", "clon", "adm1_name", "adm1_iso", "country",
                   "peat_m", "peat_frac"] + lc_cols

    for y in yrs:
        df = con.execute(f"SELECT * FROM rolled WHERE year(day) = {y}").fetchdf()
        if df.empty:
            continue
        df["day"] = pd.to_datetime(df["day"])
        df = df.merge(static[keep_static], on="cell", how="left", suffixes=("", "_s"))
        if len(enso):
            e = enso.copy()
            e["day"] = pd.to_datetime(e["day"])
            df = df.merge(e[["day", "oni", "nino34_anom", "soi_30d", "dmi"]],
                          on="day", how="left")
        if len(spi):
            df["month"] = df["day"].values.astype("datetime64[M]")
            df = df.merge(spi, on=["cell", "month"], how="left").drop(columns=["month"])
        doy = df["day"].dt.dayofyear.to_numpy()
        df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25).astype("float32")
        df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25).astype("float32")
        # consecutive dry days ending at t-lag, computed per cell on the sorted year
        df = df.sort_values(["cell", "day"])
        wet = (df["rain_1d"].fillna(0) >= config.WET_DAY_MM)
        grp = wet.groupby(df["cell"]).cumsum()
        df["dry_days"] = df.groupby(["cell", grp]).cumcount().astype("float32")
        # If precipitation has not landed for this year, EVERY rain feature is unknown — and
        # unknown must not be spelled "zero rain for 365 days", which is what a fillna(0) dry-day
        # counter would produce.  NaN is the honest value and the model treats it as missing.
        miss = df["rain_missing"].astype(bool) if "rain_missing" in df.columns else None
        if miss is not None and miss.any():
            for c in ("rain_1d", "rain_7d", "rain_30d", "rain_90d", "dry_days"):
                if c in df.columns:
                    df.loc[miss, c] = np.nan
            log(f"  panel {y}: no precipitation for this year — rain_* and dry_days written as "
                f"NaN on {int(miss.sum()):,} rows rather than as a drought")
        for L in config.LEAD_DAYS:
            df[f"y{L}"] = (df[f"y{L}_n"].fillna(0) > 0).astype("int8")
        df["is_anchor"] = df["day"].dt.year.isin(config.ANCHOR_YEARS)
        for c in df.columns:
            if df[c].dtype == np.float64:
                df[c] = df[c].astype("float32")
        drop = [c for c in df.columns if c.endswith("_n") or c.endswith("_s")]
        df = df.drop(columns=drop + ["rain_missing"], errors="ignore")
        df.to_parquet(PANEL_DIR / f"{y}.parquet", index=False, compression="zstd")
        stats["rows"] += len(df)
        stats["years"].append(int(y))
        for L in config.LEAD_DAYS:
            stats["positives"][f"y{L}"] = stats["positives"].get(f"y{L}", 0) + int(df[f"y{L}"].sum())
        log(f"  panel {y}: {len(df):,} rows, "
            + ", ".join(f"y{L} base rate {df[f'y{L}'].mean():.4f}" for L in config.LEAD_DAYS))
    return stats


def main() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = _con()
    log("features: building the cell-day panel in DuckDB (15 M rows does not fit in pandas here)")
    lc_cols = build(con)
    assert_no_leakage(con)
    stats = write_parts(con, lc_cols)
    util.require(stats["rows"] > 0, "panel is empty")
    base_rates = {k: v / max(stats["rows"], 1) for k, v in stats["positives"].items()}
    META_OUT.write_text(json.dumps({
        "rows": stats["rows"], "years": stats["years"],
        "positives": stats["positives"], "base_rates": base_rates,
        "families": FAMILIES,
        "forecast_path_drops": FORECAST_DROP,
        "feature_lag_days": config.FEATURE_LAG_DAYS,
        "anchor_years": list(config.ANCHOR_YEARS),
        "path_note": "The forecast path is defined by INFORMATION SET (features from days <= t "
                     "only), not by product: no open CHIRPS-GEFS reforecast archive covers "
                     "2012-2024, so a forecast-product-trained model is not buildable from open "
                     "data.  The reanalysis path adds the weather actually observed over the "
                     "lead window and is an upper bound, not an operational system.",
    }, indent=1))
    log(f"features: {stats['rows']:,} cell-days over {len(stats['years'])} years; "
        f"base rates {', '.join(f'{k} {v:.4f}' for k, v in base_rates.items())}")
    util.manifest_put("features", rows=stats["rows"], base_rates=base_rates)


if __name__ == "__main__":
    main()
