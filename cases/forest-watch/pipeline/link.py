"""Stage 3 - link.  The part GFW's public dashboard does not do: attach every alert cluster
to the commodity system around it.

Inputs: the per-tile cluster tables from alerts.py (which already carry the palm / peat /
primary pixel shares sampled in the clustering pass), the Universal Mill List points, and the
province polygons.

Linkage classes, mutually exclusive, evaluated in this order:
  PALM-INTERNAL   >= 50 % of the cluster's pixels fall inside mapped oil-palm plantation
                  (SDPT v2) - replanting or in-estate expansion, not a new frontier
  PALM-EDGE       any palm pixels, or a mapped estate within PALM_ADJACENT_KM - edge expansion
  MILL-CATCHMENT  outside palm but within MILL_RADIUS_KM of a Universal Mill List mill -
                  inside somebody's fresh-fruit-bunch sourcing radius
  UNLINKED        none of the above

Plus two compliance flags that turn an alert into an EUDR question: on peat (Miettinen et al.
via gfw_peatlands) and in primary forest 2001 (Turubanova / Margono).

Mill catchments are computed from the UML *points* with a KD-tree in a local azimuthal
projection, not from gfw_universal_mill_list_buffered_50_km: that pre-buffered raster returns
403 on a free key, and points additionally give the distance and the identity of the nearest
mill, which the pre-buffered mask cannot.

Scope: the 10-degree RADD tiles are not country-clipped, so they carry Malaysia,
Brunei, Papua New Guinea and Timor-Leste too.  Events that fall outside every
Indonesian province polygon (allowing a ~2 km coastal snap) are dropped here and the
count is published -- without the clip about a quarter of the "national" hectares
belong to somebody else's country.

Outputs: data/linked.parquet (cluster level), data/mills_scored.parquet,
         data/link_summary.json.
"""

from __future__ import annotations

import json
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

import config
from alerts import log

PALM_INTERNAL_SHARE = 0.5
COAST_TOL_DEG = 0.02          # ~2 km: snap coastal events to the nearest province
CLASSES = ("PALM-INTERNAL", "PALM-EDGE", "MILL-CATCHMENT", "UNLINKED")


