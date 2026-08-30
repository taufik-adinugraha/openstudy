"""Stage 1 · fires — FIRMS hotspot archive and near-real-time, cleaned.

WHY THIS STAGE IS FIRST AND WHY IT IS NOT TRIVIAL
-------------------------------------------------
Roughly one detection in ten inside the Indonesian box is not a landscape fire.  Indonesia has
~130 active volcanoes and a working oil-and-gas industry, and both radiate at the wavelengths
VIIRS and MODIS watch.  A hotspot table taken at face value therefore reports Merapi, Sinabung,
Dukono and the Duri and Bontang flares as "fires" every single day of the year — a permanent,
perfectly seasonal-looking background that a risk model will happily learn and then present as
skill.  Two filters remove it:

  * ``type == 0`` only, WHERE THE FIELD EXISTS.  FIRMS classifies each detection: 0 = presumed
    vegetation fire, 1 = active volcano, 2 = other static land source (the flares live here),
    3 = offshore.  ** THE FIELD IS ABSENT FROM EVERY NRT PRODUCT. **  NASA states it plainly:
    "data distributed via the FIRMS download tool does not attribute the static sources/inferred
    hotspot 'type'".  So a bare ``type == 0`` filter SILENTLY NO-OPS on the live tail, the recent
    part of the series keeps volcanoes and flares that the historical part drops, and the SP/NRT
    seam becomes a step change that looks like a trend.  (Case E's ``ingest_firms.py`` has this
    bug today.)  ``type`` is therefore used to BUILD a static exclusion mask from the SP archive,
    and THE MASK — not the field — is what filters every product.
  * drop low confidence, and ONLY low.  Measured on the full box for 2019-09-15..19 (35,229
    detections): confidence is ``n`` 91.2 %, ``l`` 5.7 %, ``h`` only 3.2 %.  Filtering to
    high-confidence would discard 97 % of the signal.  ``config.DROP_CONF`` drops ``l``; MODIS's
    numeric 0-100 scale gets ``config.MIN_CONF_MODIS`` so the two sensors are filtered on
    comparable terms rather than on whatever the column happens to contain.

The geometric half of the mask: any detection within ``config.VOLCANO_EXCLUDE_KM`` of a known
volcanic summit (OSM ``natural=volcano`` via Overpass, ODbL, because the Smithsonian GVP is
Cloudflare-gated) or of an empirically-identified persistent source is removed.  Persistent
sources are found from the data itself: a fine cell whose ``type`` says static in two or more
distinct months of a fourteen-year record is a flare or a landfill, whatever any single row says.
That detector needs no external list and is the one actually relied on.

THE MASK IMPLEMENTATION IS PORTED FROM CASE E, NOT REWRITTEN.
``cases/air-quality/pipeline/ingest_firms.py`` already carries a working static-source mask —
0.01 deg cells, a cell kept once it is seen static in >= 2 distinct months, then buffered by its
8 neighbours because the same flare lands in an adjacent cell often enough that an unbuffered
mask leaks the source back in.  The same three constants and the same buffer are used here.  What
changes is the input: Case E samples one five-day window per month through the ``area`` API,
because that is all it has; this case has the per-country BULK CSVs, which carry ``type`` for
every row of the whole archive.  So the mask is built on the complete SP record rather than on a
monthly sample, and is strictly the stronger object.

SENSOR CHOICE AND THE 2015 ANCHOR
---------------------------------
VIIRS SNPP standard processing begins 2012-01-20, so **both anchors are inside the VIIRS record**
and no MODIS splice is needed for them.  Measured on the Sumatra box for 2015-10-20: VIIRS 6,110
detections against MODIS 1,354, a factor of 4.5.

** COLLECTION TRAP. **  The bulk country files are MODIS Collection 6.1 (``version`` "6.2") while
the API's ``MODIS_SP`` returns Collection 6.0 (``version`` "6.03").  Same sensor, same day,
different row counts.  Do not concatenate them.  VIIRS is consistent (``version`` "2") in both,
which is why VIIRS is the only sensor this case splices.

TWO ROUTES, AND THE BULK ONE IS BETTER FOR HISTORY
--------------------------------------------------
The ``area`` API caps at FIVE days per request, so the 2012-2026 archive is ~1,000 windows per
source.  The per-country bulk CSVs avoid nearly all of that: the directory listing 404s under the
new backend, but the files themselves are live at predictable URLs (``config.FIRMS_BULK_URL``),
and VIIRS SNPP 2012-2024 for Indonesia is about 250 MB total.  Bulk lags ~18 months (2025 and
2026 are 404 — re-verified at build time), so history comes from bulk and the tail from the API.
The file sizes alone tell the story and are charted as such: Indonesia 2015 = 66.1 MB,
2019 = 33.0 MB, 2016 (La Nina) = 8.8 MB.

OUTPUT
------
``data/fires.parquet``          one row per retained detection: lat, lon, acq_date, sensor,
                                confidence, frp_mw, daynight, cell (0.25 deg)
``data/fires_removed.parquet``  cell-level counts by ``removed_reason`` — published, not binned
``data/fires_daily.parquet``    cell x day counts and summed FRP, the panel input for features.py
``data/static_mask.parquet``    the mask itself, drawn on the methodology page

RESUMABILITY
------------
Every bulk file and every ``area`` window is a part on disk; a rerun skips what is present.  The
rate limit is 5,000 requests per 10 minutes on the shared key and the key's own transaction count
is polled rather than guessed.  ``--nrt-only`` fetches just the trailing window for the daily
refresh target.
"""

