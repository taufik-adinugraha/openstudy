"""Stage events — stream GDELT 2.0 export CSVs, keep Indonesia, build the ledger.

  stream   walk masterfilelist (+ translation feed) 2017 -> now, newest month
           first: download each 15-min export zip -> count ALL rows (our own
           "share of everything" denominator, gate G-D1) -> keep rows where
           ActionGeo_CountryCode='ID' (FIPS) OR Actor1/2CountryCode='IDN' (ISO3)
           -> one parquet per month under data/events/ -> zip discarded.
           Resumable: a month whose parquet exists is skipped (the current
           month is always redone). ~50 GB flows through; ~1-2 GB is kept.
  ledger   DuckDB over the monthly parquet + daily_stats.csv + the DOC-API
           curves -> data/narrative_daily.parquet (config.LEDGER).
  all      stream then ledger (default)

Memory stays flat (<1 GB): at most EVENTS_WORKERS zips are in flight and a
month's kept rows (~150k) are buffered before the parquet write.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import re
import signal
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

import config

FEEDS = {"en": config.MASTERFILE, "trans": config.MASTERFILE_TRANS}
_TS_RE = re.compile(r"/(\d{14})(?:\.translation)?\.export\.CSV\.zip$")
_local = threading.local()
_stop = threading.Event()

SCHEMA = pa.schema([
    ("event_id", pa.int64()), ("day", pa.date32()), ("added_day", pa.date32()),
    ("actor1_code", pa.string()), ("actor1_name", pa.string()), ("actor1_country", pa.string()),
    ("actor1_type", pa.string()), ("actor2_code", pa.string()), ("actor2_name", pa.string()),
    ("actor2_country", pa.string()), ("actor2_type", pa.string()), ("is_root", pa.int8()),
    ("event_code", pa.string()), ("event_root", pa.string()), ("quad_class", pa.int8()),
    ("goldstein", pa.float32()), ("num_mentions", pa.int32()), ("num_sources", pa.int32()),
    ("num_articles", pa.int32()), ("avg_tone", pa.float32()), ("action_country", pa.string()),
    ("action_adm1", pa.string()), ("action_lat", pa.float32()), ("action_lon", pa.float32()),
    ("action_name", pa.string()), ("source_domain", pa.string()), ("feed", pa.string()),
])
C = config.EXPORT_COLS


def _session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update(config.BROWSER_UA)
        _local.s = s
    return s


# ── masterfile ────────────────────────────────────────────────────────────────
def load_masterfile(feed: str, max_age_h: float = 6) -> list[tuple[str, int, str]]:
    """[(ts14, size, url)] of export zips from EVENTS_START_YEAR on, cached locally."""
    config.RAW.mkdir(parents=True, exist_ok=True)
    cache = config.RAW / f"masterfilelist_{feed}.txt"
    fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_h * 3600
    if not fresh:
        print(f"[events] downloading masterfile ({feed})…", flush=True)
        with _session().get(FEEDS[feed], timeout=300, stream=True, allow_redirects=True) as r:
            r.raise_for_status()
            tmp = cache.with_suffix(".tmp")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
            tmp.replace(cache)
    out = []
    with open(cache) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) != 3 or not parts[2].endswith(".export.CSV.zip"):
                continue
            m = _TS_RE.search(parts[2])
            if not m or int(m.group(1)[:4]) < config.EVENTS_START_YEAR:
                continue
            out.append((m.group(1), int(parts[0]), parts[2]))
    return out


# ── one 15-minute file ────────────────────────────────────────────────────────
def _domain(url: str) -> str:
    try:
        host = url.split("/", 3)[2].lower()
    except IndexError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _f(v: str) -> float | None:
    try:
        return float(v)
    except ValueError:
        return None


def _i(v: str) -> int | None:
    try:
        return int(v)
    except ValueError:
        return None


def _date(v: str) -> dt.date | None:
    try:
        return dt.date(int(v[:4]), int(v[4:6]), int(v[6:8]))
    except ValueError:
        return None


def process_file(ts: str, url: str, feed: str) -> tuple[int, list[tuple], int, str]:
    """Download one export zip; return (rows_total, kept_rows, bytes, status)."""
    added = _date(ts)
    for attempt in range(4):
        if _stop.is_set():
            return 0, [], 0, "stopped"
        try:
            r = _session().get(url, timeout=90)
        except requests.RequestException:
            time.sleep(3 * 2 ** attempt)
            continue
        if r.status_code == 404:
            return 0, [], 0, "missing"          # GDELT has gaps; normal
        if r.status_code != 200:
            time.sleep(3 * 2 ** attempt)
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                text = z.read(z.namelist()[0]).decode("utf-8", "replace")
        except (zipfile.BadZipFile, IndexError):
            time.sleep(2)
            continue
        n = 0
        kept: list[tuple] = []
        for line in text.split("\n"):
            if not line:
                continue
            n += 1
            # cheap C-level prefilter, then the exact column test
            if "IDN" not in line and "\tID\t" not in line:
                continue
            f = line.rstrip("\r").split("\t")
            if len(f) < config.N_EXPORT_COLS:
                continue
            if not (f[config.COL_ACTOR1_COUNTRY] == config.ISO3_ID
                    or f[config.COL_ACTOR2_COUNTRY] == config.ISO3_ID
                    or f[config.COL_ACTION_COUNTRY] == config.FIPS_ID):
                continue
            kept.append((
                _i(f[C["event_id"]]), _date(f[C["day"]]), added,
                f[C["actor1_code"]], f[C["actor1_name"]], f[C["actor1_country"]], f[C["actor1_type"]],
                f[C["actor2_code"]], f[C["actor2_name"]], f[C["actor2_country"]], f[C["actor2_type"]],
                _i(f[C["is_root"]]), f[C["event_code"]], f[C["event_root"]], _i(f[C["quad_class"]]),
                _f(f[C["goldstein"]]), _i(f[C["num_mentions"]]), _i(f[C["num_sources"]]),
                _i(f[C["num_articles"]]), _f(f[C["avg_tone"]]), f[C["action_country"]],
                f[C["action_adm1"]], _f(f[C["action_lat"]]), _f(f[C["action_lon"]]),
                f[C["action_name"]], _domain(f[C["source_url"]]), feed,
            ))
        return n, kept, len(r.content), "ok"
    return 0, [], 0, "failed"


# ── stats sidecar (denominators) ──────────────────────────────────────────────
STATS_COLS = ["day", "feed", "files", "ok", "missing", "failed", "rows", "kept", "bytes"]


def _write_stats(month: str, rows: list[dict]) -> None:
    """Idempotent: replace the month's rows in daily_stats.csv."""
    existing = []
    if config.EVENTS_STATS.exists():
        with open(config.EVENTS_STATS) as fh:
            existing = [r for r in csv.DictReader(fh) if not r["day"].startswith(month)]
    with open(config.EVENTS_STATS.with_suffix(".tmp"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=STATS_COLS)
        w.writeheader()
        for r in sorted(existing + rows, key=lambda r: (r["day"], r["feed"])):
            w.writerow(r)
    config.EVENTS_STATS.with_suffix(".tmp").replace(config.EVENTS_STATS)


# ── stream ────────────────────────────────────────────────────────────────────
def stream(months_filter: list[str] | None, redo: bool, workers: int) -> int:
    config.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    plan: dict[str, list[tuple[str, str, int, str]]] = {}
    for feed in FEEDS:
        for ts, size, url in load_masterfile(feed):
            plan.setdefault(ts[:6], []).append((ts, feed, size, url))
    this_month = dt.datetime.now(dt.timezone.utc).strftime("%Y%m")
    months = sorted(plan, reverse=True)                 # newest first: recent years land early
    if months_filter:
        months = [m for m in months if any(m.startswith(f) for f in months_filter)]
    total_files = sum(len(plan[m]) for m in months)
    total_gb = sum(s for m in months for _, _, s, _ in plan[m]) / 1e9
    print(f"[events] plan: {len(months)} months, {total_files:,} files, {total_gb:.1f} GB compressed "
          f"(workers={workers})", flush=True)

    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    t_all = time.monotonic()
    done_files = 0
    for ym in months:
        out = config.EVENTS_DIR / f"events_{ym}.parquet"
        if out.exists() and not redo and ym != this_month:
            done_files += len(plan[ym])
            continue
        files = plan[ym]
        t0 = time.monotonic()
        cols: list[list] = [[] for _ in SCHEMA.names]
        stats: dict[tuple[str, str], dict] = {}
        n_done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(process_file, ts, url, feed): (ts, feed, size) for ts, feed, size, url in files}
            for fut in as_completed(futs):
                ts, feed, _ = futs[fut]
                n, kept, nbytes, status = fut.result()
                st = stats.setdefault((ts[:8], feed), {"day": ts[:8], "feed": feed, "files": 0, "ok": 0,
                                                       "missing": 0, "failed": 0, "rows": 0, "kept": 0, "bytes": 0})
                st["files"] += 1
                st[status if status in ("ok", "missing", "failed") else "failed"] += 1
                st["rows"] += n
                st["kept"] += len(kept)
                st["bytes"] += nbytes
                for row in kept:
                    for col, v in zip(cols, row):
                        col.append(v)
                n_done += 1
                if n_done % 1500 == 0:
                    el = time.monotonic() - t0
                    print(f"[events]   {ym}: {n_done}/{len(files)} files, {sum(len(c) for c in cols[:1])} kept, "
                          f"{n_done / el:.1f} files/s", flush=True)
                if _stop.is_set():
                    ex.shutdown(cancel_futures=True)
                    break
        if _stop.is_set():
            print(f"[events] stop requested — {ym} left incomplete (will be redone)", flush=True)
            return 1
        table = pa.table({name: pa.array(col, type=SCHEMA.field(name).type) for name, col in zip(SCHEMA.names, cols)},
                         schema=SCHEMA)
        tmp = out.with_suffix(".tmp.parquet")
        pq.write_table(table, tmp, compression="zstd")
        tmp.replace(out)
        _write_stats(ym, list(stats.values()))
        done_files += len(files)
        el = time.monotonic() - t0
        rows = sum(s["rows"] for s in stats.values())
        mb = sum(s["bytes"] for s in stats.values()) / 1e6
        fails = sum(s["failed"] for s in stats.values())
        miss = sum(s["missing"] for s in stats.values())
        eta = (total_files - done_files) / max(len(files) / el, 1e-6) / 3600
        print(f"[events] {ym}: {len(files)} files ({miss} missing, {fails} failed) {mb:.0f} MB, "
              f"{rows:,} rows -> {table.num_rows:,} kept ({100 * table.num_rows / max(rows, 1):.2f}%) "
              f"in {el / 60:.1f} min · {done_files:,}/{total_files:,} files · ETA {eta:.1f} h", flush=True)
    print(f"[events] stream complete in {(time.monotonic() - t_all) / 3600:.2f} h", flush=True)
    return 0


