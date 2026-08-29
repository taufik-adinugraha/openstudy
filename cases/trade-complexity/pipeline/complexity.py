"""Stages: complexity + layout (spec §B3).

complexity: per year — RCA/MCP/ECI/PCI/density/COG via py-ecomplexity
            (Harvard Growth Lab), on the HS4 export matrix over the sample.
            PCI SIGN NORMALIZATION is explicit and tested here: machinery
            chapters (84/85/87/90) must out-rank raw chapters (01–27) on mean
            PCI; if inverted, eci and pci flip for that year (issue #1).
layout    : product-space graph for the latest year — maximum-spanning-tree
            backbone + edges phi >= PHI_THRESHOLD — force layout with fixed
            seed, cached to JSON. The browser never runs the simulation.
validate  : G-B1 slice — Indonesia's latest ECI rank within config.GATE_IDN_RANK.
"""

from __future__ import annotations

import argparse
import json
import sys

import duckdb
import numpy as np
import pandas as pd

import config

COLS = {"time": "t", "loc": "country", "prod": "hs4", "val": "v"}
MACHINERY = ("84", "85", "87", "90")
RAW_CHAPTERS = tuple(f"{c:02d}" for c in range(1, 28))


def _con():
    return duckdb.connect(str(config.DB))


def compute() -> None:
    from ecomplexity import ecomplexity

    con = _con()
    data = con.execute("SELECT t, country, hs4, v FROM exports_hs4").df()
    print(f"[complexity] panel: {len(data):,} rows, years {data.t.min()}–{data.t.max()}")
    cdf = ecomplexity(data, COLS)

    # PCI sign normalization, per year (py-ecomplexity issue #1)
    flips = []
    for t, grp in cdf.groupby("t"):
        pci = grp.drop_duplicates("hs4").set_index("hs4")["pci"]
        chap = pci.index.str[:2]
        if pci[chap.isin(MACHINERY)].mean() < pci[chap.isin(RAW_CHAPTERS)].mean():
            cdf.loc[cdf["t"] == t, ["pci", "eci"]] *= -1
            flips.append(int(t))
    print(f"[complexity] sign-flipped years: {flips or 'none'}")

    con.execute("CREATE OR REPLACE TABLE complexity AS SELECT * FROM cdf")
    con.close()
    print(f"[complexity] {len(cdf):,} country-product rows stored")


def layout() -> None:
    import networkx as nx
    from ecomplexity import proximity

    con = _con()
    latest = con.execute("SELECT max(t) FROM exports_hs4").fetchone()[0]
    data = con.execute(f"SELECT t, country, hs4, v FROM exports_hs4 WHERE t={latest}").df()
    prox = proximity(data, COLS)
    print(f"[layout] proximity pairs ({latest}): {len(prox):,} cols={list(prox.columns)}")
    # ecomplexity names the pair columns after the prod column; be agnostic.
    pair_cols = [c for c in prox.columns if c not in ("t", "time", "proximity")]
    a_col, b_col = pair_cols[0], pair_cols[1]

    g = nx.Graph()
    for a, b, w in zip(prox[a_col].astype(str), prox[b_col].astype(str), prox["proximity"]):
        if a < b and w > 0:
            g.add_edge(a, b, weight=float(w))
    mst = nx.maximum_spanning_tree(g)
    keep = nx.Graph(mst)
    for u, v, d in g.edges(data=True):
        if d["weight"] >= config.PHI_THRESHOLD:
            keep.add_edge(u, v, weight=d["weight"])
    print(f"[layout] graph: {keep.number_of_nodes()} nodes, {keep.number_of_edges()} edges")
    # 3-D force layout: the browser renders it in WebGL (spec §B5 signature moment)
    pos = nx.spring_layout(keep, dim=3, seed=config.LAYOUT_SEED, k=0.06, iterations=150,
                           weight="weight")

    world_trade = data.groupby("hs4")["v"].sum()
    pci = con.execute(f"SELECT DISTINCT hs4, pci FROM complexity WHERE t={latest}").df() \
             .set_index("hs4")["pci"]
    nodes = [{"id": p, "x": round(float(xyz[0]), 4), "y": round(float(xyz[1]), 4),
              "z": round(float(xyz[2]), 4),
              "hs2": p[:2], "trade": float(world_trade.get(p, 0)),
              "pci": round(float(pci.get(p, 0)), 3)} for p, xyz in pos.items()]
    edges = [[u, v] for u, v in keep.edges()]
    config.LAYOUT_JSON.write_text(json.dumps({"year": int(latest), "nodes": nodes, "edges": edges}))
    con.close()
    print(f"[layout] cached -> {config.LAYOUT_JSON.name}")


def validate() -> int:
    con = _con()
    eci = con.execute("""
        SELECT t, country, any_value(eci) AS eci FROM complexity GROUP BY 1, 2""").df()
    iso = con.execute("SELECT * FROM countries").df()
    code_col = next(c for c in iso.columns if "code" in c.lower())
    iso3_col = next(c for c in iso.columns if "3" in c or c.lower().endswith("iso3"))
    iso_map = dict(zip(iso[code_col].astype(int), iso[iso3_col]))
    eci["iso3"] = eci["country"].map(iso_map)
    ok = True
    for t in sorted(eci["t"].unique())[-3:]:
        yr = eci[eci["t"] == t].sort_values("eci", ascending=False).reset_index(drop=True)
        yr["rank"] = yr.index + 1
        idn = yr[yr["iso3"] == "IDN"]
        rank = int(idn["rank"].iloc[0]) if len(idn) else -1
        n = len(yr)
        pct = rank / n
        print(f"[validate] {t}: IDN ECI rank {rank}/{n} = {pct:.1%} percentile-from-top "
              f"(top 5: {', '.join(yr['iso3'].head(5).astype(str))})")
        if t == 2023:
            ref_rank, ref_n = config.ATLAS_IDN_2023
            ref_pct = ref_rank / ref_n
            in_rank = config.GATE_IDN_RANK[0] <= rank <= config.GATE_IDN_RANK[1]
            in_pct = abs(pct - ref_pct) <= config.GATE_PCT_TOLERANCE
            print(f"[validate] G-B1 vs Atlas 2023 ({ref_rank}/{ref_n} = {ref_pct:.1%}): "
                  f"rank {'OK' if in_rank else 'off'}, percentile {'OK' if in_pct else 'off'} "
                  f"(Δ {abs(pct - ref_pct):.1%}); sample size {n} vs Atlas {ref_n}")
            if not (in_rank or in_pct):
                print("[validate] G-B1 FAIL — sample differences must be diagnosed before publish")
                ok = False
    con.close()
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["compute", "layout", "validate"])
    args = parser.parse_args()
    return {"compute": lambda: (compute(), 0)[1],
            "layout": lambda: (layout(), 0)[1],
            "validate": validate}[args.stage]()


if __name__ == "__main__":
    sys.exit(main())