from __future__ import annotations

import argparse
import io
import json
import time
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import config
import util
from util import log

RAW_BULK = config.RAW / "firms_bulk"
RAW_WIN = config.RAW / "firms_windows"
MASK_OUT = config.DATA_DIR / "static_mask.parquet"
VOLCANO_OUT = config.DATA_DIR / "volcanoes.parquet"
FIRES_OUT = config.DATA_DIR / "fires.parquet"
REMOVED_OUT = config.DATA_DIR / "fires_removed.parquet"
DAILY_OUT = config.DATA_DIR / "fires_daily.parquet"
AUDIT_OUT = config.DATA_DIR / "fires_audit.json"

MASK_CELL_DEG = 0.01        # ~1.1 km here; a VIIRS pixel is 375 m  (Case E constant)
MASK_BUFFER_CELLS = 1       # keep the 3x3 ring around each core cell (Case E constant)
MASK_MIN_MONTHS = 2         # a cell must be labelled static in >= 2 distinct months
CELL_STRIDE = 1_000_000
STATIC_TYPES = (1, 2, 3)
# Overpass's main instance answers 406 to a plain POST from this network; the public mirrors do
# not.  All three are tried in order and the volcano layer degrades to "unavailable" rather than
# failing the stage, because the empirical detector is the one actually relied on.
OVERPASS_MIRRORS = ("https://overpass-api.de/api/interpreter",
                    "https://overpass.kumi.systems/api/interpreter",
                    "https://overpass.private.coffee/api/interpreter",
                    "https://maps.mail.ru/osm/tools/overpass/api/interpreter")


# ── fine-grid helpers (the mask lives on a 0.01 deg grid, not the 0.25 deg model grid) ──
def fine_cells(lat, lon):
    import numpy as np
    ilat = np.floor(np.asarray(lat, dtype=float) / MASK_CELL_DEG).astype("int64")
    ilon = np.floor(np.asarray(lon, dtype=float) / MASK_CELL_DEG).astype("int64")
    return ilat * CELL_STRIDE + ilon


# ── acquisition ───────────────────────────────────────────────────────────────────────
def firms_quota() -> tuple[int, int]:
    """(used, limit) from the key's own transaction counter — measurement, not guesswork."""
    import requests
    try:
        r = requests.get(config.FIRMS_QUOTA_URL.format(key=config.FIRMS_MAP_KEY), timeout=30)
        j = r.json()
        return int(j["current_transactions"]), int(j["transaction_limit"])
    except Exception:                                       # noqa: BLE001
        return 0, 5000


