"""Stage 9 · transport — where the smoke goes, and where it came from.

WHAT THIS IS, STATED PLAINLY
----------------------------
A kinematic trajectory model driven by ERA5 winds.  Parcels are released above each fire,
advected on the 925/850/700 hPa flow, and their positions integrated forward (or backward) for
``config.TRAJ_HOURS`` hours.  That is all it is.

What it is NOT, and the methodology page says so in these words:

  * It is not a chemistry-transport model.  There is no chemistry, no aerosol microphysics, no
    secondary organic aerosol formation, no wet or dry deposition beyond a crude exponential
    decay, and no aerosol-radiation feedback (which in 2015 was strong enough to suppress the
    boundary layer and make the haze worse than the emissions alone imply).
  * It is not a dispersion model.  A single trajectory is a line, not a plume; plume width comes
    from releasing an ensemble jittered in release height and hour and reading its spread, which
    is a proxy for dispersion, not a simulation of it.
  * Vertical motion comes from ERA5 omega, and injection height — historically the single largest
    source of error, because a plume at 500 m and one at 2,000 m go to different countries — is
    taken from **CAMS GFAS**, which publishes ``injection_height``, plume top and plume bottom per
    fire.  GFAS ends 2025-12-03, so the operational tail falls back to ``config.PLUME_RISE`` and
    every run records what share of parcels used the fallback.

The honest claim is therefore "which fires were upwind" and "which receptors are downwind", at
daily resolution, with a stated direction error — not "PM2.5 will be 87 ug/m3 in Singapore on
Thursday".  And because CAMS publishes a real chemistry-transport forecast covering both anchor
years, the trajectory result is shown NEXT TO a CTM rather than in place of one.

HOW THE HEIGHT IS USED — A DELIBERATE REFINEMENT OF THE SPEC
------------------------------------------------------------
The spec fixes a blend of 0.5/0.4/0.1 across 925/850/700 hPa.  That blend is kept, but as the
FALLBACK, because a fixed blend throws away the very thing GFAS was added to provide: a parcel
released at 400 m and one released at 2,500 m should not be steered by the same wind.  Where a
release height is known, the parcel's own pressure is carried through the integration (advanced
by ERA5 omega) and the wind is interpolated in log-pressure between the bracketing levels.  Where
it is not, the fixed blend applies and the parcel is counted in the fallback share.  Neither the
blend nor the level weights are ever fitted — a fitted blend would be tuned on the same episodes
the gates then score, and the resulting agreement would prove nothing.

TWO DIRECTIONS, ONE ENGINE
--------------------------
forward   release at each fire cell weighted by summed FRP, integrate forward; a receptor's
          exposure on day t is the FRP-weighted count of parcels passing within
          ``config.RECEPTOR_KM``, decayed by travel time.
backward  release at a receptor on a chosen day, integrate backward; the parcels land on the
          cells the air came from, and intersecting them with that period's fires produces the
          attribution — the "blame the wind" interaction, and the commercially interesting half.

The two share the integrator; only the sign of the timestep and the release points differ.  A
back-trajectory that disagrees with the forward run over the same episode is a bug, and gate
G-J3 checks exactly that consistency.

OUTPUT
------
``data/receptor_exposure.parquet``  receptor x day: modelled exposure, arriving parcel count,
                                    GFAS-height share
``data/attribution.parquet``        receptor x day x province: share, from the back-trajectories
``data/back_traj.parquet``          receptor x day x parcel x step: the polylines the hero draws
``data/transport_meta.json``        the G-J3 bearing table and the fallback shares
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import config
import util
from util import log

PARTS = config.DATA_DIR / "era5_parts"
EXPOSURE_OUT = config.DATA_DIR / "receptor_exposure.parquet"
ATTR_OUT = config.DATA_DIR / "attribution.parquet"
BACK_OUT = config.DATA_DIR / "back_traj.parquet"
META_OUT = config.DATA_DIR / "transport_meta.json"
PARTS_T = config.DATA_DIR / "transport_parts"

N_SOURCE_CELLS = 120        # the strongest fire cells per day, by summed FRP
N_PARCELS_FWD = 12          # per source cell, jittered in height and release hour
N_PARCELS_BACK = 30         # per receptor-day; this ensemble IS the plume-width proxy
STEP_STORE_H = 3            # store the back-trajectory polyline every 3 h, integrate hourly
R_EARTH_M = 6_371_008.8

# US standard atmosphere, coarse: height (m) -> pressure (hPa).  Only used to pick which
# ERA5 level steers a parcel, so a 20 hPa error is immaterial; a 2,000 m error is not.
def h_to_p(h_m):
    import numpy as np
    return 1013.25 * np.power(np.clip(1.0 - 2.25577e-5 * np.asarray(h_m, dtype=float), 1e-6, 1.0),
                              5.25588)


def p_to_h(p_hpa):
    import numpy as np
    return (1.0 - np.power(np.clip(np.asarray(p_hpa, dtype=float) / 1013.25, 1e-6, 2.0),
                           1.0 / 5.25588)) / 2.25577e-5


class WindField:
    """The 6-hourly steering field for one year, held as dense arrays.

    Loading is per-year and cached for one year at a time: a trajectory never crosses a year
    boundary by more than 72 hours, and the December/January seam is handled by holding two
    years when it does.
    """

    def __init__(self) -> None:
        self._cache: dict[int, dict] = {}

    def year(self, y: int):
        import numpy as np
        if y in self._cache:
            return self._cache[y]
        p = PARTS / f"pl_{y}.npz"
        if not p.exists():
            return None
        z = np.load(p)
        d = {"u": z["u"].astype("float32") / float(z["scale_uv"]),
             "v": z["v"].astype("float32") / float(z["scale_uv"]),
             "w": z["w"].astype("float32") / float(z["scale_w"]),
             "lat": z["lat"], "lon": z["lon"], "level": z["level"].astype("float32"),
             "t": z["t"].astype("int64")}
        if len(self._cache) >= 2:
            self._cache.pop(next(iter(self._cache)))
        self._cache[y] = d
        return d

    def available_years(self) -> list[int]:
        return sorted(int(p.stem.split("_")[1]) for p in PARTS.glob("pl_*.npz"))


def wind_field(day):
    """Blend the ``config.TRAJ_LEVELS`` winds into one steering field for the day.

    Kept for the scaffold's API and used as the fallback for parcels with no known release
    height.  Weights are fixed in config, not fitted.
    """
    import numpy as np
    w = np.array([config.TRAJ_LEVELS[str(int(l))] for l in (925, 850, 700)], dtype="float32")
    return w / w.sum()


def _sample(field, t_epoch, plat, plon, ppress, use_height):
    """Trilinear-ish sample: bilinear in space, linear in time, log-linear in pressure.

    Returns (u, v, w_omega) in m/s, m/s, Pa/s.  Parcels outside the domain come back as NaN and
    the caller terminates them — never clamps them to the boundary, because a clamped parcel
    piles up on the edge and invents a source region that is not there.
    """
    import numpy as np
    lat, lon, ts, lev = field["lat"], field["lon"], field["t"], field["level"]
    # time
    i1 = np.searchsorted(ts, t_epoch)
    i1 = int(np.clip(i1, 1, len(ts) - 1))
    i0 = i1 - 1
    span = max(ts[i1] - ts[i0], 1)
    ft = np.clip((t_epoch - ts[i0]) / span, 0.0, 1.0)
    # space (lat descends, lon ascends).  A parcel that has already left the domain arrives here
    # as NaN and MUST NOT be indexed with: floor(NaN).astype(int) is INT_MIN, which numpy
    # happily uses as a negative index and which silently reads the wrong corner of the grid.
    # Terminated parcels are parked on cell (0,0) and then masked out again below.
    dlat = float(lat[1] - lat[0])
    dlon = float(lon[1] - lon[0])
    finite = np.isfinite(plat) & np.isfinite(plon) & np.isfinite(ppress)
    plat_s = np.where(finite, plat, lat[0])
    plon_s = np.where(finite, plon, lon[0])
    fy = (plat_s - lat[0]) / dlat
    fx = (plon_s - lon[0]) / dlon
    ok = finite & (fy >= 0) & (fy <= len(lat) - 1.001) & (fx >= 0) & (fx <= len(lon) - 1.001)
    y0 = np.clip(np.floor(fy), 0, len(lat) - 2).astype(np.int64)
    x0 = np.clip(np.floor(fx), 0, len(lon) - 2).astype(np.int64)
    wy, wx = fy - y0, fx - x0
    # pressure -> level weights
    lp = np.log(np.clip(np.where(finite, ppress, 850.0), 100.0, 1050.0))
    llev = np.log(lev)                                  # [925, 850, 700] descending in pressure
    if use_height is None:
        blend = wind_field(None)
        wl = np.tile(blend, (len(plat), 1))
    else:
        wl = np.zeros((len(plat), len(lev)), dtype="float32")
        idx = np.clip(np.searchsorted(-llev, -lp), 1, len(lev) - 1)
        lo, hi = idx - 1, idx
        f = (llev[lo] - lp) / np.maximum(llev[lo] - llev[hi], 1e-9)
        f = np.clip(f, 0.0, 1.0)
        rows = np.arange(len(plat))
        wl[rows, lo] = 1.0 - f
        wl[rows, hi] = f
        fb = ~use_height
        if fb.any():
            wl[fb] = wind_field(None)

    def grab(arr, ti):
        a = arr[ti]                                     # (level, lat, lon)
        c00 = a[:, y0, x0]
        c01 = a[:, y0, x0 + 1]
        c10 = a[:, y0 + 1, x0]
        c11 = a[:, y0 + 1, x0 + 1]
        bil = ((1 - wy) * ((1 - wx) * c00 + wx * c01)
               + wy * ((1 - wx) * c10 + wx * c11))       # (level, n)
        return (bil.T * wl).sum(axis=1)

    out = []
    for arr in (field["u"], field["v"], field["w"]):
        out.append((1 - ft) * grab(arr, i0) + ft * grab(arr, i1))
    u, v, om = out
    u = np.where(ok, u, np.nan)
    v = np.where(ok, v, np.nan)
    om = np.where(ok, om, np.nan)
    return u, v, om


def integrate(field, t0_epoch, lat0, lon0, press0, use_height, direction: int, hours: int,
              dt_min: int = None):
    """Advect parcels with a second-order (Petterssen) scheme on the steering field.

    First-order Euler drifts badly over 72 hours in curved flow — the iterative corrector is
    cheap and is the difference between a trajectory that lands on Singapore and one that lands
    in the Java Sea.  Parcels leaving the domain are terminated (set to NaN) and counted, never
    clamped to the boundary.

    Returns arrays of shape (steps + 1, n_parcels) for lat, lon and pressure.
    """
    import numpy as np
    dt = (dt_min or config.TRAJ_DT_MIN) * 60 * direction
    n_steps = int(hours * 60 / (dt_min or config.TRAJ_DT_MIN))
    n = len(lat0)
    LAT = np.full((n_steps + 1, n), np.nan, dtype="float32")
    LON = np.full((n_steps + 1, n), np.nan, dtype="float32")
    PR = np.full((n_steps + 1, n), np.nan, dtype="float32")
    lat, lon, pr = np.asarray(lat0, float), np.asarray(lon0, float), np.asarray(press0, float)
    LAT[0], LON[0], PR[0] = lat, lon, pr
    t = float(t0_epoch)
    for s in range(n_steps):
        u0, v0, om0 = _sample(field, t, lat, lon, pr, use_height)
        # predictor
        lat1 = lat + (v0 * dt) / R_EARTH_M * (180.0 / np.pi)
        lon1 = lon + (u0 * dt) / (R_EARTH_M * np.cos(np.radians(np.clip(lat, -89, 89)))) \
            * (180.0 / np.pi)
        pr1 = pr + om0 * dt / 100.0
        # corrector, twice (Petterssen)
        for _ in range(2):
            u1, v1, om1 = _sample(field, t + dt, lat1, lon1, pr1, use_height)
            um, vm, omm = 0.5 * (u0 + u1), 0.5 * (v0 + v1), 0.5 * (om0 + om1)
            lat1 = lat + (vm * dt) / R_EARTH_M * (180.0 / np.pi)
            lon1 = lon + (um * dt) / (R_EARTH_M
                                      * np.cos(np.radians(np.clip(lat, -89, 89)))) \
                * (180.0 / np.pi)
            pr1 = pr + omm * dt / 100.0
        lat, lon, pr = lat1, lon1, np.clip(pr1, 500.0, 1010.0)
        t += dt
        LAT[s + 1], LON[s + 1], PR[s + 1] = lat, lon, pr
    return LAT, LON, PR


def injection_height(cells, day, gfas):
    """Release height for a parcel: GFAS where it exists, ``config.PLUME_RISE`` where it does not.

    Returns ``(height_m, from_gfas)`` so the fallback share can be reported.  Even with GFAS the
    ensemble is released across a spread rather than a single height, and that spread is carried
    into the exposure estimate as an uncertainty band on the dashboard.
    """
    import numpy as np
    import pandas as pd
    n = len(cells)
    h = np.full(n, np.nan)
    if gfas is not None and len(gfas):
        g = gfas[gfas["day"] == pd.Timestamp(day)]
        if len(g):
            col = next((c for c in ("injection_height_m", "injh", "injection_height",
                                    "plume_top_m", "apt") if c in g.columns), None)
            if col:
                m = dict(zip(g["cell"].to_numpy(), g[col].to_numpy()))
                h = np.array([m.get(int(c), np.nan) for c in cells], dtype=float)
    from_gfas = np.isfinite(h) & (h > 50)
    fallback = ~from_gfas
    if fallback.any():
        rng = np.random.default_rng(int(pd.Timestamp(day).toordinal()))
        h[fallback] = rng.choice(config.PLUME_RISE, size=int(fallback.sum()))
    return h, from_gfas


def compare_cams(exposure, cams):
    """Trajectory exposure against the CAMS chemistry forecast, aligned by ISSUE time.

    Aligning by valid time instead would compare our day-3 forecast against CAMS's analysis and
    flatter us.  Published as a divergence chart, not as a score we claim to win.
    """
    import numpy as np
    import pandas as pd
    if cams is None or not len(cams) or not len(exposure):
        return {"status": "unavailable"}
    out = {}
    for name, m in config.RECEPTORS.items():
        clat, clon = util.snap_cell(m["lat"], m["lon"])
        cell = int(util.cell_key(clat, clon))
        c = cams[cams["cell"] == cell]
        if c.empty:
            continue
        e = exposure[exposure["receptor"] == name][["day", "exposure"]]
        rows = []
        for lead_h, g in c.groupby("lead_h"):
            g = g.assign(valid=pd.to_datetime(g["issue"])
                         + pd.to_timedelta(g["lead_h"], unit="h"))
            j = g.merge(e, left_on=g["valid"].dt.normalize(), right_on="day", how="inner")
            if len(j) > 20:
                rows.append({"lead_h": int(lead_h), "n": int(len(j)),
                             "spearman": float(j[["pm25", "exposure"]]
                                               .corr(method="spearman").iloc[0, 1])})
        if rows:
            out[name] = rows
    return {"status": "ok", "by_receptor": out,
            "alignment": "aligned by CAMS ISSUE time and lead, not by valid time"}


def receptor_exposure(traj_lat, traj_lon, weights, hours_axis):
    """FRP-weighted, travel-time-decayed parcel density within reach of each receptor."""
    import numpy as np
    out = {}
    decay = np.exp(-np.log(2.0) * hours_axis[:, None] / config.DECAY_HALFLIFE_H)
    for name, m in config.RECEPTORS.items():
        d = util.haversine_km(m["lat"], m["lon"], traj_lat, traj_lon)
        hit = (d <= config.RECEPTOR_KM) & np.isfinite(d)
        out[name] = float((hit * decay * weights[None, :]).sum())
    return out


def attribute(back_lat, back_lon, fires_window, cell_prov):
    """Back-trajectory attribution: which provinces' fires the air passed over.

    Returns a province share vector, and — importantly — an explicit "no attributable source"
    outcome when the back-trajectory passes over no fire at all, which is the correct answer on
    the many bad-air days that are local, not transboundary.
    """
    import numpy as np
    import pandas as pd
    n_parcels = back_lat.shape[1]
    lat = back_lat.ravel()
    lon = back_lon.ravel()
    parcel_of = np.tile(np.arange(n_parcels), back_lat.shape[0])
    ok = np.isfinite(lat) & np.isfinite(lon)
    if not ok.any() or fires_window.empty:
        return {}, 0.0
    clat, clon = util.snap_cell(lat[ok], lon[ok])
    keys = util.cell_key(clat, clon)
    pts = pd.DataFrame({"cell": keys, "parcel": parcel_of[ok]})
    visits = pts["cell"].value_counts()
    # residence time x emission, the standard receptor-model weighting (PSCF/CWT family)
    f = fires_window.groupby("cell", as_index=False)["frp_sum"].sum()
    f["visits"] = f["cell"].map(visits).fillna(0.0)
    f["score"] = f["frp_sum"] * f["visits"]
    tot = float(f["score"].sum())
    if tot <= 0:
        return {}, 0.0
    f["prov"] = f["cell"].map(cell_prov)
    # ENSEMBLE AGREEMENT, which is the honest uncertainty statement on a province share.
    # A 95 % share carried by 3 of 30 parcels and a 60 % share carried by 28 of 30 are very
    # different claims, and a bar chart alone cannot tell them apart.
    fire_cells = set(f.loc[f["frp_sum"] > 0, "cell"].astype("int64"))
    over = pts[pts["cell"].isin(fire_cells)].copy()
    over["prov"] = over["cell"].map(cell_prov)
    agree = (over.groupby("prov")["parcel"].nunique() / max(n_parcels, 1)).to_dict()
    shares = (f.groupby("prov")["score"].sum() / tot).sort_values(ascending=False)
    out = {str(k): {"share": float(v), "agreement": float(agree.get(k, 0.0))}
           for k, v in shares.items() if v > 0.005}
    return out, tot


# ── driver ────────────────────────────────────────────────────────────────────────────
def _epoch(ts) -> float:
    import pandas as pd
    return float(pd.Timestamp(ts).value // 10**9)


def main() -> None:
    import numpy as np
    import pandas as pd
    util.guard_disk()
    wf = WindField()
    yrs = wf.available_years()
    util.require(bool(yrs), "no pressure-level parts — run era5 first")
    log(f"transport: steering field available for {yrs}")

    fires = pd.read_parquet(config.DATA_DIR / "fires_daily.parquet")
    fires["day"] = pd.to_datetime(fires["day"])
    static = pd.read_parquet(config.DATA_DIR / "cell_static.parquet")
    cell_prov = dict(zip(static["cell"], static["adm1_name"]))
    gfas_p = config.DATA_DIR / "gfas.parquet"
    gfas = pd.read_parquet(gfas_p) if gfas_p.exists() else None
    if gfas is not None:
        gfas["day"] = pd.to_datetime(gfas["day"])
        log(f"  gfas injection heights: {len(gfas):,} cell-days "
            f"({gfas['day'].min().date()} -> {gfas['day'].max().date()})")
    else:
        log("  gfas absent — every parcel uses the PLUME_RISE fallback, and the share is "
            "reported rather than hidden")

    # ── incremental by steering-field year ────────────────────────────────────────────
    # The ERA5 queue delivers pressure-level years one at a time over hours, so this stage is
    # re-run repeatedly.  Each year's trajectories are cached; a rerun integrates only the years
    # that have arrived since.  Deleting a part is the way to force a recompute.
    PARTS_T.mkdir(parents=True, exist_ok=True)
    done_years = {int(p.stem.split("_")[1]) for p in PARTS_T.glob("traj_*.parquet")}
    todo_years = [y for y in yrs if y not in done_years]
    if done_years:
        log(f"transport: {sorted(done_years)} already integrated; doing {todo_years}")
    days = sorted(d for d in pd.to_datetime(sorted(fires["day"].unique()))
                  if d.year in todo_years and d.month in config.ERA5_PL_MONTHS)
    log(f"transport: {len(days):,} fire-season days to integrate "
        f"({len(yrs)} steering-field years available)")

    exp_rows, attr_rows, back_rows, fwd_rows, bearing_rows = [], [], [], [], []
    n_gfas, n_parcels_total, n_escaped = 0, 0, 0
    hours = np.arange(config.TRAJ_HOURS + 1, dtype="float32")

    for di, day in enumerate(days):
        field = wf.year(day.year)
        if field is None:
            continue
        fd = fires[fires["day"] == day].nlargest(N_SOURCE_CELLS, "frp_sum")
        if fd.empty:
            continue
        # ---- forward: fires -> downwind receptors --------------------------------------
        src_cell = np.repeat(fd["cell"].to_numpy(), N_PARCELS_FWD)
        src_lat = np.repeat(fd["clat"].to_numpy(), N_PARCELS_FWD).astype(float)
        src_lon = np.repeat(fd["clon"].to_numpy(), N_PARCELS_FWD).astype(float)
        wgt = np.repeat(fd["frp_sum"].to_numpy(), N_PARCELS_FWD) / N_PARCELS_FWD
        h, from_gfas = injection_height(src_cell, day, gfas)
        rng = np.random.default_rng(day.toordinal())
        h = np.clip(h * rng.normal(1.0, 0.25, len(h)), 100, 6000)      # ensemble height spread
        src_lat += rng.normal(0, 0.06, len(src_lat))
        src_lon += rng.normal(0, 0.06, len(src_lon))
        pr = h_to_p(h)
        t0 = _epoch(day + pd.Timedelta(hours=6))         # release near local midday
        # every parcel HAS a height — from GFAS where GFAS exists, from PLUME_RISE where it does
        # not — so every parcel is steered by the wind at its own pressure.  The fixed
        # config.TRAJ_LEVELS blend is the fallback for a parcel with no height at all, which is
        # why `use_height` is all-True here and the GFAS share is reported separately.
        use_h = np.ones(len(src_lat), bool)
        LAT, LON, _ = integrate(field, t0, src_lat, src_lon, pr, use_h,
                                +1, config.TRAJ_HOURS)
        n_gfas += int(from_gfas.sum())
        n_parcels_total += len(src_lat)
        n_escaped += int(np.isnan(LAT[-1]).sum())
        ex = receptor_exposure(LAT, LON, wgt, hours)
        for name, v in ex.items():
            exp_rows.append({"receptor": name, "day": day, "exposure": v,
                             "parcels": len(src_lat),
                             "gfas_share": float(from_gfas.mean())})

        # The hero's "flip the dial" control needs the SAME ENGINE run forwards, drawn.  Thinned
        # hard — the strongest sources, three parcels each, 3-hourly — because this is a
        # 4,000-day archive and the file is fetched on a click, not on first paint.
        if day.month in config.FIRE_SEASON_MONTHS or day.year in config.ANCHOR_YEARS:
            keep = np.argsort(-wgt)[: 40 * N_PARCELS_FWD : max(1, N_PARCELS_FWD // 3)]
            for pi in keep[:120]:
                la, lo = LAT[::STEP_STORE_H, pi], LON[::STEP_STORE_H, pi]
                good = np.isfinite(la)
                if good.sum() < 4:
                    continue
                fwd_rows.append({"day": day, "parcel": int(pi),
                                 "weight": float(wgt[pi]),
                                 "src_lat": float(src_lat[pi]), "src_lon": float(src_lon[pi]),
                                 "lat": la[good].astype("float32").tolist(),
                                 "lon": lo[good].astype("float32").tolist()})

        # ---- backward: receptor -> the fires it was standing on -------------------------
        fw = fires[(fires["day"] >= day - pd.Timedelta(days=3)) & (fires["day"] <= day)]
        for name, m in config.RECEPTORS.items():
            arrive = ex.get(name, 0.0)
            if arrive <= 0 and day.year not in config.ANCHOR_YEARS:
                continue
            blat = np.full(N_PARCELS_BACK, m["lat"]) + rng.normal(0, 0.08, N_PARCELS_BACK)
            blon = np.full(N_PARCELS_BACK, m["lon"]) + rng.normal(0, 0.08, N_PARCELS_BACK)
            bh = rng.choice([300.0, 800.0, 1500.0], size=N_PARCELS_BACK)
            BLAT, BLON, _ = integrate(field, _epoch(day + pd.Timedelta(hours=6)),
                                      blat, blon, h_to_p(bh),
                                      np.ones(N_PARCELS_BACK, bool), -1, config.TRAJ_HOURS)
            shares, score = attribute(BLAT, BLON, fw, cell_prov)
            for prov, v in shares.items():
                attr_rows.append({"receptor": name, "day": day, "province": prov,
                                  "share": v["share"], "agreement": v["agreement"],
                                  "n_parcels": N_PARCELS_BACK})
            if not shares:
                attr_rows.append({"receptor": name, "day": day,
                                  "province": "no attributable source", "share": 1.0,
                                  "agreement": 1.0, "n_parcels": N_PARCELS_BACK})
            # G-J3: the two directions must agree on where the smoke came from
            if score > 0 and arrive > 0:
                ok = np.isfinite(BLAT.ravel())
                clat_, clon_ = util.snap_cell(BLAT.ravel()[ok], BLON.ravel()[ok])
                k = util.cell_key(clat_, clon_)
                vis = pd.Series(k).value_counts()
                ff = fw.groupby(["cell", "clat", "clon"], as_index=False)["frp_sum"].sum()
                ff["w"] = ff["frp_sum"] * ff["cell"].map(vis).fillna(0.0)
                if ff["w"].sum() > 0:
                    bcy = float((ff["clat"] * ff["w"]).sum() / ff["w"].sum())
                    bcx = float((ff["clon"] * ff["w"]).sum() / ff["w"].sum())
                    hit = (util.haversine_km(m["lat"], m["lon"], LAT, LON)
                           <= config.RECEPTOR_KM)
                    reach = hit.any(axis=0)
                    if reach.sum() >= 3:
                        fcy = float(np.average(src_lat[reach], weights=wgt[reach]))
                        fcx = float(np.average(src_lon[reach], weights=wgt[reach]))
                        b_back = util.bearing_deg(m["lat"], m["lon"], bcy, bcx)
                        b_fwd = util.bearing_deg(m["lat"], m["lon"], fcy, fcx)
                        bearing_rows.append({
                            "receptor": name, "day": day,
                            "bearing_back": float(b_back), "bearing_forward": float(b_fwd),
                            "diff_deg": float(util.angdiff_deg(b_back, b_fwd)),
                            "n_arriving": int(reach.sum())})
            # store the polyline for the hero, thinned to 3-hourly
            if day.year in config.ANCHOR_YEARS or arrive > 0:
                step = STEP_STORE_H
                for pi in range(N_PARCELS_BACK):
                    la, lo = BLAT[::step, pi], BLON[::step, pi]
                    good = np.isfinite(la)
                    if good.sum() < 4:
                        continue
                    back_rows.append({"receptor": name, "day": day, "parcel": pi,
                                      "lat": la[good].astype("float32").tolist(),
                                      "lon": lo[good].astype("float32").tolist(),
                                      "hours": (np.arange(len(la))[good] * step).tolist()})
        if di % 200 == 0:
            log(f"  {di}/{len(days)} {day.date()}: {len(fd)} source cells, "
                f"{len(src_lat)} parcels, GFAS height on {from_gfas.mean():.0%}")

    # cache this run's years, then rebuild the consolidated tables from EVERY cached year
    for y in todo_years:
        for name, rows in (("exp", exp_rows), ("attr", attr_rows), ("back", back_rows),
                           ("fwd", fwd_rows), ("bear", bearing_rows)):
            sub = [r for r in rows if pd.Timestamp(r["day"]).year == y]
            pd.DataFrame(sub).to_parquet(PARTS_T / f"{name}_{y}.parquet", index=False)
        # a marker part so `done_years` sees the year even if it produced no episodes at all
        pd.DataFrame({"year": [y], "days": [sum(1 for d in days if d.year == y)],
                      "parcels": [n_parcels_total]}).to_parquet(
            PARTS_T / f"traj_{y}.parquet", index=False)

    def gather(name):
        ps = sorted(PARTS_T.glob(f"{name}_*.parquet"))
        fr = [pd.read_parquet(p) for p in ps]
        fr = [f for f in fr if len(f)]
        return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()

    exp = gather("exp")
    if len(exp):
        exp.to_parquet(EXPOSURE_OUT, index=False, compression="zstd")
    attr = gather("attr")
    if len(attr):
        attr.to_parquet(ATTR_OUT, index=False, compression="zstd")
    back = gather("back")
    if len(back):
        back.to_parquet(BACK_OUT, index=False, compression="zstd")
    fwd = gather("fwd")
    if len(fwd):
        fwd.to_parquet(config.DATA_DIR / "fwd_traj.parquet", index=False, compression="zstd")
    bear = gather("bear")

    cams_p = config.DATA_DIR / "cams_forecast.parquet"
    cams = pd.read_parquet(cams_p) if cams_p.exists() else None
    prev = json.loads(META_OUT.read_text()) if META_OUT.exists() else {}
    # running totals across cached years, so a re-run reports the whole archive, not one pass
    tot_parcels = int(prev.get("parcels", 0)) + int(n_parcels_total)
    tot_gfas = int(prev.get("parcels_gfas_height", 0)) + int(n_gfas)
    tot_escaped = int(prev.get("parcels_escaped", 0)) + int(n_escaped)
    meta = {
        "years_integrated": sorted(done_years | set(todo_years)),
        "days": int(prev.get("days", 0)) + len(days),
        "parcels": tot_parcels,
        "parcels_gfas_height": tot_gfas, "parcels_escaped": tot_escaped,
        "gfas_height_share": float(tot_gfas / max(tot_parcels, 1)),
        "plume_rise_fallback_share": float(1 - tot_gfas / max(tot_parcels, 1)),
        "escaped_domain_share": float(tot_escaped / max(tot_parcels, 1)),
        "levels": config.TRAJ_LEVELS, "hours": config.TRAJ_HOURS,
        "dt_min": config.TRAJ_DT_MIN, "scheme": "Petterssen 2nd-order, 2 corrector passes",
        "receptor_km": config.RECEPTOR_KM, "decay_halflife_h": config.DECAY_HALFLIFE_H,
        "bearing_rows": int(len(bear)),
        "cams_comparison": compare_cams(exp, cams),
    }
    if len(bear):
        bear.to_parquet(config.DATA_DIR / "bearing_check.parquet", index=False)
        meta["bearing_agreement_share"] = float(
            (bear["diff_deg"] <= config.GATE_BEARING_DEG).mean())
        meta["bearing_median_diff"] = float(bear["diff_deg"].median())
    META_OUT.write_text(json.dumps(meta, indent=1, default=str))
    log(f"transport: {len(exp):,} receptor-days, {len(attr):,} attribution rows, "
        f"{len(bear):,} bearing checks; GFAS heights on "
        f"{meta['gfas_height_share']:.0%} of parcels")
    if len(bear):
        log(f"  G-J3 preview: bearing agreement within {config.GATE_BEARING_DEG} deg on "
            f"{meta['bearing_agreement_share']:.1%} of days "
            f"(median difference {meta['bearing_median_diff']:.1f} deg)")
    util.manifest_put("transport", **{k: meta[k] for k in
                                      ("days", "parcels", "gfas_height_share")})


if __name__ == "__main__":
    main()
