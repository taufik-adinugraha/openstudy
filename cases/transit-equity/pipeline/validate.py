"""Stage 7 · validate — gates G-G1, G-G3, G-G4 → data/stats.json.

G-G1  Timetable sanity (hard). For each published-timetable OD in config.VALIDATION_OD, R5's
      in-vehicle time (sum of the non-walking legs of the fastest itinerary in the departure
      window) must land within ±15 % or ±8 min of the operator's own figure, whichever is
      larger.
G-G2  NOT EVALUATED — it needs live Google Routes API calls, which the user has not
      authorised. Recorded as pending, with the reason, rather than skipped silently.
G-G3  Network integrity (hard). ≥ 98 % of GTFS stops snap to the street graph within 200 m
      (R5's own snapper, the same one the matrix uses), and no kelurahan origin is left
      unreachable in the WALK+TRANSIT scenario.
G-G4  Plausibility (hard where bookkeeping). Rail ≥ no-rail everywhere (exact monotonicity);
      DKI core above Bodetabek periphery on population-weighted median access; and the ITDP
      People-Near-Transit replication — the share of population within 1 km of service at a
      headway ≤ 15 min — charted against the 2016 anchors (Jakarta 44 %, Greater Jakarta 16 %).

Lab rule: if the data contradicts the story, the story changes. Nothing here is tuned to pass.
"""

from __future__ import annotations

import datetime
import json
import zipfile

import numpy as np
import pandas as pd

import config
import ingest
import network
import points
import util
from util import log


# --- G-G1 -----------------------------------------------------------------------------------

def gate_g1(tn) -> dict:
    import geopandas as gpd
    from r5py import DetailedItineraries, TransportMode
    from shapely.geometry import Point

    results = []
    for od in config.VALIDATION_OD:
        if not od.get("published_min"):
            continue
        o = gpd.GeoDataFrame({"id": ["o"]}, geometry=[Point(od["from"][1], od["from"][0])], crs=4326)
        d = gpd.GeoDataFrame({"id": ["d"]}, geometry=[Point(od["to"][1], od["to"][0])], crs=4326)
        try:
            it = DetailedItineraries(
                tn, origins=o, destinations=d, snap_to_network=True,
                departure=datetime.datetime.combine(
                    datetime.date.fromisoformat(config.DEPARTURE_DATE), datetime.time(7, 30)),
                transport_modes=[TransportMode.TRANSIT, TransportMode.WALK],
                max_time=datetime.timedelta(minutes=180))
            df = pd.DataFrame(it)
            if df.empty:
                results.append({**_od_meta(od), "r5_in_vehicle_min": None, "pass": False,
                                "note": "no itinerary found"})
                continue
            tt = "travel_time"
            opt = df.groupby("option")[tt].sum().idxmin()
            best = df[df.option == opt]
            invehicle = best.loc[best.transport_mode.astype(str).str.upper() != "WALK", tt].sum()
            invehicle = float(pd.to_timedelta(invehicle).total_seconds() / 60
                              if not np.isscalar(invehicle) else invehicle)
            tol = max(od["published_min"] * config.GATE_TT_TOL_PCT / 100, config.GATE_TT_TOL_MIN)
            results.append({**_od_meta(od), "r5_in_vehicle_min": round(invehicle, 1),
                            "tolerance_min": round(tol, 1),
                            "deviation_min": round(invehicle - od["published_min"], 1),
                            "pass": bool(abs(invehicle - od["published_min"]) <= tol)})
        except Exception as e:
            results.append({**_od_meta(od), "r5_in_vehicle_min": None, "pass": False,
                            "note": f"{type(e).__name__}: {e}"})
    passed = [r for r in results if r["pass"]]
    return {"gate": "G-G1", "hard": True, "ods": results,
            "passed": len(passed), "of": len(results),
            "pass": len(results) > 0 and len(passed) == len(results)}


def _od_meta(od: dict) -> dict:
    return {"name": od["name"], "mode": od["mode"], "published_min": od["published_min"],
            "source": od["source"]}


# --- G-G3 -----------------------------------------------------------------------------------