def availability() -> dict[str, tuple[str, str]]:
    """``data_availability`` gives the SP/NRT seam.  Never hard-code it: it moves every month."""
    import pandas as pd
    import requests
    url = config.FIRMS_AVAIL_URL.format(key=config.FIRMS_MAP_KEY)
    txt = requests.get(url, timeout=60).text
    df = pd.read_csv(io.StringIO(txt))
    return {r.data_id: (r.min_date, r.max_date) for r in df.itertuples()}


def fetch_bulk(year: int, country: str = "Indonesia") -> Path | None:
    """One per-country yearly CSV.  The route for everything up to ``FIRMS_BULK_LAST_YEAR``."""
    url = config.FIRMS_BULK_URL.format(family=config.FIRMS_BULK_FAMILY, year=year,
                                       sensor=config.FIRMS_BULK_FAMILY, country=country)
    dest = RAW_BULK / f"viirs_snpp_{year}_{country}.csv"
    # min_bytes is 200, not 5000: the Singapore file is a header and a handful of rows (3.1 kB in
    # 2012) and a short-file guard tuned to Indonesia rejects it, then retries with a Range header
    # against a complete file and collects a 416.  A country with almost no fires is a fact, not a
    # truncated download.
    return util.fetch(url, dest, headers=util.browser_ua(), min_bytes=200, timeout=900)


def fetch_window(source: str, start: str, days: int):
    """One FIRMS ``area`` call (max 5 days) -> raw DataFrame, cached as a parquet part.

    Windows already on disk are skipped; windows ending within the last three days are refetched,
    because NRT keeps back-filling late granules for roughly 48 hours.
    """
    import pandas as pd
    import requests
    w, s, e, n = config.AOI
    url = config.FIRMS_AREA_URL.format(key=config.FIRMS_MAP_KEY, src=source,
                                       w=w, s=s, e=e, n=n, days=days, start=start)
    r = requests.get(url, timeout=300, headers=util.browser_ua())
    if r.status_code != 200 or r.text.lstrip().lower().startswith(("invalid", "<")):
        raise OSError(f"FIRMS area {source} {start}+{days}: {r.status_code} {r.text[:120]}")
    if not r.text.strip():
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(r.text))


def tail_windows(seam: dict) -> list[tuple[str, str, int]]:
    """(source, start, days) covering everything the bulk files do not."""
    out = []
    start = date(config.FIRMS_BULK_LAST_YEAR + 1, 1, 1)
    sp_max = datetime.strptime(seam["VIIRS_SNPP_SP"][1], "%Y-%m-%d").date()
    nrt_min = datetime.strptime(seam["VIIRS_SNPP_NRT"][0], "%Y-%m-%d").date()
    nrt_max = datetime.strptime(seam["VIIRS_SNPP_NRT"][1], "%Y-%m-%d").date()
    cur = start
    while cur <= nrt_max:
        src = config.FIRMS_SOURCES["viirs_snpp_sp"] if cur <= sp_max \
            else config.FIRMS_SOURCES["viirs_snpp_nrt"]
        days = config.FIRMS_MAX_DAYS
        # never let a window straddle the SP/NRT seam: two products, two row schemas
        if src.endswith("_SP") and cur + timedelta(days=days - 1) > sp_max:
            days = (sp_max - cur).days + 1
        if cur + timedelta(days=days - 1) > nrt_max:
            days = (nrt_max - cur).days + 1
        if days < 1:
            cur = max(cur + timedelta(days=1), nrt_min)
            continue
        out.append((src, cur.isoformat(), days))
        cur += timedelta(days=days)
    return out


