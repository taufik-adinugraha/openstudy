"""Stage 3 · climate — rainfall, monsoon onset, ENSO/IOD, and the "why radar" evidence.

CHIRPS — v3, PENTADS, AND THE PATH THAT ACTUALLY EXISTS
-------------------------------------------------------
v2 production ends after December 2026, so pinning v2 would pin a product that stops updating
mid-engagement; the build targets v3.  The reconnaissance recorded the v3 COG tree as
``global_pentad/cogs/`` — that path 404s.  The live layout is

    https://data.chc.ucsb.edu/products/CHIRPS/v3.0/pentads/global/cogs/chirps-v3.0.YYYY.MM.P.cog

a flat directory of COGs, one per pentad (P = 1..6), 0.05 deg global.  They are real COGs, so the
Java window is a range read rather than a 3 MiB whole-file download, which is the saving the
spec was after by a different route.  CHC waives copyright informally — one named individual, no
CC deed link, no version string — and that is how it is described rather than as "CC0".

Also carried forward from CHC's own README: a %CCD bug set precipitation to ZERO where IR data
was missing and it "was always a problem for Eastern Australia/Indonesia/Japan, where a gap
between two geostationary satellites exists".  It was reprocessed in 2015 so our window is clean,
but Java sits in that gap, so zero-rainfall runs are counted and reported before any threshold is
built on them.

MONSOON ONSET HAS A DOZEN DEFINITIONS AND THEY DISAGREE BY WEEKS
-----------------------------------------------------------------
So ours is stated: the first pentad from 1 September whose own and next pentad's rainfall sum to
at least ``config.ONSET_RULE['accum_mm']`` mm, with no dry spell (a pentad pair below
``dry_spell_mm``) in the following 30 days.  At pentad resolution that is the config's 40 mm over
10 days rule as closely as a 5-day product can express it, and the resolution limit is stated
with the number.

WHY RADAR, MEASURED RATHER THAN ASSERTED
-----------------------------------------
Chapter 01 counts usable Sentinel-2 scenes per month over the six kabupaten from the anonymous
Element84 Earth Search STAC (no registration) against the Sentinel-1 acquisitions we actually
used.  The wet-season collapse in the optical record is the argument for the whole method, and
it is a scene count, not a claim.

OUTPUT: data/climate_pentad.parquet, data/onset.parquet, data/enso.parquet,
data/climate_kab_month.parquet, data/optical_vs_radar.parquet.
"""

from __future__ import annotations

import io
import json
import re

import config
import util
from util import log

CHIRPS_PENTAD = ("https://data.chc.ucsb.edu/products/CHIRPS/v3.0/pentads/global/cogs/"
                 "chirps-v3.0.{y}.{m:02d}.{p}.cog")
PENTAD_START_DAY = {1: 1, 2: 6, 3: 11, 4: 16, 5: 21, 6: 26}
# NOAA CPC's ``origin.`` host does not resolve from this network; PSL serves the same ONI in the
# same year-plus-twelve-values layout as the DMI file, which also removes a second parser.
ONI_URL = "https://psl.noaa.gov/data/correlation/oni.data"
DMI_URL = "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"


def chirps() -> "pd.DataFrame":
    """Per-kabupaten pentad rainfall, window-read from the remote COGs."""
    import numpy as np
    import pandas as pd
    import rasterio
    from rasterio.windows import from_bounds

    cells = pd.read_parquet(config.DATA_DIR / "cells.parquet")
    boxes = (cells.groupby("kabupaten")
             .agg(w=("lon", "min"), e=("lon", "max"), s=("lat", "min"), n=("lat", "max")))
    y0, y1 = 2018, pd.Timestamp(config.SAR_END).year
    rows, missing, zeroruns = [], 0, 0
    with util.gdal_env():
        for y in range(y0, y1 + 1):
            for m in range(1, 13):
                for p in range(1, 7):
                    url = CHIRPS_PENTAD.format(y=y, m=m, p=p)
                    try:
                        with rasterio.open(f"/vsicurl/{url}") as ds:
                            for kab, b in boxes.iterrows():
                                win = from_bounds(b.w, b.s, b.e, b.n, transform=ds.transform)
                                a = ds.read(1, window=win, boundless=True,
                                            fill_value=np.nan).astype("float32")
                                a[a < 0] = np.nan
                                v = float(np.nanmean(a)) if np.isfinite(a).any() else np.nan
                                rows.append(dict(kabupaten=kab, year=y, month=m, pentad=p,
                                                 date=pd.Timestamp(y, m,
                                                                   PENTAD_START_DAY[p]),
                                                 rain_mm=v))
                                zeroruns += int(np.isfinite(v) and v == 0.0)
                    except Exception:                          # noqa: BLE001
                        missing += 1
            log(f"climate: CHIRPS {y} done ({len(rows):,} rows, {missing} pentads unavailable)")
    df = pd.DataFrame(rows)
    log(f"climate: CHIRPS {len(df):,} kabupaten-pentads, {missing} pentads unavailable, "
        f"{zeroruns} exact zeros (the Indonesia IR-gap caveat is checked, not assumed)")
    return df