# ── ledger ────────────────────────────────────────────────────────────────────
def ledger() -> int:
    import duckdb
    import pandas as pd

    files = sorted(config.EVENTS_DIR.glob("events_*.parquet"))
    if not files:
        print("[events] no monthly parquet yet — ledger will carry the API curves only")
        daily = pd.DataFrame({"date": pd.to_datetime([]), "n_events": pd.Series(dtype="float64")})
    else:
        daily = _events_daily(files)
    if daily.empty and not config.CURVES.exists():
        print("[events] nothing to build a ledger from (no events, no curves)")
        return 1

    # DOC-API curves, wide
    if config.CURVES.exists():
        cur = pd.read_parquet(config.CURVES)
        single = cur[cur["mode"].isin(["TimelineVol", "TimelineVolRaw", "TimelineTone"])].copy()
        name = {"TimelineVol": "vol", "TimelineVolRaw": "vol_raw", "TimelineTone": "tone"}
        single["col"] = single["qid"].str.replace("indonesia", "api", regex=False) + "_" + single["mode"].map(name)
        w = single.pivot_table(index="date", columns="col", values="value", aggfunc="first")
        norm = single[single["mode"] == "TimelineVolRaw"].pivot_table(index="date", columns="qid", values="norm", aggfunc="first")
        if "indonesia" in norm:
            w["api_norm"] = norm["indonesia"]
        w = w.reset_index()
        lo = min([w["date"].min()] + ([daily["date"].min()] if not daily.empty else []))
        hi = max([w["date"].max()] + ([daily["date"].max()] if not daily.empty else []))
        all_days = pd.DataFrame({"date": pd.date_range(lo, hi)})
        daily = all_days.merge(daily, on="date", how="left").merge(w, on="date", how="left")
    daily = daily.sort_values("date").reset_index(drop=True)
    daily.to_parquet(config.LEDGER, index=False)
    ev_days = int(daily["n_events"].notna().sum())
    print(f"[events] ledger: {len(daily):,} days {daily['date'].min().date()} -> {daily['date'].max().date()}, "
          f"{ev_days} with events, {len(daily.columns)} columns -> {config.LEDGER.name}")
    return 0


