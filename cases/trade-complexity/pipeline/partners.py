"""Stages: slice + facts — the partner-and-import half of the trade network.

The complexity chapters (ECI/PCI, product space) are export-only BY DEFINITION:
revealed comparative advantage is a statement about what a country *sells*, so
imports cannot enter that math. Nothing here touches them. What this module
adds is the other half of the network the case is named after — who buys
Indonesia, what Indonesia must buy, and from whom.

slice : stream the BACI zip one year at a time and keep ONLY the rows where
        Indonesia is exporter or importer, written as a compact per-year
        parquet. The full 330M-row corpus is never materialized — disk on the
        shared box is tight. Every year is skippable, so the stage is resumable
        after a kill: re-run and it picks up where it stopped.
facts : aggregate those slices into the dashboard view-models and evaluate the
        pre-registered gates G-B5 (export reconciliation), G-B6 (import
        coverage) and G-B7 (the nickel capital-goods test).

BACI columns: t year, i exporter, j importer, k HS6 (VARCHAR — leading zeros
are significant), v value in kUSD, q quantity in metric tonnes. Indonesia = 360.
Values are FOB in BOTH directions: BACI strips CIF margins off importer
declarations, which is why our import total sits below the BPS CIF headline.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile

import duckdb

import config

RAW = config.DATA_DIR / "raw"
SLICE = config.IDN_SLICE_DIR
OUT = config.CASE_DIR / "web" / "public" / "data"
IDN = 360

CSV_TYPES = ("{'t':'SMALLINT','i':'INTEGER','j':'INTEGER',"
             "'k':'VARCHAR','v':'DOUBLE','q':'DOUBLE'}")


def _con() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB, deliberately throttled: this box is shared with four
    other jobs and the unit caps us at 3 GB."""
    con = duckdb.connect()
    con.execute("SET memory_limit='1800MB'")
    con.execute("SET threads=2")
    con.execute(f"SET temp_directory='{config.DATA_DIR / 'duck_tmp'}'")
    return con


# --------------------------------------------------------------------------
# slice
# --------------------------------------------------------------------------

def slice_years(release: str) -> None:
    zpath = RAW / f"BACI_HS92_V{release}.zip"
    if not zpath.exists():
        sys.exit(f"[partners] missing {zpath} — run pipeline/fetch_baci.sh first")
    SLICE.mkdir(parents=True, exist_ok=True)
    con = _con()

    with zipfile.ZipFile(zpath) as z:
        members = sorted(n for n in z.namelist() if n.startswith("BACI_HS92_Y"))
        print(f"[partners] {len(members)} year files in {zpath.name}", flush=True)

        # country lookup ships in the same zip — extract once, keep it
        aux = next(n for n in z.namelist() if n.startswith("country_codes"))
        if not (config.DATA_DIR / "country_codes.csv").exists():
            with z.open(aux) as src:
                (config.DATA_DIR / "country_codes.csv").write_bytes(src.read())
            print(f"[partners] country lookup: {aux}", flush=True)

        for name in members:
            year = int(name.split("_Y")[1].split("_")[0])
            out = SLICE / f"idn_{year}.parquet"
            if out.exists():                       # resumable: skip done years
                print(f"[partners]   {year} already sliced — skip", flush=True)
                continue
            z.extract(name, RAW)
            csv = RAW / name
            try:
                # world total in the same visit (cheap context stat), then the
                # Indonesia slice; the CSV is deleted before the next year so
                # peak disk stays at one year's worth.
                world = con.execute(
                    f"SELECT sum(v) FROM read_csv('{csv}', header=true, "
                    f"types={CSV_TYPES})").fetchone()[0]
                con.execute(f"""
                    COPY (SELECT t, i, j, k, v, q
                          FROM read_csv('{csv}', header=true, types={CSV_TYPES})
                          WHERE i = {IDN} OR j = {IDN})
                    TO '{out}' (FORMAT parquet, COMPRESSION zstd)""")
                n = con.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
                (SLICE / f"world_{year}.json").write_text(
                    json.dumps({"year": year, "world_kusd": world}))
                # v is kUSD, so /1e6 lands in billions of USD
                print(f"[partners]   {year}: {n:,} IDN rows kept "
                      f"(world trade ${world/1e6:,.0f}B)", flush=True)
            finally:
                csv.unlink(missing_ok=True)
    con.close()
    total = sum(p.stat().st_size for p in SLICE.glob("*.parquet"))
    print(f"[partners] slice complete: {total/1e6:.1f} MB retained in {SLICE}")


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------