def onset(pentads):
    """Monsoon onset per kabupaten per season under the STATED rule (see the docstring)."""
    import numpy as np
    import pandas as pd

    r = config.ONSET_RULE
    out = []
    for kab, g in pentads.sort_values("date").groupby("kabupaten"):
        g = g.reset_index(drop=True)
        v = g["rain_mm"].to_numpy("float64")
        for season in config.SEASONS:
            y = int(season[:4])
            lo = pd.Timestamp(y, 9, 1)
            hi = pd.Timestamp(y + 1, 3, 31)
            sel = np.flatnonzero((g["date"] >= lo) & (g["date"] <= hi))
            hit = None
            for i in sel[:-8]:
                if not np.isfinite(v[i]) or not np.isfinite(v[i + 1]):
                    continue
                if v[i] + v[i + 1] < r["accum_mm"]:
                    continue
                nxt = v[i + 2:i + 8]
                pairs = nxt[:-1] + nxt[1:]
                if np.nanmin(pairs) < r["dry_spell_mm"] * 2:
                    continue
                hit = g["date"].iloc[i]
                break
            out.append(dict(kabupaten=kab, season=season,
                            onset=hit, onset_doy=(hit.dayofyear if hit is not None else np.nan)))
    d = pd.DataFrame(out)
    # The anomaly is in DAY-OF-YEAR space, not in calendar time.  Differencing raw timestamps
    # against a median timestamp measures how far the season is from the middle of the record —
    # which is a number in the hundreds of days and says nothing about whether the rain was
    # early.  Day-of-year is shifted so that September is the origin, because a monsoon that
    # arrives on 2 January is late, not 250 days early.
    d["shift_doy"] = d["onset_doy"].map(lambda v: v - 365 if pd.notna(v) and v < 180 else v)
    med = d.groupby("kabupaten")["shift_doy"].transform("median")
    d["onset_anom_days"] = (d["shift_doy"] - med).round()
    return d.drop(columns=["shift_doy"])


def enso():
    """NOAA ONI and HadISST DMI, monthly, with an explicit interpolation flag downstream."""
    import numpy as np
    import pandas as pd
    import requests

    def grid(url, name, missing=-9.0):
        out = []
        try:
            t = requests.get(url, headers=util.browser_ua(), timeout=120).text
            for line in t.splitlines():
                f = line.split()
                if len(f) == 13 and f[0].isdigit() and len(f[0]) == 4:
                    for m, v in enumerate(f[1:], 1):
                        try:
                            fv = float(v)
                        except ValueError:
                            continue
                        if fv > missing:
                            out.append(dict(index=name, year=int(f[0]), month=m, value=fv))
            log(f"climate: {name} {len(out)} monthly values "
                f"({min((r['year'] for r in out), default='-')}"
                f"-{max((r['year'] for r in out), default='-')})")
        except Exception as exc:                               # noqa: BLE001
            log(f"climate: {name} unavailable ({type(exc).__name__} {exc})")
        return out

    return pd.DataFrame(grid(ONI_URL, "ONI", -90.0) + grid(DMI_URL, "DMI"))