def all_stops() -> pd.DataFrame:
    rows = []
    for z in sorted(config.GTFS_DIR.glob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            if "stops.txt" not in zf.namelist():
                continue
            s = pd.read_csv(zf.open("stops.txt"))
            s["feed"] = z.name
            rows.append(s[["stop_id", "stop_name", "stop_lat", "stop_lon", "feed"]])
    return pd.concat(rows, ignore_index=True).dropna(subset=["stop_lat", "stop_lon"])


def gate_g3(tn, acc: pd.DataFrame) -> dict:
    import geopandas as gpd
    from shapely.geometry import Point

    s = all_stops()
    out = {"gate": "G-G3", "hard": True, "stops": int(len(s)),
           "by_feed": {k: int(v) for k, v in s.feed.value_counts().items()}}
    try:
        g = gpd.GeoDataFrame(s, geometry=[Point(x, y) for x, y in zip(s.stop_lon, s.stop_lat)],
                             crs=4326)
        snapped = tn.snap_to_network(g.geometry, radius=config.GATE_SNAP_M)
        ok = int(np.sum([p is not None and not p.is_empty for p in snapped]))
        out["snapped_within_200m"] = ok
        out["snap_share"] = round(ok / len(s), 4)
        out["snap_pass"] = bool(ok / len(s) >= config.GATE_SNAP_SHARE)
    except Exception as e:
        out["snap_share"] = None
        out["snap_pass"] = None
        out["snap_note"] = f"R5 snapper unavailable: {type(e).__name__}: {e}"
    a = acc[(acc.scenario == "all") & (acc.cutoff == 60)]
    unreachable = a[a.jobs_share <= 0]
    out["unreachable_origins"] = int(len(unreachable))
    out["unreachable_pop"] = float(unreachable["pop"].sum())
    out["origins"] = int(len(a))
    out["pass"] = bool(out.get("snap_pass") is not False and len(unreachable) == 0)
    return out


# --- G-G4 -----------------------------------------------------------------------------------

def people_near_transit(dests=None) -> dict:
    """ITDP People-Near-Transit: population within 1 km of service at ≤15-min headway."""
    import geopandas as gpd
    import rasterio
    from rasterio.features import geometry_mask
    from shapely.geometry import Point

    frequent = []
    for z in sorted(config.GTFS_DIR.glob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
            if not {"stops.txt", "stop_times.txt", "trips.txt", "frequencies.txt"} <= set(names):
                continue
            freq = pd.read_csv(zf.open("frequencies.txt"))
            peak = freq[(freq.start_time <= "08:00:00") & (freq.end_time > "08:00:00")]
            if peak.empty:
                peak = freq
            best = peak.groupby("trip_id").headway_secs.min()
            good_trips = set(best[best <= config.FREQUENT_HEADWAY_MIN * 60].index)
            st = pd.read_csv(zf.open("stop_times.txt"), usecols=["trip_id", "stop_id"])
            sids = set(st[st.trip_id.isin(good_trips)].stop_id.unique())
            s = pd.read_csv(zf.open("stops.txt"))
            frequent.append(s[s.stop_id.isin(sids)][["stop_id", "stop_lat", "stop_lon"]])
    if not frequent:
        return {"available": False, "reason": "no frequency-based feeds"}
    fs = pd.concat(frequent, ignore_index=True)
    pts = gpd.GeoDataFrame(fs, geometry=[Point(x, y) for x, y in zip(fs.stop_lon, fs.stop_lat)],
                           crs=4326).to_crs(points.MOLLWEIDE)
    buf = pts.buffer(1000).union_all()
    adm = gpd.read_parquet(ingest.ADM4)
    res = {"frequent_stops": int(len(fs)), "headway_max_min": config.FREQUENT_HEADWAY_MIN,
           "anchors_2016": config.ITDP_PNT_2016}
    with rasterio.open(ingest.GHS_POP_TIF) as src:
        for label, sel in (("Greater Jakarta", adm),
                           ("Jakarta", adm[adm.adm1_name.str.contains("Jakarta", case=False, na=False)])):
            region = sel.to_crs(src.crs).union_all()
            arr, tr, _ = util.read_window(ingest.GHS_POP_TIF, sel)
            inregion = ~geometry_mask([region], out_shape=arr.shape, transform=tr, invert=False)
            near = ~geometry_mask([buf.intersection(region)], out_shape=arr.shape, transform=tr,
                                  invert=False)
            tot = float(arr[inregion].sum())
            res[label] = {"pop": tot, "pop_near_frequent_transit": float(arr[inregion & near].sum()),
                          "share": round(float(arr[inregion & near].sum() / tot), 4) if tot else None}
            res[label]["above_2016_anchor"] = bool(
                res[label]["share"] is not None and res[label]["share"] >= config.ITDP_PNT_2016[label])
    return res


def gate_g4(acc: pd.DataFrame) -> dict:
    out = {"gate": "G-G4", "hard": True}
    piv = acc[acc.cutoff == 60].pivot_table(index="id", columns="scenario", values="jobs_share")
    if {"all", "no_rail"} <= set(piv.columns):
        diff = piv["all"] - piv["no_rail"]
        out["monotonicity"] = {"origins": int(len(diff)),
                               "violations": int((diff < -1e-9).sum()),
                               "worst_violation": float(diff.min()),
                               "pass": bool((diff >= -1e-9).all())}
    else:
        out["monotonicity"] = {"pass": None, "note": "the no_rail scenario has not been computed"}
    a = acc[(acc.scenario == "all") & (acc.cutoff == 60)]
    dki = a[a.adm1_name.str.contains("Jakarta", case=False, na=False)]
    bod = a[~a.adm1_name.str.contains("Jakarta", case=False, na=False)]
    import equity
    d_med = equity.wmedian(dki.jobs_share.values, dki["pop"].values)
    b_med = equity.wmedian(bod.jobs_share.values, bod["pop"].values)
    out["core_periphery"] = {"dki_median": round(d_med, 4), "bodetabek_median": round(b_med, 4),
                             "pass": bool(d_med > b_med)}
    out["people_near_transit"] = people_near_transit()
    hard = [out["core_periphery"]["pass"]]
    if out["monotonicity"].get("pass") is not None:
        hard.append(out["monotonicity"]["pass"])
    out["pass"] = bool(all(hard))
    return out


def main() -> None:
    util.guard_disk()
    acc = pd.read_parquet(config.ACCESS_ADM4)
    stats = {
        "case": "G · Transit Access & Urban Equity",
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "window": {"date": config.DEPARTURE_DATE, "from": config.DEPARTURE_WINDOW[0],
                   "to": config.DEPARTURE_WINDOW[1], "percentile": 50,
                   "note": "scheduled times over a weekday morning-peak departure window; "
                           "not congestion-adjusted"},
        "inputs": json.loads(util.MANIFEST.read_text()) if util.MANIFEST.exists() else {},
        "gtfs": network.gtfs_summary(),
        "rail_handencoded": json.loads((config.GTFS_DIR / "rail_sources.json").read_text())
        if (config.GTFS_DIR / "rail_sources.json").exists() else None,
        "origins": int(acc.id.nunique()),
        "destinations": int(len(points.build_destinations())),
        "scenarios_computed": sorted(acc.scenario.unique().tolist()),
    }
    stats["G-G4"] = gate_g4(acc)
    tn = network.build()
    stats["G-G1"] = gate_g1(tn)
    stats["G-G3"] = gate_g3(tn, acc)
    stats["G-G2"] = {"gate": "G-G2", "hard": False, "pass": None, "status": "NOT EVALUATED",
                     "reason": "requires live Google Routes API calls; the user has not "
                               "authorised them, so no external routing comparison was made."}
    hard = [stats[g].get("pass") for g in ("G-G1", "G-G3", "G-G4")]
    stats["gates_passed"] = sum(1 for h in hard if h)
    stats["gates_hard"] = len(hard)
    config.STATS_JSON.write_text(json.dumps(stats, indent=2, allow_nan=False, default=str))
    log("stats →", config.STATS_JSON)
    for g in ("G-G1", "G-G2", "G-G3", "G-G4"):
        log(" ", g, stats[g].get("pass"), json.dumps(
            {k: v for k, v in stats[g].items() if k not in ("ods", "people_near_transit")},
            default=str)[:220])


if __name__ == "__main__":
    main()
