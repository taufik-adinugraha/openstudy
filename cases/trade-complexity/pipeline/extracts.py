"""Stages: extracts + pulse + publish (spec §B3, §B5).

extracts: view-model JSONs for the dashboard, each small enough to ship:
  treemap.json     IDN export shares by HS2 chapter per year
  trajectory.json  ECI rank per year, IDN + peer set
  nickel.json      nickel value chain: ore vs processed export values per year
  space_states.json IDN RCA>=1 product sets per year (lights the constellation)
  summary.json     headline stats for build-time rendering

pulse   : quarterly Comtrade/BPS latest-year panel — separate data plane,
          never mixed into BACI charts (TODO week 5).
publish : astro build + deploy (TODO week 5).
"""

from __future__ import annotations

import argparse
import json
import sys

import duckdb

import config

OUT = config.CASE_DIR / "web" / "public" / "data"
IDN_CODE = 360
PEER_CODES = {704: "VNM", 764: "THA", 458: "MYS", 608: "PHL", 699: "IND"}
# nickel value chain, HS92 codes (prefix match)
NICKEL_ORE = ("2604",)                     # ores & concentrates
NICKEL_PROCESSED = ("7202", "7501", "7502", "7503", "7218", "7219", "7220")
CHAPTER_NAMES = {
    "27": "Mineral fuels", "15": "Animal/veg fats (palm)", "72": "Iron & steel",
    "26": "Ores", "85": "Electrical machinery", "84": "Machinery", "40": "Rubber",
    "64": "Footwear", "48": "Paper", "44": "Wood", "62": "Apparel", "61": "Knit apparel",
    "87": "Vehicles", "38": "Chemicals", "80": "Tin", "03": "Fish", "09": "Coffee & spices",
    "71": "Gems & gold", "75": "Nickel", "29": "Org. chemicals", "39": "Plastics",
}


EMBED_API = "http://localhost:8600/api/embed"   # the lab's own embedding service (Pustaka API)


def _con():
    return duckdb.connect(str(config.DB), read_only=True)


def export_vectors(search_text: dict) -> None:
    """Semantic product search: embed every HS4's descriptions with the lab's
    local multilingual-E5 service and ship int8-quantized vectors (~0.6 MB).
    Skipped with a warning if the service is down — the UI falls back to
    lexical search."""
    import base64
    import numpy as np
    import requests

    ids = sorted(search_text)
    try:
        resp = requests.post(EMBED_API, json={"texts": [search_text[i] for i in ids],
                                              "kind": "passage"}, timeout=600)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as err:  # noqa: BLE001 — degrade gracefully
        print(f"[extracts] WARNING embedding service unavailable ({err}); "
              "product_vectors.json not written — search stays lexical")
        return
    vecs = np.asarray(payload["vectors"], dtype=np.float32)
    scale = np.abs(vecs).max(axis=1, keepdims=True) + 1e-9
    q = np.clip(np.round(vecs / scale * 127), -127, 127).astype(np.int8)
    (OUT / "product_vectors.json").write_text(json.dumps({
        "model": payload["model"], "dims": payload["dims"], "ids": ids,
        "scale": [round(float(s), 6) for s in scale[:, 0]],
        "q": base64.b64encode(q.tobytes()).decode()}))
    print(f"[extracts] product_vectors.json: {len(ids)} × {payload['dims']} int8 ({payload['model']})")