def optical_vs_radar():
    """Usable Sentinel-2 scenes per month over the six kabupaten vs the S1 record we used."""
    import collections
    import pandas as pd
    import requests

    cells = pd.read_parquet(config.DATA_DIR / "cells.parquet")
    boxes = (cells.groupby("kabupaten")
             .agg(w=("lon", "min"), e=("lon", "max"), s=("lat", "min"), n=("lat", "max")))
    counts = collections.Counter()
    total = collections.Counter()
    fails = 0
    for kab, b in boxes.iterrows():
        for y in (2023, 2024, 2025):
            for m in range(1, 13):
                last = pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)
                q = {"collections": [config.S2_COLLECTION],
                     "bbox": [float(b.w), float(b.s), float(b.e), float(b.n)],
                     "datetime": f"{y}-{m:02d}-01T00:00:00Z/{last:%Y-%m-%d}T23:59:59Z",
                     "limit": 1}
                try:
                    r = requests.post(f"{config.S2_STAC}/search", json=q, timeout=90)
                    r.raise_for_status()
                    total[(kab, m)] += int(r.json().get("numberMatched") or 0)
                    q["query"] = {"eo:cloud_cover": {"lt": config.S2_CLOUD_MAX}}
                    r = requests.post(f"{config.S2_STAC}/search", json=q, timeout=90)
                    r.raise_for_status()
                    counts[(kab, m)] += int(r.json().get("numberMatched") or 0)
                except Exception as exc:                       # noqa: BLE001
                    fails += 1
                    if fails <= 3:
                        log(f"climate: STAC {kab} {y}-{m:02d} failed "
                            f"({type(exc).__name__} {exc})")
        log(f"climate: Sentinel-2 counts for {kab} done ({fails} request failures so far)")
    util.require(bool(total), "optical scene counts came back empty — chapter 01 has no evidence")
    idx = json.loads((config.DATA_DIR / "sar_index.json").read_text()) \
        if (config.DATA_DIR / "sar_index.json").exists() else {}
    s1 = collections.Counter()
    for kab, v in idx.items():
        for it in v.get("items", []):
            if it["orbit"] in v.get("orbits", []) and it["date"][:4] in ("2023", "2024", "2025"):
                s1[(kab, int(it["date"][5:7]))] += 1
    rows = [dict(kabupaten=k, month=m, s2_all=total[(k, m)], s2_usable=counts[(k, m)],
                 s1_acquisitions=s1.get((k, m), 0),
                 s2_usable_share=(counts[(k, m)] / total[(k, m)] if total[(k, m)] else None))
            for (k, m) in sorted(total)]
    return pd.DataFrame(rows)


def main() -> None:
    import pandas as pd

    util.guard_disk()
    D = config.DATA_DIR
    p = chirps()
    p.to_parquet(D / "climate_pentad.parquet", index=False)
    o = onset(p)
    o.to_parquet(D / "onset.parquet", index=False)
    log("climate: monsoon onset by kabupaten and season")
    for r in o.itertuples():
        when = str(r.onset.date()) if pd.notna(r.onset) else "not detected"
        anom = f"{r.onset_anom_days:+.0f} d" if pd.notna(r.onset_anom_days) else "—"
        log(f"    {r.kabupaten:11s} {r.season}  {when:>12}   anomaly {anom}")
    e = enso()
    if len(e):
        e.to_parquet(D / "enso.parquet", index=False)
    km = (p.assign(t_year=p["year"], t_month=p["month"])
          .groupby(["kabupaten", "t_year", "t_month"], as_index=False)["rain_mm"].sum())
    clim = km.groupby(["kabupaten", "t_month"])["rain_mm"].transform("mean")
    km["rain_anom_mm"] = km["rain_mm"] - clim
    km.to_parquet(D / "climate_kab_month.parquet", index=False)
    try:
        ov = optical_vs_radar()
        ov.to_parquet(D / "optical_vs_radar.parquet", index=False)
        g = ov.groupby("month")[["s2_all", "s2_usable", "s1_acquisitions"]].sum()
        log("climate: usable Sentinel-2 scenes per month (2023-25, six kabupaten) vs S1")
        for m, r in g.iterrows():
            log(f"    month {m:2d}  S2 all {int(r.s2_all):5d}  usable<{config.S2_CLOUD_MAX}% "
                f"{int(r.s2_usable):5d} ({100 * r.s2_usable / max(r.s2_all, 1):4.1f} %)  "
                f"S1 {int(r.s1_acquisitions):4d}")
    except Exception as exc:                                   # noqa: BLE001
        log(f"climate: optical comparison failed ({type(exc).__name__} {exc})")
    log(f"climate -> {D}")


if __name__ == "__main__":
    main()
