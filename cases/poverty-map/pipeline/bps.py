"""Ground truth — BPS WebAPI poverty series per kabupaten/kota, 2016-2025.

var 621 P0 (% poor) · 622 P1 (depth) · 623 P2 (severity) · 624 poverty line (Rp/cap/mo).
One request per (var, year); the `th` path segment is REQUIRED by the data endpoint.
BPS's WAF rejects curl-style user agents, so every request carries a browser UA
(config.BROWSER_UA). Every raw response is cached under data/raw/bps/ so a rerun is
free and the pull is reproducible.

Output: data/bps_poverty.parquet — one row per (bps_code, year) with the four measures.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

import config

RAW = config.RAW / "bps"
OUT = config.DATA_DIR / "bps_poverty.parquet"
SLEEP = 0.8


def _key() -> str:
    key = os.environ.get("BPS_API_KEY", "")
    if not key:
        sys.exit("[bps] BPS_API_KEY missing — repo-root .env (BPS 'App ID' is the key)")
    return key


def _get(path: str, cache: Path, refresh: bool = False) -> dict:
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    base = path if path.startswith("http") else f"{config.BPS_API}/{path}"
    url = f"{base}/key/{_key()}"
    last = None
    for attempt in range(5):
        try:
            resp = requests.get(url, headers=config.BROWSER_UA, timeout=120)
        except requests.RequestException as err:          # transient network
            last = err
            time.sleep(5 * (attempt + 1))
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            last = f"HTTP {resp.status_code}"
            time.sleep(5 * (attempt + 1))
            continue
        resp.raise_for_status()
        payload = resp.json()
        if str(payload.get("status")).lower() == "error":
            raise RuntimeError(f"[bps] {path}: {payload.get('message')}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))
        time.sleep(SLEEP)
        return payload
    raise RuntimeError(f"[bps] {path}: gave up after retries ({last})")


def _clean(label: str) -> str:
    return re.sub(r"<[^>]+>", "", label or "").strip()


def parse(payload: dict, var: int, measure: str) -> pd.DataFrame:
    """Flatten datacontent. Keys are vervar+var+turvar+th+turtahun concatenated; province
    header rows (2-digit vervar, <b>-wrapped label) are dropped — only kab/kota are kept."""
    content = payload.get("datacontent") or {}
    years = {int(t["val"]): int(t["label"]) for t in payload.get("tahun", [])}
    turvars = [int(t["val"]) for t in payload.get("turvar", [])] or [0]
    turtahuns = [int(t["val"]) for t in payload.get("turtahun", [])] or [0]
    rows = []
    for vv in payload.get("vervar", []):
        code = int(vv["val"])
        # BPS codes provinces as PP00 (and wraps their labels in <b>); kab/kota are PPRR, RR>0.
        if code < 1000 or code % 100 == 0:
            continue
        name = _clean(vv["label"])
        for th, year in years.items():
            val = None
            for tv in turvars:
                for tt in turtahuns:
                    val = content.get(f"{code}{var}{tv}{th}{tt}", val)
            if val is None:
                continue
            try:
                num = float(val)
            except (TypeError, ValueError):
                continue
            rows.append({"bps_code": f"{code:04d}", "bps_name": name,
                         "year": year, measure: num})
    return pd.DataFrame(rows)


def build(refresh_latest: bool = False) -> pd.DataFrame:
    frames: dict[str, list[pd.DataFrame]] = {}
    for var, measure in config.BPS_VARS.items():
        for year, th in config.BPS_YEARS.items():
            cache = RAW / f"data_var{var}_th{th}.json"
            payload = _get(config.BPS_DATA_URL.format(domain=config.BPS_DOMAIN, var=var, th=th),
                           cache, refresh=refresh_latest and year == max(config.BPS_YEARS))
            df = parse(payload, var, measure)
            frames.setdefault(measure, []).append(df)
            print(f"[bps] var {var} ({measure}) {year}: {len(df)} kab/kota rows", flush=True)

    merged: pd.DataFrame | None = None
    for measure, parts in frames.items():
        wide = pd.concat(parts, ignore_index=True)
        wide = wide.drop_duplicates(["bps_code", "year"], keep="last")
        cols = ["bps_code", "bps_name", "year", measure]
        merged = wide[cols] if merged is None else merged.merge(
            wide[["bps_code", "year", measure]], on=["bps_code", "year"], how="outer")
    assert merged is not None
    merged = merged.sort_values(["year", "bps_code"]).reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(OUT, index=False)
    per_year = merged.groupby("year")["bps_code"].nunique().to_dict()
    print(f"[bps] {len(merged)} rows, {merged['bps_code'].nunique()} regencies → {OUT.name}")
    print(f"[bps] regencies per year: {per_year}", flush=True)
    return merged


def main() -> int:
    build(refresh_latest="--refresh" in sys.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