def _events_daily(files: list[Path]) -> "pd.DataFrame":
    import duckdb
    import pandas as pd

    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'; SET threads=2;")
    glob = str(config.EVENTS_DIR / "events_*.parquet")
    root_cols = ",\n".join(f"count(*) FILTER (WHERE event_root = '{c:02d}') AS root_{c:02d}" for c in range(1, 21))
    daily = con.execute(f"""
        WITH e AS (SELECT * FROM read_parquet('{glob}', union_by_name=true))
        SELECT added_day AS date,
               count(*) AS n_events,
               count(*) FILTER (WHERE feed = 'en') AS n_en,
               count(*) FILTER (WHERE feed = 'trans') AS n_trans,
               count(*) FILTER (WHERE is_root = 1) AS n_root,
               count(*) FILTER (WHERE action_country = 'ID') AS n_in_idn,
               count(*) FILTER (WHERE quad_class = 1) AS quad_verbal_coop,
               count(*) FILTER (WHERE quad_class = 2) AS quad_material_coop,
               count(*) FILTER (WHERE quad_class = 3) AS quad_verbal_conflict,
               count(*) FILTER (WHERE quad_class = 4) AS quad_material_conflict,
               count(*) FILTER (WHERE event_root = '14') AS protest_n,
               count(*) FILTER (WHERE event_root IN ('18', '19', '20')) AS violence_n,
               avg(goldstein) AS goldstein_mean,
               sum(goldstein * num_mentions) / nullif(sum(num_mentions), 0) AS goldstein_wmean,
               avg(avg_tone) AS tone_mean,
               sum(num_mentions) AS mentions,
               {root_cols}
        FROM e GROUP BY 1 ORDER BY 1""").df()
    actors = con.execute(f"""
        WITH e AS (SELECT added_day, actor1_country AS c FROM read_parquet('{glob}', union_by_name=true)
                   UNION ALL
                   SELECT added_day, actor2_country FROM read_parquet('{glob}', union_by_name=true)),
             agg AS (SELECT added_day, c, count(*) AS n FROM e WHERE c <> '' AND c <> 'IDN' GROUP BY 1, 2),
             rk AS (SELECT *, row_number() OVER (PARTITION BY added_day ORDER BY n DESC, c) AS r FROM agg)
        SELECT added_day AS date, string_agg(c || ':' || n, ',' ORDER BY r) AS top_actors
        FROM rk WHERE r <= 5 GROUP BY 1""").df()
    con.close()
    daily["date"] = pd.to_datetime(daily["date"])
    actors["date"] = pd.to_datetime(actors["date"])
    daily = daily.merge(actors, on="date", how="left")

    # denominators from the streaming pass
    if config.EVENTS_STATS.exists():
        st = pd.read_csv(config.EVENTS_STATS, dtype={"day": str})
        st["date"] = pd.to_datetime(st["day"], format="%Y%m%d")
        wide = st.pivot_table(index="date", columns="feed", values=["rows", "files", "ok", "kept"], aggfunc="sum")
        wide.columns = [f"global_{a}_{b}" for a, b in wide.columns]
        daily = daily.merge(wide.reset_index(), on="date", how="left")
        for feed in ("en", "trans"):
            if f"global_rows_{feed}" in daily:
                daily[f"share_{feed}"] = 100 * daily[f"n_{feed}"] / daily[f"global_rows_{feed}"].replace(0, pd.NA)
        if {"global_rows_en", "global_rows_trans"} <= set(daily.columns):
            daily["share_all"] = 100 * daily["n_events"] / (daily["global_rows_en"].fillna(0) + daily["global_rows_trans"].fillna(0)).replace(0, pd.NA)
        for c in [c for c in daily.columns if c.startswith("share_")]:
            daily[c] = pd.to_numeric(daily[c], errors="coerce").astype("float64")
    return daily