def pull_tail(seam: dict, only_last: bool = False) -> None:
    RAW_WIN.mkdir(parents=True, exist_ok=True)
    wins = tail_windows(seam)
    if only_last:
        wins = wins[-2:]
    today = date.today()
    used, limit = firms_quota()
    log(f"FIRMS tail: {len(wins)} windows; key at {used}/{limit} per 10 min")
    for i, (src, start, days) in enumerate(wins):
        part = RAW_WIN / f"{src}_{start}_{days}.parquet"
        fresh = (today - (date.fromisoformat(start) + timedelta(days=days - 1))).days < 3
        if part.exists() and not fresh:
            continue
        try:
            raw = fetch_window(src, start, days)
        except Exception as exc:                            # noqa: BLE001 — resumable by design
            log(f"  window {start} [{src}]: {type(exc).__name__} {exc} — skipped, resumable")
            continue
        raw.to_parquet(part, index=False)
        if i % 25 == 0:
            log(f"  {i}/{len(wins)} {start} [{src}] {len(raw):,} rows")
        time.sleep(0.35)
        if i % 200 == 199:
            used, limit = firms_quota()
            if used > limit * 0.8:
                log(f"  quota {used}/{limit} — pausing 60 s")
                time.sleep(60)


# ── the mask ──────────────────────────────────────────────────────────────────────────
def fetch_volcanoes():
    """OSM ``natural=volcano`` nodes in the Indonesian bbox (ODbL).

    Over-includes extinct cones — that over-exclusion is quantified in G-J1 rather than hidden.
    Degrades to an empty frame with a recorded reason if every mirror refuses.
    """
    import pandas as pd
    import requests
    if VOLCANO_OUT.exists():
        return pd.read_parquet(VOLCANO_OUT)
    # Negative cache.  Four mirrors at a 240 s timeout is a sixteen-minute stall in the middle of
    # the stage every single run, and the geometric filter is the half the spec says is NOT
    # relied on.  Once a run has failed, later runs skip straight past until the marker is
    # deleted — `rm data/volcanoes_unavailable.json` is the retry.
    neg = config.DATA_DIR / "volcanoes_unavailable.json"
    if neg.exists():
        log("  volcanoes: skipped — a previous run found every Overpass mirror refusing "
            f"(delete {neg.name} to retry)")
        return pd.DataFrame(columns=["lat", "lon", "name"])
    w, s, e, n = config.BBOX_IDN
    ql = config.OVERPASS_VOLCANO_QL.format(s=s, w=w, n=n, e=e)
    for mirror in OVERPASS_MIRRORS:
        try:
            # GET with ?data= first: the main instance answers 406 to a form POST from this
            # network but serves the identical query over GET.
            r = requests.get(mirror, params={"data": ql}, timeout=75, headers=util.browser_ua())
            if r.status_code != 200:
                r = requests.post(mirror, data={"data": ql}, timeout=75,
                                  headers=util.browser_ua())
            if r.status_code != 200:
                log(f"  overpass {urllib.parse.urlparse(mirror).netloc}: {r.status_code}")
                continue
            els = r.json().get("elements", [])
            df = pd.DataFrame([{"lat": el["lat"], "lon": el["lon"],
                                "name": (el.get("tags") or {}).get("name", "")}
                               for el in els if "lat" in el])
            if len(df):
                df.to_parquet(VOLCANO_OUT, index=False)
                log(f"  volcanoes: {len(df)} OSM nodes via "
                    f"{urllib.parse.urlparse(mirror).netloc}")
                return df
        except Exception as exc:                            # noqa: BLE001
            log(f"  overpass {urllib.parse.urlparse(mirror).netloc}: "
                f"{type(exc).__name__} {exc}")
    log("  volcanoes: EVERY Overpass mirror refused — geometric filter unavailable this run; "
        "the empirical persistent-source detector still runs and is the one relied on")
    neg.write_text(json.dumps({
        "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mirrors_tried": list(OVERPASS_MIRRORS),
        "effect": "the static mask carries no volcano buffer this build; its composition is "
                  "published either way, so the page says which half of the filter ran",
        "retry": "delete this file and rerun `make fires`",
    }, indent=1))
    return pd.DataFrame(columns=["lat", "lon", "name"])


