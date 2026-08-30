"""Stage 2a — build the modelling table.

One row per (station, hour). Everything in it is knowable AT ISSUE TIME: the
lagged PM2.5 the station has already reported, the meteorology already
analysed, and the fires already detected. Nothing is read from the future.
That is the whole discipline of this file — a single accidental forward-fill
here would produce a beautiful, meaningless model.

The upwind-fire construction is the one genuinely case-specific feature:
hotspot counts are attributed to the 3-sector arc the wind is blowing FROM,
in three distance rings, summed over the trailing 24 and 72 hours. A fire
downwind of Jakarta is correctly worth nothing.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

import config

SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
RINGS = ["near", "mid", "far"]
OUT = config.DATA_DIR / "features.parquet"
LAGS = (1, 2, 3, 6, 12, 24, 48)


def log(msg: str) -> None:
    print(f"[features] {msg}", flush=True)


def load_stations() -> pd.DataFrame:
    st = json.loads((config.DATA_DIR / "stations.json").read_text())["stations"]
    return pd.DataFrame(st)[["location_id", "name", "lat", "lon", "provider", "status"]]


# ── meteorology ──────────────────────────────────────────────────────────
def station_meteo(era5: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
    """Nearest ERA5 cell per station. The box is 5 x 4 cells over ~110 km, so
    'nearest cell' is an honest ~14 km representation, not an interpolation
    dressed up as detail."""
    cells = era5[["latitude", "longitude"]].drop_duplicates().reset_index(drop=True)
    rows = []
    for st in stations.itertuples():
        d = (cells["latitude"] - st.lat) ** 2 + (cells["longitude"] - st.lon) ** 2
        c = cells.loc[d.idxmin()]
        rows.append({"location_id": st.location_id,
                     "latitude": c["latitude"], "longitude": c["longitude"]})
    link = pd.DataFrame(rows)
    m = era5.merge(link, on=["latitude", "longitude"], how="inner")
    return m.drop(columns=["latitude", "longitude"])


def derive_meteo(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["wind_speed"] = np.sqrt(df["u10"] ** 2 + df["v10"] ** 2)
    # Meteorological convention: the direction the wind blows FROM.
    df["wind_from_deg"] = (270.0 - np.degrees(np.arctan2(df["v10"], df["u10"]))) % 360.0
    df["wind_from_sector"] = [SECTORS[int(((b + 22.5) % 360) // 45)] for b in df["wind_from_deg"]]
    df["t2m_c"] = df["t2m"] - 273.15
    df["d2m_c"] = df["d2m"] - 273.15
    # August-Roche-Magnus RH from temperature and dewpoint.
    a, b = 17.625, 243.04
    df["rh"] = 100.0 * np.exp(a * df["d2m_c"] / (b + df["d2m_c"]) - a * df["t2m_c"] / (b + df["t2m_c"]))
    df["precip_mm"] = df["tp"] * 1000.0
    df["ssrd_wm2"] = df["ssrd"] / 3600.0
    df["blh"] = df["blh"].clip(lower=1.0)
    df["log_blh"] = np.log(df["blh"])
    # Ventilation index: how much air is available to dilute into, per hour.
    df["ventilation"] = df["wind_speed"] * df["blh"]
    return df.drop(columns=["u10", "v10", "t2m", "d2m", "tp", "ssrd"])


# ── fire ─────────────────────────────────────────────────────────────────
def upwind_fire(meteo: pd.DataFrame, fire: pd.DataFrame) -> pd.DataFrame:
    """Trailing fire activity in the arc the wind comes from."""
    if fire.empty:
        for r in RINGS:
            for w in (1, 3):
                meteo[f"fire_up_{r}_{w}d"] = 0.0
                meteo[f"frp_up_{r}_{w}d"] = 0.0
        meteo["fire_region_3d"] = 0.0
        return meteo

    fire = fire.copy()
    fire["date"] = pd.to_datetime(fire["acq_date"])
    metrics = ["n_fire", "frp_sum"]

    # date x sector matrix per (metric, ring), completed with real zeros so the
    # rolling windows do not silently treat "no fires" as "no observation".
    dates = pd.date_range(fire["date"].min(), fire["date"].max(), freq="D")
    mats: dict[tuple[str, str], pd.DataFrame] = {}
    for metric in metrics:
        for ring in RINGS:
            sub = fire[fire["ring"] == ring]
            mat = (sub.pivot_table(index="date", columns="sector", values=metric,
                                   aggfunc="sum", observed=True)
                   if len(sub) else pd.DataFrame(index=dates))
            mats[(metric, ring)] = mat.reindex(index=dates, columns=SECTORS).fillna(0.0)

    # A smoke plume is not a laser: credit the 3-sector arc around the bearing.
    def arc3(mat: pd.DataFrame) -> pd.DataFrame:
        out = {}
        for i, s in enumerate(SECTORS):
            trio = [SECTORS[(i - 1) % 8], s, SECTORS[(i + 1) % 8]]
            out[s] = mat[trio].sum(axis=1)
        return pd.DataFrame(out, index=mat.index)

    meteo = meteo.copy()
    meteo["date"] = meteo["ts_utc"].dt.floor("D").dt.tz_localize(None)
    # Detections from day D are only safely available from D+1 — a VIIRS
    # overpass at 13:30 local cannot inform that morning's 06:00 forecast.
    lookup_idx = pd.MultiIndex.from_arrays(
        [meteo["date"] - pd.Timedelta(days=1), meteo["wind_from_sector"]])

    for metric, prefix in (("n_fire", "fire_up"), ("frp_sum", "frp_up")):
        for ring in RINGS:
            a = arc3(mats[(metric, ring)])
            for wdays in (1, 3):
                rolled = a.rolling(wdays, min_periods=1).sum().stack()
                meteo[f"{prefix}_{ring}_{wdays}d"] = np.nan_to_num(
                    rolled.reindex(lookup_idx).to_numpy(), nan=0.0)

    region = sum(mats[("n_fire", r)].sum(axis=1) for r in RINGS)
    region3 = region.rolling(3, min_periods=1).sum()
    meteo["fire_region_3d"] = np.nan_to_num(
        region3.reindex(meteo["date"] - pd.Timedelta(days=1)).to_numpy(), nan=0.0)
    return meteo.drop(columns=["date"])


# ── assembly ─────────────────────────────────────────────────────────────
def build() -> pd.DataFrame:
    ground = pd.read_parquet(config.DATA_DIR / "ground_hourly.parquet")
    era5 = pd.read_parquet(config.DATA_DIR / "era5_hourly.parquet")
    fire_path = config.DATA_DIR / "fire_daily.parquet"
    fire = pd.read_parquet(fire_path) if fire_path.exists() else pd.DataFrame()
    stations = load_stations()

    ground["ts_utc"] = pd.to_datetime(ground["ts_utc"], utc=True).dt.floor("h")
    era5["ts_utc"] = pd.to_datetime(era5["ts_utc"], utc=True).dt.floor("h")
    ground = ground.groupby(["location_id", "ts_utc"], as_index=False)["pm25"].mean()

    meteo = derive_meteo(station_meteo(era5, stations[stations["location_id"].isin(ground["location_id"])]))
    meteo = upwind_fire(meteo, fire)
    log(f"meteo rows {len(meteo):,}  {meteo['ts_utc'].min()} -> {meteo['ts_utc'].max()}")

    frames = []
    for loc, g in ground.groupby("location_id"):
        m = meteo[meteo["location_id"] == loc]
        if m.empty:
            continue
        lo, hi = max(g["ts_utc"].min(), m["ts_utc"].min()), min(g["ts_utc"].max(), m["ts_utc"].max())
        if hi <= lo:
            continue
        idx = pd.date_range(lo, hi, freq="h", tz="UTC")
        d = pd.DataFrame({"ts_utc": idx, "location_id": loc})
        d = d.merge(g, on=["location_id", "ts_utc"], how="left")
        d = d.merge(m, on=["location_id", "ts_utc"], how="left")

        # Lags and rolling summaries of the target — issue-time knowledge only.
        for lag in LAGS:
            d[f"pm25_lag{lag}"] = d["pm25"].shift(lag)
        d["pm25_roll6"] = d["pm25"].rolling(6, min_periods=3).mean()
        d["pm25_roll24"] = d["pm25"].rolling(24, min_periods=12).mean()
        d["pm25_std24"] = d["pm25"].rolling(24, min_periods=12).std()
        d["pm25_d1"] = d["pm25"] - d["pm25_lag1"]
        d["pm25_d24"] = d["pm25"] - d["pm25_lag24"]
        for col in ("blh", "wind_speed", "precip_mm"):
            d[f"{col}_roll24"] = d[col].rolling(24, min_periods=12).mean()
        d["precip_24h"] = d["precip_mm"].rolling(24, min_periods=12).sum()

        # Targets.
        for h in config.HORIZONS:
            d[f"y_h{h}"] = d["pm25"].shift(-h)
        frames.append(d)

    df = pd.concat(frames, ignore_index=True)

    # Calendar in Jakarta local time (WIB, UTC+7) — the diurnal cycle is a
    # traffic-and-boundary-layer cycle, and it runs on local clocks.
    local = df["ts_utc"].dt.tz_convert("Asia/Jakarta")
    df["hour_local"] = local.dt.hour
    df["dow"] = local.dt.dayofweek
    df["doy"] = local.dt.dayofyear
    df["hour_sin"] = np.sin(2 * np.pi * df["hour_local"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour_local"] / 24)
    df["doy_sin"] = np.sin(2 * np.pi * df["doy"] / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * df["doy"] / 365.25)

    df["persistence"] = df["pm25"]      # the baseline every horizon must beat
    df = df.sort_values(["location_id", "ts_utc"]).reset_index(drop=True)
    df.to_parquet(OUT, index=False)

    cov = df.groupby("location_id")["pm25"].agg(["count", "mean"])
    log(f"wrote {OUT.name}: {len(df):,} station-hours, {df['location_id'].nunique()} stations")
    for loc, r in cov.iterrows():
        log(f"  {loc:>8}: {int(r['count']):>6} observed hours, mean PM2.5 {r['mean']:.1f} ug/m3")
    return df


if __name__ == "__main__":
    build()