def export_views() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    con = _con()

    # --- treemap: IDN exports by HS2 per year ---
    rows = con.execute(f"""
        SELECT t, substr(k,1,2) AS hs2, sum(v) AS v
        FROM flows WHERE i = {IDN_CODE} GROUP BY 1, 2 ORDER BY 1, 3 DESC""").fetchall()
    treemap: dict = {}
    for t, hs2, v in rows:
        treemap.setdefault(str(t), []).append(
            {"hs2": hs2, "name": CHAPTER_NAMES.get(hs2, f"HS {hs2}"), "v": round(v)})
    (OUT / "treemap.json").write_text(json.dumps(treemap))

    # --- ECI rank trajectory: IDN + peers ---
    eci = con.execute("""
        WITH e AS (SELECT t, country, any_value(eci) AS eci FROM complexity GROUP BY 1,2)
        SELECT t, country, rank() OVER (PARTITION BY t ORDER BY eci DESC) AS r,
               count(*) OVER (PARTITION BY t) AS n
        FROM e""").fetchall()
    want = {IDN_CODE: "IDN", **PEER_CODES}
    traj: dict = {v: {} for v in want.values()}
    n_by_year = {}
    for t, c, r, n in eci:
        n_by_year[str(t)] = n
        if c in want:
            traj[want[c]][str(t)] = int(r)
    (OUT / "trajectory.json").write_text(json.dumps({"ranks": traj, "n": n_by_year}))

    # --- nickel chain: ore vs processed, IDN exports ---
    def series(prefixes):
        ors = " OR ".join(f"k LIKE '{p}%'" for p in prefixes)
        return dict(con.execute(f"""
            SELECT t, round(sum(v)) FROM flows
            WHERE i = {IDN_CODE} AND ({ors}) GROUP BY 1 ORDER BY 1""").fetchall())
    nickel = {"ore": series(NICKEL_ORE), "processed": series(NICKEL_PROCESSED)}
    (OUT / "nickel.json").write_text(json.dumps(
        {k: {str(t): v for t, v in s.items()} for k, s in nickel.items()}))

    # --- product-space state: IDN RCA>=1 per year ---
    states = {}
    for t, hs4 in con.execute(f"""
        SELECT t, hs4 FROM complexity WHERE country = {IDN_CODE} AND mcp = 1""").fetchall():
        states.setdefault(str(t), []).append(hs4)
    (OUT / "space_states.json").write_text(json.dumps(states))

    # --- layout passthrough ---
    if config.LAYOUT_JSON.exists():
        (OUT / "product_space.json").write_text(config.LAYOUT_JSON.read_text())

    # --- HS4 labels (first HS6 description, trimmed) + full search text ---
    prod = con.execute("SELECT * FROM products").df()
    code_col = next(c for c in prod.columns if "code" in c.lower())
    desc_col = next(c for c in prod.columns if "desc" in c.lower())
    names: dict = {}
    full: dict[str, list[str]] = {}
    for code, desc in zip(prod[code_col].astype(str).str.zfill(6), prod[desc_col]):
        names.setdefault(code[:4], str(desc).split(";")[0].split(":")[0][:60])
        full.setdefault(code[:4], []).append(str(desc))
    search_text = {h: " | ".join(dict.fromkeys(d))[:700] for h, d in full.items()}
    (OUT / "names.json").write_text(json.dumps(names))
    (OUT / "search_text.json").write_text(json.dumps(search_text))
    export_vectors(search_text)

    # --- adjacency: IDN's nearest unoccupied products, latest year ---
    latest_c = con.execute("SELECT max(t) FROM complexity").fetchone()[0]
    adj = con.execute(f"""
        SELECT c.hs4, c.density, c.pci, w.v AS world
        FROM complexity c
        JOIN (SELECT hs4, sum(v) AS v FROM exports_hs4 WHERE t={latest_c} GROUP BY 1) w
          ON w.hs4 = c.hs4
        WHERE c.t={latest_c} AND c.country={IDN_CODE} AND c.mcp = 0
        ORDER BY c.density DESC LIMIT 400""").fetchall()
    (OUT / "adjacency.json").write_text(json.dumps(
        [{"hs4": h, "density": round(float(d), 4), "pci": round(float(p), 3),
          "world": round(float(w))} for h, d, p, w in adj]))

    # --- summary for build time ---
    latest = max(int(t) for t in treemap)
    total = sum(d["v"] for d in treemap[str(latest)])
    ore23 = nickel["ore"]
    proc = nickel["processed"]
    summary = {
        "latestYear": latest,
        "totalExports": total,
        "idnRank": {t: traj["IDN"].get(t) for t in sorted(traj["IDN"])[-12:]},
        "nCountries": n_by_year.get(str(latest)),
        "oreCollapse": {str(t): ore23.get(t, 0) for t in range(2013, latest + 1)},
        "processedRise": {str(t): proc.get(t, 0) for t in range(2013, latest + 1)},
        "peers": list(PEER_CODES.values()),
    }
    (OUT / ".." / ".." / "src" / "data").mkdir(parents=True, exist_ok=True)
    (config.CASE_DIR / "web" / "src" / "data" / "summary.json").write_text(
        json.dumps(summary, indent=1))
    con.close()
    print(f"[extracts] views written to {OUT} (latest year {latest}, "
          f"IDN exports ${total/1e6:,.0f}M kUSD-scale)")


def pulse() -> None:
    raise NotImplementedError("week 5: Comtrade (<=500 calls/day) + BPS refresh")


def deploy() -> None:
    raise NotImplementedError("week 5: astro build + wrangler pages deploy")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["extracts", "pulse", "publish"])
    args = parser.parse_args()
    try:
        {"extracts": export_views, "pulse": pulse, "publish": deploy}[args.stage]()
    except NotImplementedError as todo:
        print(f"[extracts:{args.stage}] STUB — not yet implemented: {todo}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