def build_static_mask(sp_archive):
    """The heart of gate G-J1, and the reason this case does not inherit Case E's NRT bug.

    Ported from ``cases/air-quality/pipeline/ingest_firms.py``: 0.01 deg cells, a cell promoted
    to "core" once the SP archive labels it type 1/2/3 in ``MASK_MIN_MONTHS`` or more distinct
    months, then buffered by its 8 neighbours.  Fed here with the COMPLETE bulk SP record rather
    than Case E's monthly sample.

    Unioned with the geometric half — OSM volcano summits buffered by
    ``config.VOLCANO_EXCLUDE_KM`` — into one mask that is then applied to EVERY product,
    including the NRT tail where the ``type`` field does not exist at all.

    Vegetation fire in Indonesia is intensely seasonal; something the archive calls a static
    source in March and again in November is a flare or a landfill.  Written to
    ``data/static_mask.parquet`` and published on the methodology page as a map, because a filter
    this consequential should be inspectable rather than asserted.
    """
    import numpy as np
    import pandas as pd

    d = sp_archive
    d = d[pd.to_numeric(d["type"], errors="coerce").isin(STATIC_TYPES)]
    log(f"  mask: {len(d):,} type-labelled static detections in the SP archive")
    if d.empty:
        return pd.DataFrame(columns=["cell", "is_core", "source"])
    d = d.assign(cell=fine_cells(d["lat"], d["lon"]),
                 ym=pd.to_datetime(d["acq_date"]).dt.strftime("%Y-%m"),
                 t=pd.to_numeric(d["type"], errors="coerce").astype("int16"))
    agg = (d.groupby("cell")
             .agg(n_det=("t", "size"), n_months=("ym", "nunique"),
                  n_t1=("t", lambda s: int((s == 1).sum())),
                  n_t2=("t", lambda s: int((s == 2).sum())),
                  n_t3=("t", lambda s: int((s == 3).sum())))
             .reset_index())
    core = agg[agg["n_months"] >= MASK_MIN_MONTHS].copy()
    log(f"  mask: {len(agg):,} static cells -> {len(core):,} core "
        f"(>= {MASK_MIN_MONTHS} distinct months)")

    ilat = core["cell"] // CELL_STRIDE
    ilon = core["cell"] - ilat * CELL_STRIDE
    buffered: dict[int, bool] = {}
    for di in range(-MASK_BUFFER_CELLS, MASK_BUFFER_CELLS + 1):
        for dj in range(-MASK_BUFFER_CELLS, MASK_BUFFER_CELLS + 1):
            for c in ((ilat + di) * CELL_STRIDE + (ilon + dj)).to_numpy():
                buffered.setdefault(int(c), False)
    for c in core["cell"].to_numpy():
        buffered[int(c)] = True
    m = pd.DataFrame({"cell": list(buffered), "is_core": list(buffered.values())})
    m["source"] = np.where(m["is_core"], "persistent_source", "persistent_buffer")

    volc = fetch_volcanoes()
    if len(volc):
        # every 0.01 deg cell centre within VOLCANO_EXCLUDE_KM of a summit
        rad_cells = int(np.ceil(config.VOLCANO_EXCLUDE_KM / 111.0 / MASK_CELL_DEG))
        vi = np.floor(volc["lat"].to_numpy() / MASK_CELL_DEG).astype("int64")
        vj = np.floor(volc["lon"].to_numpy() / MASK_CELL_DEG).astype("int64")
        vcells: dict[int, None] = {}
        for di in range(-rad_cells, rad_cells + 1):
            for dj in range(-rad_cells, rad_cells + 1):
                clat = (vi + di + 0.5) * MASK_CELL_DEG
                clon = (vj + dj + 0.5) * MASK_CELL_DEG
                keep = util.haversine_km(volc["lat"].to_numpy(), volc["lon"].to_numpy(),
                                         clat, clon) <= config.VOLCANO_EXCLUDE_KM
                for c in ((vi + di) * CELL_STRIDE + (vj + dj))[keep]:
                    vcells.setdefault(int(c), None)
        vm = pd.DataFrame({"cell": list(vcells)})
        vm["is_core"] = True
        vm["source"] = "near_volcano"
        log(f"  mask: volcano buffer adds {len(vm):,} cells "
            f"({config.VOLCANO_EXCLUDE_KM} km around {len(volc)} summits)")
        m = pd.concat([m, vm], ignore_index=True)
        m = (m.sort_values("is_core", ascending=False)
               .drop_duplicates("cell", keep="first").reset_index(drop=True))

    m["ilat"] = m["cell"] // CELL_STRIDE
    m["ilon"] = m["cell"] - m["ilat"] * CELL_STRIDE
    m["lat"] = (m["ilat"] + 0.5) * MASK_CELL_DEG
    m["lon"] = (m["ilon"] + 0.5) * MASK_CELL_DEG
    m.to_parquet(MASK_OUT, index=False)
    log(f"  mask: {len(m):,} cells total ({int(m['is_core'].sum()):,} core + "
        f"{MASK_BUFFER_CELLS}-cell buffer) -> {MASK_OUT.name}")
    return m


