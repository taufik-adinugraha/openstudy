"""Build the region crosswalk: geoBoundaries shapeID ↔ BPS domain code.

BPS WebAPI's domain list carries every kabupaten/kota domain (4-digit codes,
provinces end in 00). geoBoundaries-2020 has 519 ADM2 shapes. Matching is by
normalized name — the Kota/Kabupaten distinction is PRESERVED (Bekasi and
Kota Bekasi are different regencies) — exact first, then fuzzy, with manual
overrides for known spelling drift. Unmatched rows are written with an empty
code and flagged for hand-repair; zonal→calibrate joins refuse unmatched ids.
"""

from __future__ import annotations

import difflib
import os
import re
import sys

import pandas as pd
import requests

import config

# geoBoundaries shapeName -> BPS domain_id, for renames fuzzy can't bridge.
MANUAL = {
    "Toba Samosir": "1206",   # renamed Toba (2020)
    "Mamuju Utara": "7605",   # renamed Pasangkayu
    "Kota Baru": "6302",      # KABUPATEN Kotabaru (S. Kalimantan) — the name
                              # starts with "Kota" but it is not a city
}
# Non-administrative polygons in geoBoundaries-2020 (lakes/reservoirs/forest):
# excluded from the crosswalk; their SOL never joins regional statistics.
EXCLUDE = {"Danau", "Danau Toba", "Hutan", "Waduk Cirata", "Wadung Kedungombo"}


def norm(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r"^(kab\.|kabupaten|kota adm\.|kota administrasi|kota)\s+", "", n)
    n = n.replace("kepulauan", "kep")
    return re.sub(r"[^a-z]", "", n)


def bps_domains() -> pd.DataFrame:
    """BPS kab/kota domains. Names omit the Kota prefix — the KIND lives in the
    code: last two digits >= 71 means Kota, else Kabupaten."""
    key = os.environ["BPS_API_KEY"]
    url = f"https://webapi.bps.go.id/v1/api/domain/type/all/key/{key}"
    resp = requests.get(url, headers=config.BPS_HEADERS, timeout=60)
    resp.raise_for_status()
    rows = resp.json()["data"][1]
    df = pd.DataFrame(rows)
    kab = df[(df["domain_id"].str.len() == 4)
             & (df["domain_id"] != "0000")
             & (~df["domain_id"].str.endswith("00"))].copy()
    kab["is_kota"] = kab["domain_id"].str[2:].astype(int) >= 71
    kab["key"] = kab["is_kota"].map({True: "kota|", False: "kab|"}) + kab["domain_name"].map(norm)
    return kab


def main() -> int:
    import geopandas as gpd

    kab = bps_domains()
    print(f"[crosswalk] BPS kabupaten/kota domains: {len(kab)}")
    gdf = gpd.read_file(config.BOUNDARIES)[[config.REGION_ID, config.REGION_NAME]]

    by_key = kab.set_index("key")
    dup = by_key.index[by_key.index.duplicated()].tolist()
    if dup:
        print(f"[crosswalk] WARNING duplicate BPS keys: {dup}")
    by_code = kab.set_index("domain_id")

    rows = []
    for _, r in gdf.iterrows():
        shape_name = r[config.REGION_NAME]
        if shape_name in EXCLUDE:
            rows.append({"shapeID": r[config.REGION_ID], "shapeName": shape_name,
                         "bps_code": "", "bps_name": "", "match": "EXCLUDED"})
            continue
        method, code, bps_name = "", "", ""
        if shape_name in MANUAL:
            hit = by_code.loc[MANUAL[shape_name]]
            method, code, bps_name = "manual", MANUAL[shape_name], hit["domain_name"]
        else:
            is_kota = bool(re.match(r"^kota\s", shape_name.lower()))
            key = ("kota|" if is_kota else "kab|") + norm(shape_name)
            if key in by_key.index:
                hit = by_key.loc[key]
                hit = hit.iloc[0] if isinstance(hit, pd.DataFrame) else hit
                method, code, bps_name = "exact", hit["domain_id"], hit["domain_name"]
            else:
                pool = by_key[by_key["is_kota"] == is_kota].index
                close = difflib.get_close_matches(key, pool, n=1, cutoff=0.8)
                if close:
                    hit = by_key.loc[close[0]]
                    hit = hit.iloc[0] if isinstance(hit, pd.DataFrame) else hit
                    method, code, bps_name = "fuzzy", hit["domain_id"], hit["domain_name"]
        rows.append({"shapeID": r[config.REGION_ID], "shapeName": shape_name,
                     "bps_code": code, "bps_name": bps_name, "match": method or "UNMATCHED"})

    out = pd.DataFrame(rows)
    dest = config.CROSSWALK.parent / "region_crosswalk.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest, index=False)

    counts = out["match"].value_counts().to_dict()
    print(f"[crosswalk] {counts} -> {dest}")
    matched = out[out["bps_code"] != ""]
    dupes = matched[matched["bps_code"].duplicated(keep=False)]
    if len(dupes):
        print("[crosswalk] ERROR duplicate BPS code assignments:")
        print(dupes[["shapeName", "bps_code", "bps_name", "match"]].to_string(index=False))
        return 1
    missing = set(bps_domains()["domain_id"]) - set(matched["bps_code"])
    if missing:
        print(f"[crosswalk] BPS domains with no shape: {sorted(missing)}")
    unmatched = out[out["match"] == "UNMATCHED"]
    if len(unmatched):
        print("[crosswalk] unmatched shapes (need MANUAL entries):")
        for name in unmatched["shapeName"]:
            print(f"    {name}")
    fuzzy = out[out["match"] == "fuzzy"]
    if len(fuzzy):
        print("[crosswalk] fuzzy matches (verify):")
        for _, r in fuzzy.iterrows():
            print(f"    {r.shapeName}  ->  {r.bps_name} ({r.bps_code})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