def finish_chain(fetch_ok: bool = False) -> int:
    """ledger -> validate -> export, serialized across units by a file lock so the
    curves unit and the events unit can both call it without racing."""
    import fcntl
    import subprocess

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.DATA_DIR / ".chain.lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        rc = ledger()
        if rc == 0:
            here = Path(__file__).resolve().parent
            subprocess.run([sys.executable, str(here / "validate.py")], check=False)
            subprocess.run([sys.executable, str(here / "export_web.py")]
                           + ([] if fetch_ok else ["--no-fetch"]), check=False)
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", nargs="?", default="all", choices=["stream", "ledger", "all"])
    ap.add_argument("--months", nargs="*", help="YYYYMM or YYYY prefixes to (re)process")
    ap.add_argument("--redo", action="store_true", help="reprocess months whose parquet exists")
    ap.add_argument("--workers", type=int, default=config.EVENTS_WORKERS)
    a = ap.parse_args()
    rc = 0
    if a.stage in ("stream", "all"):
        rc = stream(a.months, a.redo, min(a.workers, config.EVENTS_WORKERS))
    if a.stage == "ledger":
        rc = ledger()
    elif a.stage == "all" and rc == 0:
        rc = finish_chain()          # backfill complete: gates + exports refresh themselves
    return rc


if __name__ == "__main__":
    sys.exit(main())