def clean(raw, mask_ids: set[int]):
    """Apply the confidence filter and the static mask to one frame.

    Returns ``(kept, removed)``.  ``removed`` carries ``removed_reason`` in
    {``type_volcano``, ``type_static``, ``type_offshore``, ``low_confidence``,
    ``static_mask``} so gate G-J1 publishes the composition of what was thrown away rather than
    only its size.  ``type``-derived reasons are available on SP rows only; NRT rows can only ever
    be removed by the mask or by confidence, and the reason column says so rather than implying a
    filter that did not run.
    """
    import numpy as np
    import pandas as pd
    d = raw.copy()
    d["removed_reason"] = None

    if "type" in d.columns:
        t = pd.to_numeric(d["type"], errors="coerce")
        d.loc[t == 1, "removed_reason"] = "type_volcano"
        d.loc[t == 2, "removed_reason"] = "type_static"
        d.loc[t == 3, "removed_reason"] = "type_offshore"

    conf = d["confidence"].astype(str).str.strip().str.lower()
    numeric = pd.to_numeric(conf, errors="coerce")
    low = np.where(numeric.notna(), numeric < config.MIN_CONF_MODIS,
                   conf.isin(config.DROP_CONF))
    d.loc[low & d["removed_reason"].isna(), "removed_reason"] = "low_confidence"

    inmask = pd.Series(fine_cells(d["lat"], d["lon"]), index=d.index).isin(mask_ids)
    d.loc[inmask & d["removed_reason"].isna(), "removed_reason"] = "static_mask"

    kept = d[d["removed_reason"].isna()].drop(columns=["removed_reason"])
    removed = d[d["removed_reason"].notna()]
    return kept, removed


def to_daily(kept):
    """Cell x day counts, summed FRP and night fraction — the panel input for features.py."""
    import numpy as np
    import pandas as pd
    d = kept.copy()
    d["clat"], d["clon"] = util.snap_cell(d["lat"], d["lon"])
    d["day"] = pd.to_datetime(d["acq_date"])
    d["night"] = (d["daynight"].astype(str).str.upper() == "N").astype("int8")
    g = (d.groupby(["clat", "clon", "day"], sort=False)
           .agg(n_fire=("frp", "size"), frp_sum=("frp", "sum"), frp_max=("frp", "max"),
                night_frac=("night", "mean"))
           .reset_index())
    g["cell"] = util.cell_key(g["clat"], g["clon"])
    for c in ("clat", "clon", "frp_sum", "frp_max", "night_frac"):
        g[c] = g[c].astype("float32")
    g["n_fire"] = g["n_fire"].astype("int32")
    return g


