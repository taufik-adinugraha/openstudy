"""Independent re-analysis for the review article.

Nothing here changes the case's published pipeline. It re-derives the case's own
headline claims from the same BACI slices under different specifications, so the
review can say what survives and what does not. Four stages, each pre-specified
before the numbers were read:

nickel   : the "fifteen-fold" processed-nickel rise, decomposed into tonnes and
           unit value, and the basket decomposed at HS6 — how much of what the
           case calls "processed nickel" contains any nickel at all.
partners : concentration on a FIXED partner panel. BACI's Indonesian partner
           roster grows from 151 to 218 reporting territories between 1995 and
           2000, and a Herfindahl index over a growing roster falls for reasons
           that have nothing to do with trade. Recomputed on partners present in
           every year, the diversification claim is retested.
gap      : where the +17.9% BACI-vs-BPS export gap sits, by chapter and by
           partner, and whether any aggregate/"nes" code carries it.
eci      : (needs the BACI archive) rebuild the world HS4 export matrix, recompute
           ECI, and measure how far apart adjacent ranks actually are — the case
           publishes a rank, and a rank is only meaningful if the underlying
           scores are separated.

Usage:  uv run python pipeline/review.py <stage>...   (or `all`)
Writes: data/review.json and web/src/data/review.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import zipfile

import duckdb

import config

SLICE = config.IDN_SLICE_DIR
GLOB = str(SLICE / "idn_*.parquet")
IDN = 360
OUT_DATA = config.DATA_DIR / "review.json"
OUT_WEB = config.CASE_DIR / "web" / "src" / "data" / "review.json"
RAW = config.DATA_DIR / "raw"
WORLD_HS4 = config.DATA_DIR / "world_hs4.parquet"

CSV_TYPES = ("{'t':'SMALLINT','i':'INTEGER','j':'INTEGER',"
             "'k':'VARCHAR','v':'DOUBLE','q':'DOUBLE'}")

# The case's own definition of the nickel chain (extracts.py).
ORE = ("2604",)
PROCESSED = ("7202", "7501", "7502", "7503", "7218", "7219", "7220")
# HS6 lines inside 7202 that contain no nickel by construction.
NON_NICKEL_7202 = ("720211", "720219", "720221", "720229", "720230", "720241",
                   "720249", "720250", "720270", "720280", "720291", "720292",
                   "720293", "720299")
HS6_LABELS = {
    "720211": "Ferro-manganese, C>2%", "720219": "Ferro-manganese, other",
    "720221": "Ferro-silicon, Si>55%", "720229": "Ferro-silicon, other",
    "720230": "Ferro-silico-manganese", "720241": "Ferro-chromium, C>4%",
    "720249": "Ferro-chromium, other", "720250": "Ferro-silico-chromium",
    "720260": "Ferro-nickel", "720270": "Ferro-molybdenum",
    "720280": "Ferro-tungsten", "720291": "Ferro-titanium",
    "720292": "Ferro-vanadium", "720293": "Ferro-niobium",
    "720299": "Ferro-alloys, other",
    "750110": "Nickel mattes", "750120": "Nickel oxide sinters",
    "750210": "Unwrought nickel, not alloyed", "750220": "Unwrought nickel alloys",
    "750300": "Nickel waste & scrap",
    "721890": "Stainless semi-finished", "721810": "Stainless ingots",
    "721899": "Stainless semi-finished, other",
    "721911": "Stainless hot-rolled coil >10mm", "721912": "Stainless hot-rolled 4.75-10mm",
    "721913": "Stainless hot-rolled 3-4.75mm", "721914": "Stainless hot-rolled <3mm",
    "721921": "Stainless hot-rolled plate >10mm", "721922": "Stainless plate 4.75-10mm",
    "721923": "Stainless plate 3-4.75mm", "721924": "Stainless plate <3mm",
    "721931": "Stainless cold-rolled >4.75mm", "721932": "Stainless cold-rolled 3-4.75mm",
    "721933": "Stainless cold-rolled 1-3mm", "721934": "Stainless cold-rolled 0.5-1mm",
    "721935": "Stainless cold-rolled <0.5mm", "721990": "Stainless flat, other",
    "722011": "Stainless narrow hot-rolled >4.75mm", "722012": "Stainless narrow hot-rolled",
    "722020": "Stainless narrow cold-rolled", "722090": "Stainless narrow, other",
    "260400": "Nickel ores & concentrates",
}

BASE = config.G_B7_BASE_YEARS          # 2013-2015
PEAK = config.G_B7_PEAK_YEARS          # 2021-2024


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET memory_limit='2500MB'")
    con.execute("SET threads=2")
    con.execute(f"SET temp_directory='{config.DATA_DIR / 'duck_tmp'}'")
    return con


def _slices(con) -> None:
    con.execute(f"CREATE OR REPLACE VIEW f AS SELECT * FROM read_parquet('{GLOB}')")


def _win(series: dict, years) -> float:
    vals = [series.get(y, 0.0) for y in years]
    return sum(vals) / len(vals) if vals else 0.0


def _load() -> dict:
    if OUT_DATA.exists():
        return json.loads(OUT_DATA.read_text())
    return {}


def _save(out: dict) -> None:
    OUT_DATA.write_text(json.dumps(out, indent=1))
    OUT_WEB.parent.mkdir(parents=True, exist_ok=True)
    OUT_WEB.write_text(json.dumps(out, indent=1))
    print(f"[review] -> {OUT_DATA} and {OUT_WEB}")


# ---------------------------------------------------------------------------
# stage: nickel — tonnes vs dollars, and what is actually nickel
# ---------------------------------------------------------------------------

def stage_nickel(out: dict) -> None:
    con = _con()
    _slices(con)
    like = lambda pfx: " OR ".join(f"k LIKE '{p}%'" for p in pfx)  # noqa: E731

    def series(pfx):
        rows = con.execute(f"""
            SELECT t, sum(v) AS v, sum(q) AS q,
                   sum(CASE WHEN q IS NULL OR q <= 0 THEN v ELSE 0 END) AS v_noq
            FROM f WHERE i={IDN} AND ({like(pfx)}) GROUP BY 1 ORDER BY 1""").fetchall()
        return {int(t): {"v": v or 0.0, "q": q or 0.0, "v_noq": vn or 0.0}
                for t, v, q, vn in rows}

    proc = series(PROCESSED)
    ore = series(ORE)

    def decomp(s, label):
        vb, vp = _win({t: r["v"] for t, r in s.items()}, BASE), _win({t: r["v"] for t, r in s.items()}, PEAK)
        qb, qp = _win({t: r["q"] for t, r in s.items()}, BASE), _win({t: r["q"] for t, r in s.items()}, PEAK)
        nb = _win({t: r["v_noq"] for t, r in s.items()}, BASE)
        np_ = _win({t: r["v_noq"] for t, r in s.items()}, PEAK)
        uvb = vb / qb if qb else float("nan")
        uvp = vp / qp if qp else float("nan")
        d = {"label": label,
             "vBase": vb, "vPeak": vp, "vRatio": vp / vb if vb else float("nan"),
             "qBase": qb, "qPeak": qp, "qRatio": qp / qb if qb else float("nan"),
             "uvBase": uvb, "uvPeak": uvp, "uvRatio": uvp / uvb if uvb else float("nan"),
             "qMissingShareBase": nb / vb if vb else 0.0,
             "qMissingSharePeak": np_ / vp if vp else 0.0}
        # log decomposition of the value ratio
        if d["vRatio"] > 0 and d["qRatio"] > 0:
            lv, lq = math.log(d["vRatio"]), math.log(d["qRatio"])
            d["shareVolume"] = lq / lv
            d["shareUnitValue"] = 1 - lq / lv
        return d

    out["nickel"] = {
        "base": list(BASE), "peak": list(PEAK),
        "processed": decomp(proc, "Processed nickel, as the case defines it"),
        "ore": decomp(ore, "Nickel ore (HS 2604)"),
        "series": {str(t): {"v": round(r["v"]), "q": round(r["q"])}
                   for t, r in sorted(proc.items())},
    }

    # --- HS6 composition of the "processed nickel" basket -------------------
    rows = con.execute(f"""
        SELECT k, sum(CASE WHEN t IN {tuple(BASE)} THEN v ELSE 0 END)/{len(BASE)} AS vb,
                  sum(CASE WHEN t IN {tuple(PEAK)} THEN v ELSE 0 END)/{len(PEAK)} AS vp
        FROM f WHERE i={IDN} AND ({like(PROCESSED)}) GROUP BY 1 ORDER BY vp DESC""").fetchall()
    comp = [{"hs6": k, "label": HS6_LABELS.get(k, f"HS {k}"), "vBase": vb, "vPeak": vp}
            for k, vb, vp in rows]
    peak_tot = sum(c["vPeak"] for c in comp)
    base_tot = sum(c["vBase"] for c in comp)
    non_ni = [c for c in comp if c["hs6"] in NON_NICKEL_7202]
    ferro_ni = [c for c in comp if c["hs6"] == "720260"]
    nickel_ch75 = [c for c in comp if c["hs6"].startswith("75")]
    stainless = [c for c in comp if c["hs6"][:4] in ("7218", "7219", "7220")]
    grp = lambda g: {"vBase": sum(c["vBase"] for c in g), "vPeak": sum(c["vPeak"] for c in g)}  # noqa: E731
    out["nickel"]["composition"] = {
        "peakTotal": peak_tot, "baseTotal": base_tot,
        "top": [{k: (round(v) if isinstance(v, float) else v) for k, v in c.items()}
                for c in comp[:12]],
        "groups": {
            "Ferro-nickel (720260)": grp(ferro_ni),
            "Nickel mattes & unwrought (HS 75)": grp(nickel_ch75),
            "Stainless steel (7218/19/20)": grp(stainless),
            "Ferro-alloys with no nickel in them": grp(non_ni),
        },
        "nonNickelPeakShare": grp(non_ni)["vPeak"] / peak_tot if peak_tot else 0.0,
        "nonNickelBaseShare": grp(non_ni)["vBase"] / base_tot if base_tot else 0.0,
    }

    # --- narrow definition: only lines that must contain nickel -------------
    narrow = con.execute(f"""
        SELECT t, sum(v), sum(q) FROM f
        WHERE i={IDN} AND (k = '720260' OR k LIKE '75%')
        GROUP BY 1 ORDER BY 1""").fetchall()
    ns = {int(t): {"v": v or 0.0, "q": q or 0.0, "v_noq": 0.0} for t, v, q in narrow}
    out["nickel"]["narrow"] = decomp(ns, "Ferro-nickel + HS 75 only")
    out["nickel"]["narrowSeries"] = {str(t): round(r["v"]) for t, r in sorted(ns.items())}

    n = out["nickel"]
    print(f"[review·nickel] value {n['processed']['vRatio']:.2f}x  "
          f"tonnes {n['processed']['qRatio']:.2f}x  unit value {n['processed']['uvRatio']:.2f}x")
    print(f"[review·nickel] volume explains {n['processed']['shareVolume']:.1%} of the log rise")
    print(f"[review·nickel] non-nickel ferro-alloys are {n['composition']['nonNickelPeakShare']:.2%} "
          f"of the peak basket")
    print(f"[review·nickel] narrow (ferro-nickel + HS75) value {n['narrow']['vRatio']:.2f}x "
          f"tonnes {n['narrow']['qRatio']:.2f}x")
    con.close()


# ---------------------------------------------------------------------------
# stage: partners — concentration on a fixed panel
# ---------------------------------------------------------------------------

def stage_partners(out: dict) -> None:
    con = _con()
    _slices(con)
    exp = con.execute(f"SELECT t, j AS p, sum(v) FROM f WHERE i={IDN} GROUP BY 1,2").fetchall()
    imp = con.execute(f"SELECT t, i AS p, sum(v) FROM f WHERE j={IDN} GROUP BY 1,2").fetchall()
    E, I = {}, {}
    for t, p, v in exp:
        E.setdefault(int(t), {})[int(p)] = v
    for t, p, v in imp:
        I.setdefault(int(t), {})[int(p)] = v
    years = sorted(E)

    def hhi(d):
        s = sum(d.values())
        return sum((v / s) ** 2 for v in d.values()) * 10_000 if s else 0.0

    def top5(d):
        s = sum(d.values())
        return sum(sorted(d.values(), reverse=True)[:5]) / s if s else 0.0

    # the panel of partners that trade in EVERY year, in each direction
    panelE = set.intersection(*[{p for p, v in E[t].items() if v > 0} for t in years])
    panelI = set.intersection(*[{p for p, v in I[t].items() if v > 0} for t in years])

    def restrict(d, panel):
        return {p: v for p, v in d.items() if p in panel}

    rows = []
    for t in years:
        fe, fi = restrict(E[t], panelE), restrict(I[t], panelI)
        rows.append({
            "year": t,
            "nExp": len([1 for v in E[t].values() if v > 0]),
            "nImp": len([1 for v in I[t].values() if v > 0]),
            "hhiExpAll": round(hhi(E[t]), 1), "hhiImpAll": round(hhi(I[t]), 1),
            "hhiExpPanel": round(hhi(fe), 1), "hhiImpPanel": round(hhi(fi), 1),
            "top5ExpAll": round(top5(E[t]), 5), "top5ExpPanel": round(top5(fe), 5),
            "coverExp": round(sum(fe.values()) / sum(E[t].values()), 5),
            "coverImp": round(sum(fi.values()) / sum(I[t].values()), 5),
        })
    first, last = rows[0], rows[-1]
    troughA = min(rows, key=lambda r: r["hhiExpAll"])
    troughP = min(rows, key=lambda r: r["hhiExpPanel"])
    out["panel"] = {
        "panelSizeExp": len(panelE), "panelSizeImp": len(panelI),
        "rows": rows,
        "fallAll": first["hhiExpAll"] - troughA["hhiExpAll"],
        "fallPanel": first["hhiExpPanel"] - troughP["hhiExpPanel"],
        "troughAll": troughA["year"], "troughPanel": troughP["year"],
        "survivingShare": ((first["hhiExpPanel"] - troughP["hhiExpPanel"])
                           / (first["hhiExpAll"] - troughA["hhiExpAll"])),
        "entryYears": [r["year"] for r in rows if r["nExp"] - rows[max(0, rows.index(r) - 1)]["nExp"] >= 15],
    }
    print(f"[review·partners] fixed panel: {len(panelE)} export partners, "
          f"{len(panelI)} import partners present every year")
    print(f"[review·partners] export HHI {first['year']} all {first['hhiExpAll']:.0f} / "
          f"panel {first['hhiExpPanel']:.0f}  ->  trough {troughP['year']} panel "
          f"{troughP['hhiExpPanel']:.0f}  ->  {last['year']} panel {last['hhiExpPanel']:.0f}")
    print(f"[review·partners] {out['panel']['survivingShare']:.1%} of the measured "
          f"diversification survives a fixed roster")
    con.close()


# ---------------------------------------------------------------------------
# stage: gap — where the BACI-vs-BPS export gap sits
# ---------------------------------------------------------------------------

def stage_gap(out: dict) -> None:
    con = _con()
    _slices(con)
    year = 2023
    tot = con.execute(f"SELECT sum(v) FROM f WHERE i={IDN} AND t={year}").fetchone()[0]
    chap = con.execute(f"""
        SELECT substr(k,1,2) AS hs2, sum(v) AS v FROM f
        WHERE i={IDN} AND t={year} GROUP BY 1 ORDER BY 2 DESC LIMIT 12""").fetchall()
    part = con.execute(f"""
        SELECT j, sum(v) AS v FROM f WHERE i={IDN} AND t={year}
        GROUP BY 1 ORDER BY 2 DESC LIMIT 12""").fetchall()
    npart = con.execute(f"SELECT count(DISTINCT j) FROM f WHERE i={IDN} AND t={year}").fetchone()[0]
    from extracts import CHAPTER_NAMES
    names = json.loads((config.CASE_DIR / "web" / "public" / "data" / "balance.json").read_text())["names"]

    baci = tot * 1e3
    bps = config.BPS_IDN_EXPORTS_2023
    excess = baci - bps
    out["gap"] = {
        "year": year, "baci": baci, "bps": bps, "comtrade": config.BENCH_IDN_EXPORTS_2023,
        "excess": excess, "deviation": (baci - bps) / bps,
        "nPartners": npart,
        "chapters": [{"hs2": h, "name": CHAPTER_NAMES.get(h, f"HS {h}"),
                      "v": round(v), "share": v / tot,
                      "excessIfProportional": v * 1e3 * (excess / baci)}
                     for h, v in chap],
        "partners": [{"code": int(p), "name": names.get(str(p), {}).get("name", f"BACI {p}"),
                      "v": round(v), "share": v / tot} for p, v in part],
        # every code above 900 in BACI is a real territory; aggregates would be
        # the thing that manufactures a gap, so this is the check
        "aggregateCodes": [int(p) for p, _ in part if int(p) >= 900],
        "byYear": [],
    }
    for y, b in sorted(config.BPS_EXPORTS.items()):
        v = con.execute(f"SELECT sum(v) FROM f WHERE i={IDN} AND t={y}").fetchone()[0] * 1e3
        out["gap"]["byYear"].append({"year": y, "baci": v, "bps": b,
                                     "deviation": (v - b) / b, "excess": v - b})
    print(f"[review·gap] {year}: BACI ${baci/1e9:.2f}B vs BPS ${bps/1e9:.2f}B, "
          f"excess ${excess/1e9:.2f}B over {npart} partners; "
          f"aggregate codes in top 12: {out['gap']['aggregateCodes'] or 'none'}")
    con.close()


# ---------------------------------------------------------------------------
# stage: eci — rebuild the world matrix and ask what a rank is worth
# ---------------------------------------------------------------------------

def build_world(release: str) -> None:
    """Aggregate the BACI archive to (year, exporter, HS4, value) without ever
    materialising the 330M-row bilateral table. One CSV on disk at a time."""
    zpath = RAW / f"BACI_HS92_V{release}.zip"
    if not zpath.exists():
        sys.exit(f"[review·eci] missing {zpath}")
    con = _con()
    parts = config.DATA_DIR / "world_parts"
    parts.mkdir(exist_ok=True)
    with zipfile.ZipFile(zpath) as z:
        members = sorted(n for n in z.namelist() if n.startswith("BACI_HS92_Y"))
        for name in members:
            year = int(name.split("_Y")[1].split("_")[0])
            dest = parts / f"w_{year}.parquet"
            if dest.exists():
                print(f"[review·eci]   {year} done — skip", flush=True)
                continue
            z.extract(name, RAW)
            csv = RAW / name
            try:
                con.execute(f"""
                    COPY (SELECT t, i AS country, substr(k,1,4) AS hs4, sum(v) AS v
                          FROM read_csv('{csv}', header=true, types={CSV_TYPES})
                          GROUP BY 1,2,3)
                    TO '{dest}' (FORMAT parquet, COMPRESSION zstd)""")
                n = con.execute(f"SELECT count(*) FROM '{dest}'").fetchone()[0]
                print(f"[review·eci]   {year}: {n:,} country-product rows", flush=True)
            finally:
                csv.unlink(missing_ok=True)
        aux = next(n for n in z.namelist() if n.startswith("country_codes"))
        with z.open(aux) as src:
            (config.DATA_DIR / "country_codes.csv").write_bytes(src.read())
    con.execute(f"""COPY (SELECT * FROM read_parquet('{parts / 'w_*.parquet'}'))
                    TO '{WORLD_HS4}' (FORMAT parquet, COMPRESSION zstd)""")
    print(f"[review·eci] world HS4 matrix -> {WORLD_HS4} "
          f"({WORLD_HS4.stat().st_size/1e6:.1f} MB)")
    con.close()


def _code_maps(con):
    iso = con.execute(f"SELECT * FROM read_csv('{config.DATA_DIR / 'country_codes.csv'}', "
                      "header=true, all_varchar=true)").df()
    code_c = next(c for c in iso.columns if "code" in c.lower() and "iso" not in c.lower())
    iso3_c = next(c for c in iso.columns if "iso" in c.lower() and "3" in c)
    code2iso = {}
    for _, r in iso.iterrows():
        try:
            code2iso[int(r[code_c])] = str(r[iso3_c])
        except (ValueError, TypeError):
            continue
    code2iso[490] = "TWN"     # BACI "Other Asia, nes"; the Atlas ranks Taiwan
    return code2iso


def _eci_from_matrix(X, codes, prods, code2iso):
    """Balassa RCA -> Mcp -> the method of reflections' eigenvector, with the
    Atlas sign convention. Returns (DataFrame, rca matrix, kept indices)."""
    import numpy as np
    import pandas as pd
    tot_c = X.sum(axis=1, keepdims=True)
    tot_p = X.sum(axis=0, keepdims=True)
    total = X.sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        rca = (X / tot_c) / (tot_p / total)
    rca = np.nan_to_num(rca)
    Mcp = (rca >= 1).astype(float)
    ok_c, ok_p = Mcp.sum(axis=1) > 0, Mcp.sum(axis=0) > 0
    M2 = Mcp[np.ix_(ok_c, ok_p)]
    cc = np.asarray(codes)[ok_c]
    kc, kp = M2.sum(axis=1), M2.sum(axis=0)
    Mt = (M2 / kc[:, None]) @ (M2 / kp[None, :]).T
    wv, v = np.linalg.eig(Mt)
    ev = v[:, np.argsort(-wv.real)[1]].real
    eci = (ev - ev.mean()) / ev.std()
    if np.corrcoef(eci, kc)[0, 1] < 0:
        eci = -eci
    d = pd.DataFrame({"country": cc, "eci": eci, "kc": kc,
                      "iso3": [code2iso.get(int(c), str(c)) for c in cc]})
    d = d.sort_values("eci", ascending=False).reset_index(drop=True)
    d["rank"] = d.index + 1
    return d, rca, ok_c, ok_p


def _matrix_for_year(con, year: int, code2iso):
    """The case's sample rule: population >= 1M and total trade >= US$1B."""
    pop = json.loads((config.DATA_DIR / "population_2023.json").read_text())[1]
    popmap = {r["countryiso3code"]: r["value"] for r in pop if r.get("value")}
    pop_ok = {c for c, i3 in code2iso.items() if popmap.get(i3, 0) >= config.MIN_POPULATION}
    pop_ok.add(490)
    df = con.execute(f"SELECT country, hs4, sum(v) AS v FROM w WHERE t={year} "
                     "GROUP BY 1,2").df()
    tot = df.groupby("country")["v"].sum()
    keep = {int(c) for c, s in tot.items() if s >= 1e6 and int(c) in pop_ok}
    df = df[df["country"].isin(keep)]
    M = df.pivot_table(index="country", columns="hs4", values="v", aggfunc="sum",
                       fill_value=0.0)
    return M