# Import-demand groupings: the politically live question is not "which HS2"
# but "what kind of dependence". Chapters are mapped to the four buckets the
# owner named, plus a remainder.
DEMAND_GROUPS = [
    ("Capital goods & machinery", "#5D8FBF", ("84", "85", "86", "87", "88", "89", "90")),
    ("Chemicals & plastics", "#7D6F9C", ("28", "29", "30", "31", "32", "33", "34",
                                         "35", "36", "37", "38", "39", "40")),
    ("Food & agriculture", "#7E9A6C", ("01", "02", "03", "04", "05", "06", "07", "08",
                                       "09", "10", "11", "12", "13", "14", "15", "16",
                                       "17", "18", "19", "20", "21", "22", "23", "24")),
    ("Fuels & minerals", "#6E7887", ("25", "26", "27")),
]


def _group_of(hs2: str) -> str:
    for label, _c, chapters in DEMAND_GROUPS:
        if hs2 in chapters:
            return label
    return "Everything else"


def _countries(con) -> dict[int, dict]:
    path = config.DATA_DIR / "country_codes.csv"
    if not path.exists():
        sys.exit("[partners] country_codes.csv missing — run the slice stage")
    df = con.execute(f"SELECT * FROM read_csv('{path}', header=true, "
                     "all_varchar=true)").df()
    cols = {c.lower(): c for c in df.columns}
    code_c = next(c for k, c in cols.items() if "code" in k and "iso" not in k)
    name_c = next(c for k, c in cols.items() if "name" in k)
    iso_c = next((c for k, c in cols.items() if "iso" in k and "3" in k), None)
    out = {}
    for _, r in df.iterrows():
        try:
            code = int(r[code_c])
        except (ValueError, TypeError):
            continue
        out[code] = {"name": str(r[name_c]),
                     "iso3": str(r[iso_c]) if iso_c else str(code)}
    return out


def _fmt_pct(x: float) -> str:
    return f"{x*100:+.2f}%"