# ── normalisation ─────────────────────────────────────────────────────────────────────
KEEP = ["lat", "lon", "acq_date", "acq_time", "confidence", "frp", "daynight",
        "bright_ti4", "bright_ti5", "type", "version", "satellite"]


def normalise(df, sensor: str, product: str):
    import pandas as pd
    d = df.rename(columns={"latitude": "lat", "longitude": "lon"})
    for c in KEEP:
        if c not in d.columns:
            d[c] = pd.NA
    d = d[KEEP].copy()
    d["lat"] = pd.to_numeric(d["lat"], errors="coerce")
    d["lon"] = pd.to_numeric(d["lon"], errors="coerce")
    d["frp"] = pd.to_numeric(d["frp"], errors="coerce")
    d = d[d["lat"].notna() & d["lon"].notna()]
    w, s, e, n = config.AOI
    d = d[(d["lon"] >= w) & (d["lon"] <= e) & (d["lat"] >= s) & (d["lat"] <= n)]
    d["sensor"] = sensor
    d["product"] = product
    return d


def main() -> None:
    import numpy as np
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrt-only", action="store_true",
                    help="daily refresh: fetch only the trailing NRT window")
    args = ap.parse_args()
    util.require(bool(config.FIRMS_MAP_KEY), "FIRMS_MAP_KEY missing from repo-root .env")
    RAW_BULK.mkdir(parents=True, exist_ok=True)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    seam = availability()
    log(f"FIRMS seam: SP {seam['VIIRS_SNPP_SP']}  NRT {seam['VIIRS_SNPP_NRT']}")

    # --- bulk history -----------------------------------------------------------------
    # ** EVERY COUNTRY THE AOI TOUCHES, NOT JUST INDONESIA. **
    # The bulk route is per country; the `area` route is per bounding box.  Fetching only the
    # Indonesian bulk files would give a history that stops at the Kalimantan border and a live
    # tail that includes Sarawak — a step change at the 2025 seam that looks exactly like a
    # Malaysian fire season starting.  That is the same class of bug as the NRT `type` problem,
    # and it is avoided by pulling every bulk country the AOI overlaps and letting `normalise`
    # clip all of them to the same box.
    bulk_mb = {}
    if not args.nrt_only:
        for year in range(int(config.START[:4]), config.FIRMS_BULK_LAST_YEAR + 1):
            if not util.guard_disk(1.0):
                break
            got = 0
            for country in config.FIRMS_BULK_COUNTRIES:
                p = fetch_bulk(year, country)
                if p is None:
                    continue
                got += p.stat().st_size
            if not got:
                log(f"  bulk {year}: absent")
                continue
            bulk_mb[year] = round(got / 1e6, 1)
            log(f"  bulk {year}: {bulk_mb[year]} MB across "
                f"{len(config.FIRMS_BULK_COUNTRIES)} countries")
        util.manifest_put("firms_bulk", mb_by_year=bulk_mb,
                          countries=list(config.FIRMS_BULK_COUNTRIES))

    # --- tail -------------------------------------------------------------------------
    pull_tail(seam, only_last=args.nrt_only)

    # --- load -------------------------------------------------------------------------
    frames = []
    for p in sorted(RAW_BULK.glob("*.csv")):
        raw = pd.read_csv(p, low_memory=False)
        frames.append(normalise(raw, "VIIRS_SNPP", "SP_BULK"))
    n_bulk = sum(len(f) for f in frames)
    for p in sorted(RAW_WIN.glob("*.parquet")):
        raw = pd.read_parquet(p)
        if raw.empty:
            continue
        product = "SP_API" if "_SP_" in p.name else "NRT_API"
        frames.append(normalise(raw, "VIIRS_SNPP", product))
    util.require(bool(frames), "no FIRMS data on disk")
    allrows = pd.concat(frames, ignore_index=True)
    # the SP bulk archive and the SP API can overlap at the boundary year; dedupe on the
    # detection key rather than trusting the file layout
    before = len(allrows)
    allrows = allrows.drop_duplicates(subset=["lat", "lon", "acq_date", "acq_time"])
    log(f"loaded {before:,} rows ({n_bulk:,} bulk) -> {len(allrows):,} after dedupe")

    has_type = allrows["type"].notna()
    log(f"type field present on {has_type.mean():.1%} of rows "
        f"({(~has_type).sum():,} NRT rows have no type at all — this is the bug the mask fixes)")

    # --- mask + clean -----------------------------------------------------------------
    sp = allrows[has_type]
    mask = build_static_mask(sp)
    mask_ids = set(mask["cell"].astype("int64").tolist())
    kept, removed = clean(allrows, mask_ids)

    share = len(removed) / max(len(allrows), 1)
    comp = removed["removed_reason"].value_counts().to_dict()
    log(f"filter: kept {len(kept):,} of {len(allrows):,} — removed {len(removed):,} "
        f"({share:.2%}); composition {comp}")

    # the gate's whole point: the mask must bite on the NRT tail, where `type` does not exist
    nrt = allrows[~has_type]
    nrt_kept, nrt_removed = clean(nrt, mask_ids)
    nrt_share = len(nrt_removed) / max(len(nrt), 1)
    log(f"filter on the NRT tail alone: removed {len(nrt_removed):,} of {len(nrt):,} "
        f"({nrt_share:.2%}) — a type-only filter would have removed 0")

    kept = kept.reset_index(drop=True)
    kept["clat"], kept["clon"] = util.snap_cell(kept["lat"], kept["lon"])
    kept["cell"] = util.cell_key(kept["clat"], kept["clon"])
    kept[["lat", "lon", "acq_date", "acq_time", "confidence", "frp", "daynight",
          "sensor", "product", "cell"]].to_parquet(FIRES_OUT, index=False, compression="zstd")

    rem = removed.copy()
    rem["clat"], rem["clon"] = util.snap_cell(rem["lat"], rem["lon"])
    (rem.groupby(["clat", "clon", "removed_reason"], as_index=False)
        .agg(n=("lat", "size"), frp_sum=("frp", "sum"))
        .to_parquet(REMOVED_OUT, index=False, compression="zstd"))

    daily = to_daily(kept)
    daily.to_parquet(DAILY_OUT, index=False, compression="zstd")

    yearly = (kept.assign(y=pd.to_datetime(kept["acq_date"]).dt.year)
                  .groupby("y").size().to_dict())
    audit = {
        "rows_raw": int(len(allrows)), "rows_kept": int(len(kept)),
        "rows_removed": int(len(removed)), "removed_share": float(share),
        "removed_composition": {k: int(v) for k, v in comp.items()},
        "nrt_rows": int(len(nrt)), "nrt_removed": int(len(nrt_removed)),
        "nrt_removed_share": float(nrt_share),
        "type_present_share": float(has_type.mean()),
        "mask_cells": int(len(mask)), "mask_core_cells": int(mask["is_core"].sum()),
        "mask_sources": {k: int(v) for k, v in mask["source"].value_counts().to_dict().items()},
        "bulk_mb_by_year": bulk_mb or util.manifest_read().get("firms_bulk", {}).get("mb_by_year", {}),
        "detections_by_year": {int(k): int(v) for k, v in yearly.items()},
        "seam": {k: list(v) for k, v in seam.items() if k.startswith("VIIRS_SNPP")},
        "cells": int(daily["cell"].nunique()), "days": int(daily["day"].nunique()),
    }
    AUDIT_OUT.write_text(json.dumps(audit, indent=1))
    util.manifest_put("fires", **{k: audit[k] for k in
                                  ("rows_kept", "rows_removed", "removed_share")})
    log(f"fires: {len(kept):,} retained detections in {audit['cells']:,} cells over "
        f"{audit['days']:,} days -> {FIRES_OUT.name}")


if __name__ == "__main__":
    main()
