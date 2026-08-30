"""Stage curves — cached GDELT DOC 2.0 API timelines (spec N3 "curves").

Every response is cached as JSON under data/raw/docapi/, keyed by the exact
request parameters, so a rerun on the same day costs zero API calls. Live calls
are spaced >= config.DOCAPI_MIN_SPACING_S apart and back off exponentially on
429 / 5xx / network errors (the API is keyless and IP-rate-limited).

  battery   fetch every (query, mode) in config.QUERIES over 2017 -> today,
            plus an ArtList headline sample per validation anchor
  curves    parse the cache into data/docapi_curves.parquet (long format)
  all       battery then curves (default)

Notes verified 2026-08-30: TimelineVol is PRE-NORMALIZED (percent of all
articles GDELT monitored in the interval); TimelineVolRaw carries the raw
count plus `norm` (total monitored) per point; a 2017->now request comes back
at daily resolution. `sourcecountry:` is the publisher's country (FIPS ID).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

import config

_session = requests.Session()
_session.headers.update(config.BROWSER_UA)
_last_call = 0.0
_live_calls = 0


def _today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def window_end() -> str:
    """End of window = today 00:00 UTC — stable within a day, so cache keys are stable."""
    return _today_utc().strftime("%Y%m%d") + "000000"


def _cache_path(label: str, params: dict) -> Path:
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
    return config.DOCAPI_CACHE / f"{label}__{key}.json"


def fetch(label: str, params: dict) -> dict | None:
    """One DOC-API call, cache-first. Returns the parsed JSON (None if the API
    answered with a non-JSON message, e.g. 'no results' for an empty query)."""
    global _last_call, _live_calls
    config.DOCAPI_CACHE.mkdir(parents=True, exist_ok=True)
    path = _cache_path(label, params)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            path.unlink()
    params = {**params, "format": "json"}
    for attempt in range(config.DOCAPI_MAX_RETRIES):
        wait = config.DOCAPI_MIN_SPACING_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            r = _session.get(config.DOC_API, params=params, timeout=config.DOCAPI_TIMEOUT_S)
            _live_calls += 1
        except requests.RequestException as err:
            backoff = min(60 * 2 ** attempt, 600)
            print(f"[doc_api] {label}: network error {err.__class__.__name__}; retry in {backoff}s", flush=True)
            time.sleep(backoff)
            continue
        if r.status_code == 200:
            text = r.text.strip()
            if not text.startswith("{"):
                low = text.lower()
                if "too many" in low or "rate" in low or "quota" in low or "throttl" in low:
                    backoff = min(60 * 2 ** attempt, 900)   # throttle disguised as 200
                    print(f"[doc_api] {label}: throttle text reply; backing off {backoff}s", flush=True)
                    time.sleep(backoff)
                    continue
                # a deterministic verdict (empty/invalid query): cache it, never re-ask
                print(f"[doc_api] {label}: non-JSON reply: {text[:120]!r}", flush=True)
                path.write_text(json.dumps({"_error": text[:500], "_params": params}))
                return {"_error": text[:500]}
            try:
                js = r.json()
            except ValueError:
                print(f"[doc_api] {label}: JSON parse failure; retrying", flush=True)
                time.sleep(30)
                continue
            js["_params"] = params
            js["_fetched"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            path.write_text(json.dumps(js))
            return js
        if r.status_code in (429, 500, 502, 503, 504):
            backoff = min(60 * 2 ** attempt, 900)
            print(f"[doc_api] {label}: HTTP {r.status_code}; backing off {backoff}s", flush=True)
            time.sleep(backoff)
            continue
        print(f"[doc_api] {label}: HTTP {r.status_code}: {r.text[:200]}", flush=True)
        return None
    # skip for this pass — the battery reports it missing and the unit re-runs later
    print(f"[doc_api] {label}: still refused after {config.DOCAPI_MAX_RETRIES} attempts — skipping for now", flush=True)
    return None


def fetch_timeline(qid: str, query: str, mode: str) -> dict | None:
    """Full-window timeline; if the API downgrades resolution for a long span,
    fall back to one request per calendar year and stitch the series."""
    params = {"query": query, "mode": mode, "startdatetime": config.WINDOW_START,
              "enddatetime": window_end()}
    js = fetch(f"{qid}__{mode}", params)
    if js is None or js.get("query_details", {}).get("date_resolution", "day") == "day":
        return js
    print(f"[doc_api] {qid}/{mode}: resolution {js['query_details']['date_resolution']} — refetching per year")
    stitched: dict[str, dict] = {}
    for year in range(config.EVENTS_START_YEAR, _today_utc().year + 1):
        end = min(f"{year + 1}0101000000", window_end())
        p = {**params, "startdatetime": f"{year}0101000000", "enddatetime": end}
        part = fetch(f"{qid}__{mode}__{year}", p)
        if not part:
            continue
        for s in part.get("timeline", []):
            tgt = stitched.setdefault(s["series"], {"series": s["series"], "data": []})
            tgt["data"].extend(s.get("data", []))
    return {"query_details": js.get("query_details", {}), "timeline": list(stitched.values())}


def fetch_anchor_articles(day: str, window: int) -> dict | None:
    """Headline sample for an anchor window (ArtList caps at 250 records)."""
    start = dt.date.fromisoformat(day)
    end = start + dt.timedelta(days=window)
    params = {"query": "Indonesia", "mode": "ArtList", "maxrecords": 100, "sort": "HybridRel",
              "startdatetime": start.strftime("%Y%m%d") + "000000",
              "enddatetime": end.strftime("%Y%m%d") + "000000"}
    return fetch(f"artlist__{day}", params)


def battery() -> int:
    """Returns the number of queries still missing after this pass."""
    t0 = time.monotonic()
    n = missing = 0
    for qid, (query, modes) in config.QUERIES.items():
        for mode in modes:
            js = fetch_timeline(qid, query, mode)
            n += 1
            if js is None:            # refused this pass — retried on the next unit run
                missing += 1
                continue
            if "_error" in js:        # deterministic empty/invalid verdict — done, not missing
                print(f"[doc_api] {qid:22s} {mode:22s} EMPTY/INVALID (cached verdict)", flush=True)
                continue
            npts = sum(len(s.get("data", [])) for s in js.get("timeline", []))
            print(f"[doc_api] {qid:22s} {mode:22s} series={len(js.get('timeline', []))} points={npts}", flush=True)
    for day, spec in config.ANCHORS.items():
        js = fetch_anchor_articles(day, spec["window"])
        if js is None:
            missing += 1
            continue
        print(f"[doc_api] artlist {day}: {len(js.get('articles', []))} articles", flush=True)
    print(f"[doc_api] battery pass done: {n} timelines + {len(config.ANCHORS)} article lists, "
          f"{missing} still missing, {_live_calls} live calls, {time.monotonic() - t0:.0f}s", flush=True)
    return missing


def _parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s[:8], "%Y%m%d").date()


def parse_timeline(qid: str, mode: str, js: dict) -> list[dict]:
    rows = []
    for s in js.get("timeline", []):
        for p in s.get("data", []):
            rows.append({"qid": qid, "mode": mode, "series": s.get("series", ""),
                         "date": _parse_date(p["date"]), "value": float(p.get("value", float("nan"))),
                         "norm": float(p["norm"]) if p.get("norm") is not None else None})
    return rows


def curves() -> None:
    """Cache -> long parquet. Uses whatever the battery has fetched so far."""
    rows: list[dict] = []
    for qid, (query, modes) in config.QUERIES.items():
        for mode in modes:
            params = {"query": query, "mode": mode, "startdatetime": config.WINDOW_START,
                      "enddatetime": window_end()}
            path = _cache_path(f"{qid}__{mode}", params)
            if not path.exists():
                # fall back to the newest cached window for this qid/mode (refresh not yet run today)
                cands = sorted(config.DOCAPI_CACHE.glob(f"{qid}__{mode}__*.json"), key=lambda p: p.stat().st_mtime)
                if not cands:
                    continue
                path = cands[-1]
            js = json.loads(path.read_text())
            if "_error" in js:
                continue
            if js.get("query_details", {}).get("date_resolution", "day") != "day":
                stitched = fetch_timeline(qid, query, mode)  # cache-only if per-year files exist
                js = stitched or js
            rows.extend(parse_timeline(qid, mode, js))
    if not rows:
        print("[doc_api] no cached timelines yet — run `battery` first")
        return
    df = pd.DataFrame(rows).drop_duplicates(["qid", "mode", "series", "date"]).sort_values(["qid", "mode", "series", "date"])
    df["date"] = pd.to_datetime(df["date"])
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(config.CURVES, index=False)
    span = f"{df['date'].min().date()} -> {df['date'].max().date()}"
    print(f"[doc_api] curves: {len(df):,} rows, {df['qid'].nunique()} queries, {span} -> {config.CURVES.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", nargs="?", default="all", choices=["battery", "curves", "all"])
    a = ap.parse_args()
    missing = 0
    if a.stage in ("battery", "all"):
        missing = battery()
    if a.stage in ("curves", "all"):
        curves()
    # nonzero => a Restart=on-failure unit re-runs the pass (cache makes it cheap)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
