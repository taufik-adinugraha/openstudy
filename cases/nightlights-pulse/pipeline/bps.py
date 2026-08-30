"""BPS WebAPI → real regional GDP (PDRB ADHK) per kabupaten/kota, the calibration target.

Discovery (2026-08-30, keyword "pdrb" on the NATIONAL domain 0000) found two
tables that already carry every kabupaten/kota, so no 514-domain crawl is
needed:

  var 2194  [Seri 2010] PDRB ADHK (2010=100) menurut Pengeluaran, Kabupaten/Kota
            ANNUAL 2016→2025 · turvar 1550 = "PDRB" (total) · vervar = 514 codes
  var 2534  PDRB Triwulanan ADHK (2010=100) menurut Lapangan Usaha, Kab/Kota
            QUARTERLY 2022→2025 · turvar 2189 = "PDRB" · turtahun 31..34 = Q1..Q4,
            35 = annual (used only as a cross-check against 2194)

The expenditure-side and production-side totals are the same PDRB, so 2194
gives the long annual series and 2534 the short quarterly one. Provincial
domains (e.g. 3200 var 101) hold the same annual numbers table-by-table and are
NOT crawled.

Codes: BPS uses post-2022 province codes for the Papua splits (92xx, 95xx-97xx)
while the boundary crosswalk (2020 vintage) carries the old 91xx/94xx codes;
the recode is resolved by name and written to data/raw/bps/code_recode.csv.

Every response is cached under data/raw/bps/<domain>/ (personal key, WAF that
needs a browser UA, informal rate limit → one request per ~0.8 s).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

import config

BASE = "https://webapi.bps.go.id/v1/api"
DOMAIN = "0000"
RAW = config.DATA_DIR / "raw" / "bps"
OUT = config.DATA_DIR / "bps_pdrb.parquet"
RECODE = RAW / "code_recode.csv"
ANNUAL = {"var": 2194, "total": 1550}                       # annual, expenditure side
QUARTERLY = {"var": 2534, "total": 2189,                     # quarterly, production side
             "turtahun": {31: 1, 32: 2, 33: 3, 34: 4, 35: 0}}
SLEEP = 0.8


def _key() -> str:
    key = os.environ.get("BPS_API_KEY", "")
    if not key:
        sys.exit("[bps] BPS_API_KEY missing — put it in the repo-root .env (the BPS 'App ID' is the key)")
    return key


def get(path: str, cache: Path, refresh: bool = False) -> dict:
    """GET {BASE}/{path}/key/… with browser UA, retry on 429/5xx, JSON cache."""
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    url = f"{BASE}/{path}/key/{_key()}"
    for attempt in range(5):
        resp = requests.get(url, headers=config.BPS_HEADERS, timeout=120)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") == "Error":
            raise RuntimeError(f"[bps] {path}: {payload.get('message')}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))
        time.sleep(SLEEP)
        return payload
    raise RuntimeError(f"[bps] {path}: gave up after retries")


def paged(path: str, cache_stem: str, refresh: bool = False) -> list[dict]:
    first = get(path, RAW / DOMAIN / f"{cache_stem}_p1.json", refresh)
    rows = list(first["data"][1])
    for page in range(2, int(first["data"][0]["pages"]) + 1):
        rows += get(f"{path}/page/{page}", RAW / DOMAIN / f"{cache_stem}_p{page}.json", refresh)["data"][1]
    return rows


def discover(keyword: str = "pdrb") -> list[dict]:
    rows = paged(f"list/model/var/domain/{DOMAIN}/keyword/{keyword}", f"var_{keyword}")
    for v in rows:
        print(f"  var {v['var_id']:>5} | vert {v['vertical']:>4} | {v['title'][:110]}")
    return rows


def years(var: int, refresh: bool = False) -> dict[int, int]:
    """th_id -> calendar year for a variable."""
    rows = paged(f"list/model/th/domain/{DOMAIN}/var/{var}", f"th_var{var}", refresh)
    return {int(t["th_id"]): int(t["th"]) for t in rows}


def fetch_year(var: int, th: int, refresh: bool = False) -> dict:
    return get(f"list/model/data/domain/{DOMAIN}/var/{var}/th/{th}",
               RAW / DOMAIN / f"data_var{var}_th{th}.json", refresh)


def parse(payload: dict, var: int, total_turvar: int, turtahun_map: dict[int, int]) -> pd.DataFrame:
    """Flatten datacontent → rows (bps_code, bps_name, year, quarter, pdrb).
    Keys are the concatenation vervar+var+turvar+th+turtahun; we rebuild them
    from the label lists instead of slicing by position."""
    content = payload["datacontent"]
    th = int(payload["tahun"][0]["val"])
    year = int(payload["tahun"][0]["label"])
    rows = []
    for vv in payload["vervar"]:
        for tt in payload["turtahun"]:
            key = f"{vv['val']}{var}{total_turvar}{th}{tt['val']}"
            if key in content:
                rows.append({"bps_code": f"{int(vv['val']):04d}", "bps_name": vv["label"].strip(),
                             "year": year, "quarter": turtahun_map[int(tt["val"])],
                             "pdrb": float(content[key]), "var_id": var})
    return pd.DataFrame(rows)


# Kabupaten whose NAME begins with "kota" — the word is part of the toponym, not a
# city marker, and BPS/geoBoundaries disagree on whether it is one word or two.
# Without this, "Kotawaringin Barat" -> "waringinbarat" but "Kota Waringin Barat"
# -> "kotawaringinbarat": two keys for one regency, and the loser drops out of the
# calibration silently. Matched before any prefix handling.
_KOTA_TOPONYMS = re.compile(r"^kota\s*(baru|waringin)\b")


def _norm(name: str) -> str:
    n = name.lower().strip()
    if _KOTA_TOPONYMS.match(n):
        # keep the whole toponym, collapse the optional space: kabupaten, not kota
        return re.sub(r"[^a-z]", "", n.replace("kepulauan", "kep"))
    kota = bool(re.match(r"^kota\b", n))
    n = re.sub(r"^(kab\.|kabupaten|kota adm\.|kota administrasi|kota)\s*", "", n)
    n = n.replace("kepulauan", "kep")
    return ("kota" if kota else "") + re.sub(r"[^a-z]", "", n)


def recode_map(bps_codes: pd.DataFrame) -> pd.DataFrame:
    """crosswalk (2020-vintage) code -> current BPS code; identity where unchanged."""
    xw = pd.read_csv(config.CROSSWALK.parent / "region_crosswalk.csv", dtype=str)
    xw = xw[xw["match"] != "EXCLUDED"]
    have = dict(zip(bps_codes["bps_code"], bps_codes["bps_name"]))
    rows, unmatched = [], []
    # A name key that maps to two different regencies would silently attach one
    # regency's PDRB to another. Fail loudly instead of calibrating on it.
    spare_pairs = [(_norm(n), c, n) for c, n in have.items() if c not in set(xw["bps_code"])]
    seen: dict[str, tuple[str, str]] = {}
    for k, c, n in spare_pairs:
        if k in seen and seen[k][0] != c:
            raise ValueError(
                f"[bps] name-key collision {k!r}: {seen[k][0]} {seen[k][1]!r} vs {c} {n!r} — "
                "two regencies would compete for one PDRB series; fix _norm before calibrating")
        seen[k] = (c, n)
    spare = {k: c for k, c, _ in spare_pairs}
    for r in xw.itertuples():
        if r.bps_code in have:
            rows.append({"xw_code": r.bps_code, "bps_code": r.bps_code, "how": "same"})
            continue
        hit = spare.get(_norm(r.bps_name))
        if hit:
            rows.append({"xw_code": r.bps_code, "bps_code": hit, "how": "renamed-province-split"})
        else:
            unmatched.append((r.bps_code, r.bps_name))
    if unmatched:
        print(f"[bps] WARNING {len(unmatched)} crosswalk codes without a BPS series: {unmatched}")
    out = pd.DataFrame(rows)
    RAW.mkdir(parents=True, exist_ok=True)
    out.to_csv(RECODE, index=False)
    print(f"[bps] recode: {(out['how'] != 'same').sum()} codes remapped by name → {RECODE.name}")
    return out


def build(refresh_latest: bool = False) -> pd.DataFrame:
    frames = []
    for spec, turtahun in ((ANNUAL, {0: 0}), (QUARTERLY, QUARTERLY["turtahun"])):
        ths = years(spec["var"], refresh_latest)
        latest = max(ths)
        for th, year in sorted(ths.items()):
            payload = fetch_year(spec["var"], th, refresh_latest and th == latest)
            df = parse(payload, spec["var"], spec["total"], turtahun)
            frames.append(df)
            print(f"[bps] var {spec['var']} {year}: {len(df)} rows, {df['bps_code'].nunique()} regencies")
    bps = pd.concat(frames, ignore_index=True)
    # the quarterly table's own annual column (turtahun 35) is a cross-check only
    ann_q = bps[(bps["var_id"] == QUARTERLY["var"]) & (bps["quarter"] == 0)]
    ann_a = bps[(bps["var_id"] == ANNUAL["var"])]
    chk = ann_q.merge(ann_a, on=["bps_code", "year"], suffixes=("_q", "_a"))
    if len(chk):
        rel = ((chk["pdrb_q"] - chk["pdrb_a"]).abs() / chk["pdrb_a"]).describe()
        print(f"[bps] cross-check annual(2534) vs annual(2194): median rel diff {rel['50%']:.2%}, max {rel['max']:.2%}")
    bps = bps[~((bps["var_id"] == QUARTERLY["var"]) & (bps["quarter"] == 0))]
    codes = bps[["bps_code", "bps_name"]].drop_duplicates("bps_code")
    rc = recode_map(codes)
    bps = bps.merge(rc[["xw_code", "bps_code"]], on="bps_code", how="left")
    bps.to_parquet(OUT, index=False)
    print(f"[bps] {len(bps)} rows → {OUT.name}: annual {ann_a['year'].min()}–{ann_a['year'].max()}, "
          f"quarterly {bps[bps['quarter'] > 0]['year'].min()}–{bps[bps['quarter'] > 0]['year'].max()}")
    return bps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--discover", action="store_true", help="list national-domain PDRB variables and exit")
    parser.add_argument("--refresh", action="store_true", help="re-fetch the latest year (BPS revises it)")
    args = parser.parse_args()
    if args.discover:
        discover()
        return 0
    build(refresh_latest=args.refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