def _spearman(a, b):
    import numpy as np
    ra = np.argsort(np.argsort(-np.asarray(a))).astype(float)
    rb = np.argsort(np.argsort(-np.asarray(b))).astype(float)
    return float(np.corrcoef(ra, rb)[0, 1])


def stage_eci(out: dict) -> None:
    """Recompute ECI from the world matrix and run the two checks the case
    specified but never computed: the rank-correlation gate against the Harvard
    Atlas in every overlap year, and whether a rank is separated enough from its
    neighbours to be a statement about anything."""
    import numpy as np
    if not WORLD_HS4.exists():
        print("[review·eci] world matrix absent — run `build` first")
        return
    apath = config.DATA_DIR / "atlas_hs92.json"
    if not apath.exists():
        sys.exit("[review·eci] data/atlas_hs92.json missing (Harvard Dataverse "
                 "doi:10.7910/DVN/XTAQMC, eci_hs92 columns)")
    atlas = json.loads(apath.read_text())
    con = _con()
    con.execute(f"CREATE OR REPLACE VIEW w AS SELECT * FROM read_parquet('{WORLD_HS4}')")
    code2iso = _code_maps(con)

    ours = json.loads((config.CASE_DIR / "web" / "public" / "data"
                       / "trajectory.json").read_text())

    years = list(range(1995, 2025))
    rows, detail = [], {}
    for year in years:
        M = _matrix_for_year(con, year, code2iso)
        d, rca, ok_c, ok_p = _eci_from_matrix(M.values, list(M.index),
                                              list(M.columns), code2iso)
        r = d[d["iso3"] == "IDN"]
        rank, n = int(r["rank"].iloc[0]), len(d)
        e = d["eci"].values
        ay = atlas["eci"].get(str(year), {})
        ar = atlas["rank"].get(str(year), {})
        common = [i for i in d["iso3"] if i in ay]
        mine = [float(d.loc[d["iso3"] == i, "eci"].iloc[0]) for i in common]
        theirs = [ay[i] for i in common]
        rho = _spearman(mine, theirs)
        pub = ours["ranks"]["IDN"].get(str(year))
        pubn = ours["n"].get(str(year))
        # the like-for-like comparison: our ECI, ranked over only the economies
        # the Atlas itself ranks. Removes the sample-size difference entirely.
        sub = d[d["iso3"].isin(ar)].reset_index(drop=True)
        sub_rank = int(sub[sub["iso3"] == "IDN"].index[0]) + 1 if "IDN" in set(sub["iso3"]) else None
        rows.append({
            "year": year, "rank": rank, "n": n, "pct": rank / n,
            "eci": float(r["eci"].iloc[0]), "kc": int(r["kc"].iloc[0]),
            "spearman": rho, "nCommon": len(common),
            "atlasRank": ar.get("IDN"), "atlasN": len(ar),
            "atlasPct": (ar.get("IDN") / len(ar)) if ar.get("IDN") else None,
            "onAtlasSample": sub_rank, "onAtlasSampleN": len(sub),
            "publishedRank": pub, "publishedN": pubn,
        })
        if year in (2014, 2023, 2024):
            eci_idn = float(r["eci"].iloc[0])
            near = int(((e >= eci_idn - 0.05) & (e <= eci_idn + 0.05)).sum())
            # ECI distance spanned by a ten-place move around Indonesia
            lo, hi = max(0, rank - 6), min(n - 1, rank + 4)
            ten = float(abs(e[lo] - e[hi]))
            detail[str(year)] = {
                "sd": float(e.std()), "within005": near, "eciTenPlaces": ten,
                "top5": list(d["iso3"].head(5)),
                "neighbours": [{"iso3": str(t.iso3), "rank": int(t.rank),
                                "eci": round(float(t.eci), 4)}
                               for t in d.iloc[max(0, rank - 6):rank + 5].itertuples()],
            }
        print(f"[review·eci] {year}: IDN #{rank}/{n} ({rank/n:.1%})  published "
              f"#{pub}/{pubn}  on-Atlas-sample #{sub_rank}/{len(sub)}  "
              f"Atlas #{ar.get('IDN')}/{len(ar)}  "
              f"Spearman vs Atlas {rho:.3f} on {len(common)} economies", flush=True)

    rhos = [r["spearman"] for r in rows]
    peers = ("IDN",) + config.PEERS
    out["eci"] = {
        "rows": rows, "detail": detail,
        "atlasPeers": {c: {y: atlas["rank"][y].get(c) for y in sorted(atlas["rank"])}
                       for c in peers},
        "atlasSource": "Growth Lab, Harvard University — Growth Projections and "
                       "Complexity Rankings, doi:10.7910/DVN/XTAQMC, column eci_rank_hs92",
        "gateSpearman": config.GATE_SPEARMAN,
        "spearmanMin": min(rhos), "spearmanMax": max(rhos),
        "spearmanMean": sum(rhos) / len(rhos),
        "yearsBelowGate": [r["year"] for r in rows if r["spearman"] < config.GATE_SPEARMAN],
        "atlasBenchmarkInConfig": list(config.ATLAS_IDN_2023),
        "reproduction": {
            "corrWithPublished": _spearman(
                [r["rank"] for r in rows if r["publishedRank"]],
                [r["publishedRank"] for r in rows if r["publishedRank"]]),
            "meanAbsRankDiff": sum(abs(r["rank"] - r["publishedRank"]) for r in rows
                                   if r["publishedRank"]) / len(rows),
        },
    }
    print(f"[review·eci] G-B1 Spearman gate {config.GATE_SPEARMAN}: "
          f"min {min(rhos):.3f}, mean {sum(rhos)/len(rhos):.3f}, "
          f"{len(out['eci']['yearsBelowGate'])}/{len(rows)} years below")

    # ------------------------------------------------------------------
    # Does the failed export reconciliation touch the complexity result?
    # The case asserts RCA is a ratio of shares so a proportional level
    # difference cancels. Proportionality is the assumption; test it.
    # ------------------------------------------------------------------
    year = 2023
    M = _matrix_for_year(con, year, code2iso)
    codes = list(M.index)
    prods = list(M.columns)
    idx = codes.index(IDN)
    excess_share = out["gap"]["deviation"] / (1 + out["gap"]["deviation"])  # of BACI
    base, _, _, _ = _eci_from_matrix(M.values.copy(), codes, prods, code2iso)
    base_rank = int(base.loc[base["iso3"] == "IDN", "rank"].iloc[0])
    cf = {"baseRank": base_rank, "n": len(base),
          "excessShare": excess_share, "cases": []}

    def run(label, X):
        d, _, _, _ = _eci_from_matrix(X, codes, prods, code2iso)
        rk = int(d.loc[d["iso3"] == "IDN", "rank"].iloc[0])
        cf["cases"].append({"label": label, "rank": rk, "shift": rk - base_rank})
        print(f"[review·eci·cf] {label:<46} IDN #{rk} ({rk - base_rank:+d})", flush=True)

    X = M.values.copy()
    X[idx, :] *= (1 - excess_share)
    run("proportional: every product scaled down", X)

    hs2 = np.array([str(p)[:2] for p in prods])
    for chapters, name in ((("27",), "all of it in mineral fuels (HS 27)"),
                           (("27", "15", "26"), "spread over fuels, palm oil and ores")):
        X = M.values.copy()
        sel = np.isin(hs2, chapters)
        pool = X[idx, sel].sum()
        cut = out["gap"]["excess"] / 1e3          # USD -> kUSD
        if pool > cut:
            X[idx, sel] *= (pool - cut) / pool
            run(name, X)
        else:
            print(f"[review·eci·cf] {name}: chapter pool too small, skipped")

    # how fragile is Indonesia's Mcp row to an 18% mis-statement of a product?
    _, rca, ok_c, ok_p = _eci_from_matrix(M.values.copy(), codes, prods, code2iso)
    ridn = rca[idx, :]
    band = 1 + excess_share
    cf["mcpProducts"] = int((ridn >= 1).sum())
    cf["nearBoundary"] = int(((ridn >= 1 / band) & (ridn <= band)).sum())
    cf["nearBoundaryShare"] = cf["nearBoundary"] / max(1, cf["mcpProducts"])
    print(f"[review·eci·cf] Indonesia has {cf['mcpProducts']} products with RCA>=1; "
          f"{cf['nearBoundary']} sit within +/-{excess_share:.1%} of the RCA=1 line")
    out["eci"]["counterfactual"] = cf
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", nargs="+",
                    choices=["nickel", "partners", "gap", "build", "eci", "all"])
    ap.add_argument("--release", default=config.BACI_RELEASE)
    args = ap.parse_args()
    stages = args.stage
    if "all" in stages:
        stages = ["nickel", "partners", "gap", "eci"]
    out = _load()
    for s in stages:
        if s == "build":
            build_world(args.release)
            continue
        {"nickel": stage_nickel, "partners": stage_partners,
         "gap": stage_gap, "eci": stage_eci}[s](out)
        _save(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