def build_facts() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    con = _con()
    files = sorted(SLICE.glob("idn_*.parquet"))
    if not files:
        sys.exit("[partners] no slices — run the slice stage first")
    glob = str(SLICE / "idn_*.parquet")
    con.execute(f"CREATE VIEW f AS SELECT * FROM read_parquet('{glob}')")
    years = [r[0] for r in con.execute(
        "SELECT DISTINCT t FROM f ORDER BY t").fetchall()]
    print(f"[partners] {len(files)} year slices, {years[0]}–{years[-1]}", flush=True)
    names = _countries(con)

    def nm(code: int) -> dict:
        return names.get(code, {"name": f"BACI {code}", "iso3": str(code)})

    # ---- partner totals, both directions -----------------------------------
    exp = con.execute(f"SELECT t, j AS p, sum(v) AS v FROM f WHERE i={IDN} "
                      "GROUP BY 1,2").fetchall()
    imp = con.execute(f"SELECT t, i AS p, sum(v) AS v FROM f WHERE j={IDN} "
                      "GROUP BY 1,2").fetchall()
    exp_by, imp_by = {}, {}
    for t, p, v in exp:
        exp_by.setdefault(t, {})[p] = v
    for t, p, v in imp:
        imp_by.setdefault(t, {})[p] = v

    tot_exp = {t: sum(d.values()) for t, d in exp_by.items()}
    tot_imp = {t: sum(d.values()) for t, d in imp_by.items()}

    def hhi(d: dict) -> float:
        s = sum(d.values())
        return sum((v / s) ** 2 for v in d.values()) * 10_000 if s else 0.0

    def topn_share(d: dict, n: int) -> float:
        s = sum(d.values())
        return sum(sorted(d.values(), reverse=True)[:n]) / s if s else 0.0

    # every partner that ever cracks the top 12 in either direction gets a
    # named band; the rest are pooled, so the stack stays readable.
    featured: set[int] = set()
    for t in years:
        for d in (exp_by.get(t, {}), imp_by.get(t, {})):
            featured.update(sorted(d, key=d.get, reverse=True)[:12])
    featured = sorted(featured,
                      key=lambda p: -(exp_by.get(years[-1], {}).get(p, 0)
                                      + imp_by.get(years[-1], {}).get(p, 0)))

    partners = {
        "years": years,
        "partners": [{"code": p, **nm(p)} for p in featured],
        "exp": {str(p): [round(exp_by.get(t, {}).get(p, 0)) for t in years]
                for p in featured},
        "imp": {str(p): [round(imp_by.get(t, {}).get(p, 0)) for t in years]
                for p in featured},
        "totalExp": [round(tot_exp.get(t, 0)) for t in years],
        "totalImp": [round(tot_imp.get(t, 0)) for t in years],
        "nPartnersExp": [len(exp_by.get(t, {})) for t in years],
        "nPartnersImp": [len(imp_by.get(t, {})) for t in years],
        "hhiExp": [round(hhi(exp_by.get(t, {})), 1) for t in years],
        "hhiImp": [round(hhi(imp_by.get(t, {})), 1) for t in years],
        "top5Exp": [round(topn_share(exp_by.get(t, {}), 5), 5) for t in years],
        "top5Imp": [round(topn_share(imp_by.get(t, {}), 5), 5) for t in years],
    }
    (OUT / "partners.json").write_text(json.dumps(partners))

    # ---- trade balance by partner, per year --------------------------------
    balance = {}
    for t in years:
        e, i = exp_by.get(t, {}), imp_by.get(t, {})
        rows = []
        for p in set(e) | set(i):
            ev, iv = e.get(p, 0), i.get(p, 0)
            rows.append({"c": p, "e": round(ev), "i": round(iv)})
        rows.sort(key=lambda r: -(r["e"] + r["i"]))
        balance[str(t)] = rows[:30]
    (OUT / "balance.json").write_text(json.dumps({
        "years": years, "rows": balance,
        "names": {str(p): nm(p) for p in
                  {r["c"] for yr in balance.values() for r in yr}}}))

    # ---- imports by chapter and by demand group ----------------------------
    imp_hs2 = con.execute(f"""
        SELECT t, substr(k,1,2) AS hs2, sum(v) AS v FROM f
        WHERE j={IDN} GROUP BY 1,2 ORDER BY 1,3 DESC""").fetchall()
    from extracts import CHAPTER_NAMES
    by_year: dict[str, list] = {}
    groups: dict[str, dict[str, float]] = {}
    for t, hs2, v in imp_hs2:
        by_year.setdefault(str(t), []).append(
            {"hs2": hs2, "name": CHAPTER_NAMES.get(hs2, f"HS {hs2}"), "v": round(v)})
        g = _group_of(hs2)
        groups.setdefault(str(t), {}).setdefault(g, 0.0)
        groups[str(t)][g] += v
    latest = years[-1]
    imp_hs4 = con.execute(f"""
        SELECT substr(k,1,4) AS hs4, sum(v) AS v FROM f
        WHERE j={IDN} AND t={latest} GROUP BY 1 ORDER BY 2 DESC LIMIT 25""").fetchall()
    # who supplies the top chapters, latest year — the "dependence has an
    # address" panel
    supply = con.execute(f"""
        SELECT substr(k,1,2) AS hs2, i AS p, sum(v) AS v FROM f
        WHERE j={IDN} AND t={latest} GROUP BY 1,2""").fetchall()
    sup: dict[str, list] = {}
    for hs2, p, v in supply:
        sup.setdefault(hs2, []).append({"c": p, "v": round(v)})
    for hs2 in sup:
        sup[hs2] = sorted(sup[hs2], key=lambda r: -r["v"])[:6]
    (OUT / "imports.json").write_text(json.dumps({
        "years": years, "byYear": by_year,
        "groups": {"order": [g[0] for g in DEMAND_GROUPS] + ["Everything else"],
                   "colors": {g[0]: g[1] for g in DEMAND_GROUPS}
                             | {"Everything else": "#6B7280"},
                   "series": groups},
        "topHs4": [{"hs4": h, "v": round(v)} for h, v in imp_hs4],
        "suppliers": sup,
        "names": {str(p): nm(p) for p in
                  {r["c"] for v in sup.values() for r in v}},
        "latest": latest}))

    # ---- G-B7: the nickel capital-goods test -------------------------------
    caps = " OR ".join(f"substr(k,1,2)='{c}'" for c in config.G_B7_CAPGOODS)
    capgoods = dict(con.execute(f"""
        SELECT t, sum(v) FROM f WHERE j={IDN} AND ({caps})
        GROUP BY 1 ORDER BY 1""").fetchall())
    inputs = {}
    for code in config.G_B7_INPUTS:
        inputs[code] = dict(con.execute(f"""
            SELECT t, sum(v) FROM f WHERE j={IDN} AND k LIKE '{code}%'
            GROUP BY 1 ORDER BY 1""").fetchall())

    def window(series: dict, yrs) -> float:
        vals = [series.get(y, 0) for y in yrs]
        return sum(vals) / len(vals) if vals else 0.0

    base_y, peak_y = config.G_B7_BASE_YEARS, config.G_B7_PEAK_YEARS

    def growth(series: dict) -> float:
        b = window(series, base_y)
        return (window(series, peak_y) - b) / b if b else float("nan")

    cap_g = growth(capgoods)
    tot_g = growth(tot_imp)
    h1 = cap_g >= config.G_B7_H1_CAPGOODS_GROWTH
    h2 = (cap_g - tot_g) >= config.G_B7_H2_EXCESS_POINTS
    input_g = {c: growth(s) for c, s in inputs.items()}
    h3_hits = [c for c, g in input_g.items() if g >= config.G_B7_H3_INPUT_GROWTH]
    h3 = bool(h3_hits)
    passed = sum([h1, h2, h3])
    verdict = "PASS" if passed == 3 else "PARTIAL" if passed == 2 else "FAIL"

    nickel_test = {
        "base": list(base_y), "peak": list(peak_y),
        "capgoods": {str(t): round(v) for t, v in sorted(capgoods.items())},
        "totalImports": {str(t): round(v) for t, v in sorted(tot_imp.items())},
        "inputs": {c: {"label": config.G_B7_INPUTS[c],
                       "series": {str(t): round(v) for t, v in sorted(s.items())},
                       "growth": None if input_g[c] != input_g[c] else round(input_g[c], 4)}
                   for c, s in inputs.items()},
        "capGrowth": round(cap_g, 4), "totalGrowth": round(tot_g, 4),
        "excess": round(cap_g - tot_g, 4),
        "thresholds": {"h1": config.G_B7_H1_CAPGOODS_GROWTH,
                       "h2": config.G_B7_H2_EXCESS_POINTS,
                       "h3": config.G_B7_H3_INPUT_GROWTH},
        "h1": h1, "h2": h2, "h3": h3, "h3hits": h3_hits, "verdict": verdict,
    }

    # ---- G-B5 / G-B6 reconciliation ---------------------------------------
    baci_exp_2023 = tot_exp.get(2023, 0) * 1e3      # kUSD -> USD
    baci_imp_2023 = tot_imp.get(2023, 0) * 1e3
    dev_exp = (baci_exp_2023 - config.BENCH_IDN_EXPORTS_2023) / config.BENCH_IDN_EXPORTS_2023
    dev_bps = (baci_exp_2023 - config.BPS_IDN_EXPORTS_2023) / config.BPS_IDN_EXPORTS_2023
    dev_imp = (baci_imp_2023 - config.BPS_IDN_IMPORTS_2023) / config.BPS_IDN_IMPORTS_2023
    g5 = abs(dev_exp) <= config.GATE_G_B5_TOLERANCE
    g6 = abs(dev_imp) <= config.GATE_G_B6_TOLERANCE

    # G-B5 failed on 2023; show whether that is a one-year artefact or a stable
    # methodology gap by repeating the comparison on every year BPS publishes.
    series = []
    for y, bps in sorted(config.BPS_EXPORTS.items()):
        baci = tot_exp.get(y, 0) * 1e3
        series.append({"year": y, "baci": baci, "bps": bps,
                       "deviation": round((baci - bps) / bps, 5)})

    gates = {
        "G-B5": {"name": "Export reconciliation vs UN Comtrade 2023",
                 "baci": baci_exp_2023, "benchmark": config.BENCH_IDN_EXPORTS_2023,
                 "bps": config.BPS_IDN_EXPORTS_2023,
                 "deviation": round(dev_exp, 5), "deviationBps": round(dev_bps, 5),
                 "tolerance": config.GATE_G_B5_TOLERANCE, "pass": g5,
                 "series": series},
        "G-B6": {"name": "Import coverage vs BPS 2023 (CIF)",
                 "baci": baci_imp_2023, "benchmark": config.BPS_IDN_IMPORTS_2023,
                 "deviation": round(dev_imp, 5),
                 "tolerance": config.GATE_G_B6_TOLERANCE, "pass": g6},
        "G-B7": {"name": "Nickel capital-goods import test",
                 "verdict": verdict, "pass": verdict == "PASS",
                 "h1": h1, "h2": h2, "h3": h3},
    }
    (OUT / "nickel_inputs.json").write_text(json.dumps(nickel_test))
    (OUT / "gates_partners.json").write_text(json.dumps(gates))

    # ---- headline numbers for build-time rendering -------------------------
    lastyr = years[-1]
    e_last = exp_by.get(lastyr, {})
    i_last = imp_by.get(lastyr, {})
    top_exp = sorted(e_last, key=e_last.get, reverse=True)[:5]
    top_imp = sorted(i_last, key=i_last.get, reverse=True)[:5]
    e23 = exp_by.get(2023, {})
    stats = {
        "latest": lastyr,
        "totalExp": tot_exp.get(lastyr), "totalImp": tot_imp.get(lastyr),
        "balance": tot_exp.get(lastyr, 0) - tot_imp.get(lastyr, 0),
        "topExp": [{**nm(p), "v": round(e_last[p]),
                    "share": round(e_last[p] / tot_exp[lastyr], 4)} for p in top_exp],
        "topImp": [{**nm(p), "v": round(i_last[p]),
                    "share": round(i_last[p] / tot_imp[lastyr], 4)} for p in top_imp],
        "chinaShare": {
            str(y): round(exp_by.get(y, {}).get(156, 0) / tot_exp[y], 5)
            for y in years if tot_exp.get(y)},
        "chinaImpShare": {
            str(y): round(imp_by.get(y, {}).get(156, 0) / tot_imp[y], 5)
            for y in years if tot_imp.get(y)},
        "china2023": round(e23.get(156, 0) / tot_exp[2023], 5) if tot_exp.get(2023) else None,
        "hhiFirst": partners["hhiExp"][0], "hhiLast": partners["hhiExp"][-1],
        "gates": gates, "nickel": {"verdict": verdict, "capGrowth": round(cap_g, 4),
                                   "totalGrowth": round(tot_g, 4)},
    }
    # Narrative statistics. The concentration story turned out to be U-shaped
    # rather than monotonic, so the trough is a first-class number, not prose.
    hx, t5 = partners["hhiExp"], partners["top5Exp"]
    imin = min(range(len(hx)), key=lambda i: hx[i])
    e_first = exp_by.get(years[0], {})
    top_first = max(e_first, key=e_first.get) if e_first else None
    stats["concentration"] = {
        "firstYear": years[0], "hhiFirst": hx[0], "top5First": t5[0],
        "troughYear": years[imin], "hhiTrough": hx[imin], "top5Trough": t5[imin],
        "lastYear": years[-1], "hhiLast": hx[-1], "top5Last": t5[-1],
        "hhiImpFirst": partners["hhiImp"][0], "hhiImpLast": partners["hhiImp"][-1],
        "top5ImpLast": partners["top5Imp"][-1],
        "biggestFirst": ({**nm(top_first),
                          "share": round(e_first[top_first] / tot_exp[years[0]], 4)}
                         if top_first else None),
    }
    biggest_input = max(input_g, key=lambda c: input_g[c] if input_g[c] == input_g[c] else -9)
    stats["nickel"].update({
        "biggestInput": {"code": biggest_input,
                         "label": config.G_B7_INPUTS[biggest_input],
                         "growth": round(input_g[biggest_input], 4)},
        "h1": h1, "h2": h2, "h3": h3, "h3hits": h3_hits,
    })
    config.PARTNER_STATS.write_text(json.dumps(stats, indent=1))
    (config.CASE_DIR / "web" / "src" / "data" / "partner_summary.json").write_text(
        json.dumps(stats, indent=1))

    # ---- console report ----------------------------------------------------
    print("\n=== GATES (thresholds fixed before any result was read) ===")
    print(f"G-B5 export reconciliation : BACI 2023 ${baci_exp_2023/1e9:.2f}B vs "
          f"Comtrade ${config.BENCH_IDN_EXPORTS_2023/1e9:.1f}B "
          f"({_fmt_pct(dev_exp)}), vs BPS ${config.BPS_IDN_EXPORTS_2023/1e9:.2f}B "
          f"({_fmt_pct(dev_bps)}) -> {'PASS' if g5 else 'FAIL'} "
          f"(tol ±{config.GATE_G_B5_TOLERANCE:.0%})")
    for row in series:
        print(f"     {row['year']}: BACI ${row['baci']/1e9:.2f}B vs BPS "
              f"${row['bps']/1e9:.2f}B ({_fmt_pct(row['deviation'])})")
    print(f"G-B6 import coverage       : BACI 2023 ${baci_imp_2023/1e9:.2f}B vs "
          f"BPS ${config.BPS_IDN_IMPORTS_2023/1e9:.2f}B CIF "
          f"({_fmt_pct(dev_imp)}) -> {'PASS' if g6 else 'FAIL'} "
          f"(tol ±{config.GATE_G_B6_TOLERANCE:.0%})")
    print(f"G-B7 nickel capital goods  : {verdict}")
    print(f"   H1 HS84+85 growth {cap_g:+.1%} >= {config.G_B7_H1_CAPGOODS_GROWTH:.0%}"
          f" -> {h1}")
    print(f"   H2 excess over total imports {cap_g - tot_g:+.1%} "
          f"(total {tot_g:+.1%}) >= {config.G_B7_H2_EXCESS_POINTS:.0%} -> {h2}")
    print(f"   H3 any input >= {config.G_B7_H3_INPUT_GROWTH:.0%}: {h3} {h3_hits}")
    for c, g in sorted(input_g.items(), key=lambda kv: -(kv[1] if kv[1] == kv[1] else -9)):
        print(f"      {c} {config.G_B7_INPUTS[c]:<30} {g:+.1%}"
              if g == g else f"      {c} {config.G_B7_INPUTS[c]:<30} n/a")
    print("\n=== PARTNERS ===")
    print(f"{lastyr} top export partners: " + ", ".join(
        f"{nm(p)['name']} {e_last[p]/tot_exp[lastyr]:.1%}" for p in top_exp))
    print(f"{lastyr} top import partners: " + ", ".join(
        f"{nm(p)['name']} {i_last[p]/tot_imp[lastyr]:.1%}" for p in top_imp))
    print(f"export HHI {years[0]} {partners['hhiExp'][0]:.0f} -> "
          f"{lastyr} {partners['hhiExp'][-1]:.0f}; "
          f"top-5 share {partners['top5Exp'][0]:.1%} -> {partners['top5Exp'][-1]:.1%}")
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["slice", "facts"])
    ap.add_argument("--release", default=config.BACI_RELEASE)
    args = ap.parse_args()
    if args.stage == "slice":
        slice_years(args.release)
    else:
        build_facts()
    return 0


if __name__ == "__main__":
    sys.exit(main())