def _xyz(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Unit-sphere cartesian, so a KD-tree gives true great-circle neighbours."""
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def chord(km: float) -> float:
    return 2.0 * np.sin(km / 6371.0088 / 2.0)


def main(argv: list[str]) -> None:
    files = sorted(f for f in config.ALERTS_DIR.glob("*.parquet")
                   if not f.name.startswith("raw_"))          # raw_*.parquet is the G-H2 grid
    frames = [pd.read_parquet(f) for f in files]
    frames = [f for f in frames if len(f)]
    if not frames:
        log("no cluster tables — run alerts first")
        raise SystemExit(1)
    df = pd.concat(frames, ignore_index=True)
    log(f"{len(df):,} clusters, {df.ha.sum():,.0f} ha")

    # --- province attribution, and the national clip ---------------------------------
    # The 10-degree RADD tiles cover whole squares, so they carry Peninsular Malaysia, Sarawak,
    # Sabah, Brunei, Papua New Guinea and Timor-Leste as well as Indonesia.  Without this clip
    # roughly a quarter of the "national" hectares are somebody else's country.  Points that miss
    # every province polygon by less than COAST_TOL_DEG are snapped to the nearest one (coastal
    # clearings against a generalised coastline); anything further out is dropped and counted.
    prov = gpd.read_parquet(config.BOUNDARIES)[["province", "geometry"]]
    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs=4326)
    j = gpd.sjoin(pts, prov, how="left", predicate="within").drop(columns=["index_right"])
    miss = j.province.isna()
    if miss.any():
        near = gpd.sjoin_nearest(pts.loc[miss.to_numpy()], prov, how="left",
                                 max_distance=COAST_TOL_DEG)
        near = near[~near.index.duplicated()]
        j.loc[miss.to_numpy(), "province"] = near.province.reindex(j.index[miss.to_numpy()])
    outside = j.province.isna()
    dropped_ha = float(j.loc[outside].ha.sum())
    log(f"clipped to Indonesia: dropped {int(outside.sum()):,} events / {dropped_ha:,.0f} ha "
        f"({dropped_ha / df.ha.sum():.1%}) falling in Malaysia, Brunei, PNG or Timor-Leste "
        f"— the 10-degree tiles are not country-clipped")
    df = pd.DataFrame(j.loc[~outside].drop(columns="geometry")).reset_index(drop=True)

    # --- mills ------------------------------------------------------------------------
    mills = pd.read_parquet(config.MILLS_PARQUET)
    tree = cKDTree(_xyz(mills.longitude.to_numpy(), mills.latitude.to_numpy()))
    cxyz = _xyz(df.lon.to_numpy(), df.lat.to_numpy())
    d, i = tree.query(cxyz, k=1)
    df["mill_dist_km"] = 2 * 6371.0088 * np.arcsin(np.clip(d, 0, 2) / 2)
    df["nearest_mill"] = mills.mill_name.to_numpy()[i]
    df["nearest_mill_group"] = mills.parent_com.to_numpy()[i]
    df["nearest_mill_id"] = mills.uml_id.to_numpy()[i]
    df["nearest_mill_rspo"] = mills.rspo_statu.to_numpy()[i]
    df["mills_within_50km"] = [len(x) for x in
                               tree.query_ball_point(cxyz, chord(config.MILL_RADIUS_KM))]

    # --- linkage class ------------------------------------------------------------------
    cls = np.full(len(df), "UNLINKED", dtype=object)
    in_catch = df.mill_dist_km.to_numpy() <= config.MILL_RADIUS_KM
    cls[in_catch] = "MILL-CATCHMENT"
    palm_share = df.palm_share.to_numpy()
    cls[palm_share > 0] = "PALM-EDGE"
    cls[palm_share >= PALM_INTERNAL_SHARE] = "PALM-INTERNAL"
    df["link_class"] = cls
    df["linked"] = df.link_class != "UNLINKED"
    df["on_peat"] = df.peat_share.to_numpy() >= 0.5
    df["in_primary"] = df.primary_share.to_numpy() >= 0.5
    df["week"] = pd.to_datetime(df.first_date).dt.to_period("W-SUN").dt.start_time
    df["quarter"] = pd.to_datetime(df.first_date).dt.to_period("Q").astype(str)
    df["year"] = pd.to_datetime(df.first_date).dt.year

    df.to_parquet(config.LINKED, index=False)
    log("linkage class shares by hectare:")
    sh = df.groupby("link_class").ha.sum()
    for k, v in (sh / sh.sum()).sort_values(ascending=False).items():
        log(f"    {k:<15} {v:6.1%}  ({sh[k]:>12,.0f} ha)")
    log(f"    on peat        {df.loc[df.on_peat].ha.sum()/df.ha.sum():6.1%}")
    log(f"    in primary     {df.loc[df.in_primary].ha.sum()/df.ha.sum():6.1%}")

    (config.DATA_DIR / "link_summary.json").write_text(json.dumps({
        "dropped_outside_indonesia_events": int(outside.sum()),
        "dropped_outside_indonesia_ha": round(dropped_ha, 1),
        "coast_snap_tolerance_deg": COAST_TOL_DEG,
    }, indent=1))

    # --- per-mill alert pressure --------------------------------------------------------
    latest = pd.to_datetime(df.last_date).max()
    recent = df.loc[pd.to_datetime(df.first_date) >= latest - pd.Timedelta(days=365)]
    rxyz = _xyz(recent.lon.to_numpy(), recent.lat.to_numpy())
    rows = []
    if len(recent):
        near = tree.query_ball_point(rxyz, chord(config.MILL_RADIUS_KM))
        mha = np.zeros(len(mills))
        mn = np.zeros(len(mills), dtype=int)
        mpeat = np.zeros(len(mills))
        ha = recent.ha.to_numpy()
        peat = recent.on_peat.to_numpy()
        for k, lst in enumerate(near):
            for m in lst:
                mha[m] += ha[k]
                mn[m] += 1
                mpeat[m] += ha[k] if peat[k] else 0.0
        rows = mills.assign(alert_ha_12m=mha, alert_clusters_12m=mn, peat_alert_ha_12m=mpeat)
    scored = pd.DataFrame(rows)
    scored.to_parquet(config.MILLS_SCORED, index=False)
    if len(scored):
        top = scored.nlargest(5, "alert_ha_12m")[["mill_name", "parent_com", "province",
                                                  "alert_ha_12m"]]
        log("top mills by alert pressure (12 m):\n" + top.to_string(index=False))
    log("link complete")


if __name__ == "__main__":
    main(sys.argv[1:])
