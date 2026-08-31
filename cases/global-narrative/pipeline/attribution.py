"""Attribution — who is actually speaking, and did the tone rise come from them?

Two questions the dashboard cannot answer from the API curves alone, both settled
here against the event archive already on disk (8.05 M Indonesia rows, 2017 → now):

  A · WHOSE NARRATIVE.  Every kept event carries the domain that published it.
      Collapsing sub-domains to their site (nasional.kompas.com -> kompas.com) and
      classifying sites by where the publisher is based tells us what share of "the
      world's news about Indonesia" is Indonesia's own press — and how concentrated
      it is.

  B · THE WARMING.  Mean tone rises ~0.9 points across the decade in BOTH the API
      curve and the event layer. Three things could produce that: the same outlets
      writing more warmly (within), GDELT reading the surviving outlets in different
      proportions (between), or GDELT dropping outlets and picking up new ones
      (turnover). The Griliches-Regev decomposition separates them exactly:

        ΔT = Σ_C w̄_g Δt_g + Σ_C Δw_g (t̄_g − T̄)
             + Σ_E w_g^B (t_g^B − T̄) − Σ_X w_g^A (t_g^A − T̄)

      where C are sites present in both years, E entrants, X exits. The four terms
      sum to the observed change with no residual; the split is the answer.

Reads only local parquet — no API calls, so it can run while the curve fetcher is
still working. Writes data/attribution.json and web/src/data/attribution.json.
"""

from __future__ import annotations

import json
import math
import sys

import duckdb

import config

OUT = config.DATA_DIR / "attribution.json"
WEB = config.CASE_DIR / "web" / "src" / "data" / "attribution.json"
GLOB = str(config.EVENTS_DIR / "events_*.parquet")

# Multi-label public suffixes we must keep whole so `republika.co.id` does not
# collapse to `co.id`. Everything else is treated as a single-label suffix.
TWO_LABEL = {
    "co.id", "or.id", "go.id", "ac.id", "web.id", "net.id", "my.id", "sch.id", "desa.id",
    "co.uk", "org.uk", "com.au", "net.au", "org.au", "com.my", "net.my", "org.my",
    "com.sg", "com.ph", "net.ph", "com.pk", "com.tr", "com.bn", "com.vn", "com.cn",
    "com.hk", "com.tw", "co.nz", "org.nz", "co.jp", "co.kr", "co.th", "com.br",
    "co.za", "com.mx", "com.ar", "com.eg", "com.sa", "com.ng", "com.kh", "com.fj",
    "com.pg", "co.in", "net.in", "org.in", "com.bd", "com.lb", "com.qa", "com.kw",
}

# Sites based in Indonesia, largest first. Curated from the 400 busiest domains in
# the archive (which carry 78% of all rows); the list is published so it can be
# audited. A site NOT on this list is Indonesian only if it ends in `.id`.
ID_SITES = {
    "antaranews.com", "liputan6.com", "tribunnews.com", "cnnindonesia.com",
    "thejakartapost.com", "republika.co.id", "tempo.co", "merdeka.com", "kompas.com",
    "viva.co.id", "jpnn.com", "beritasatu.com", "okezone.com", "bisnis.com",
    "mediaindonesia.com", "jawapos.com", "beritajatim.com", "rri.co.id",
    "pikiran-rakyat.com", "koran-jakarta.com", "metrotvnews.com", "sindonews.com",
    "elshinta.com", "solopos.com", "lampost.co", "krjogja.com", "medanbisnisdaily.com",
    "waspada.co.id", "harianterbit.com", "inilah.com", "poskotanews.com",
    "harianjogja.com", "lensaindonesia.com", "kontan.co.id", "sumutpos.co",
    "dnaberita.com", "netralnews.com", "balidiscovery.com", "harianandalas.com",
    "analisadaily.com", "sumeks.co.id", "radartegal.com", "bengkuluekspress.com",
    "inilahkoran.com", "nowjakarta.co.id", "tabloidbintang.com", "bolaindo.com",
    "thepresidentpost.com", "kapanlagi.com", "gatra.com", "news24xx.com",
    "batampos.co.id", "rimanews.com", "korankaltim.com", "harianrakyatbengkulu.com",
    "manadopostonline.com", "malang-post.com", "hariansinggalang.co.id",
    "indonesiatribune.com", "voi.co.id", "ihram.co.id", "kompas.id", "antarajateng.com",
    "radarsemarang.com", "jakartaglobe.id", "voi.co.id",
    "indonesia-investments.com", "seputarpapua.com", "suara.com", "detik.com",
    "kumparan.com", "tirto.id", "katadata.co.id", "idntimes.com", "bareksa.com",
    "wartaekonomi.co.id", "investor.id", "cnbcindonesia.com", "grid.id", "sindonews.co",
    "jitunews.com", "tribun-timur.com", "bali-travelnews.com", "theindonesia.id",
    "kabarbisnis.com", "swa.co.id", "validnews.id", "beritagar.id", "alinea.id",
    "law-justice.co", "jurnas.com", "koranperdjoeangan.com", "riaupos.co", "haluan.co",
    "padek.co", "sumbarprov.go.id", "dpr.go.id", "setneg.go.id", "kemlu.go.id",
}
# Foreign outlets that publish heavily in Indonesian or from Indonesia but are
# owned and edited abroad — kept OUT of the Indonesian set on purpose.
FOREIGN_IN_ID_LANG = {"voaindonesia.com", "mongabay.co.id", "benarnews.org", "dw.com",
                      "bbc.com", "rfa.org", "ucanews.com"}
STATE_SITES = {"antaranews.com", "rri.co.id"}   # ANTARA (state wire) + RRI (state radio)


def site_of(domain: str) -> str:
    d = (domain or "").lower().strip().lstrip(".")
    if not d or "." not in d:
        return d
    parts = d.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_LABEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def origin(site: str) -> str:
    if site in FOREIGN_IN_ID_LANG:
        return "foreign"
    if site in ID_SITES or site.endswith(".id"):
        return "indonesia"
    return "foreign"


def gr_decompose(a: dict, b: dict) -> dict:
    """Griliches-Regev decomposition of a weighted mean between two periods.
    a, b: site -> (n, mean_tone). Returns four terms that sum exactly to ΔT."""
    na = sum(v[0] for v in a.values()); nb = sum(v[0] for v in b.values())
    wa = {g: v[0] / na for g, v in a.items()}
    wb = {g: v[0] / nb for g, v in b.items()}
    Ta = sum(wa[g] * a[g][1] for g in a)
    Tb = sum(wb[g] * b[g][1] for g in b)
    Tbar = (Ta + Tb) / 2
    C = set(a) & set(b); E = set(b) - set(a); X = set(a) - set(b)
    within = sum(((wa[g] + wb[g]) / 2) * (b[g][1] - a[g][1]) for g in C)
    between = sum((wb[g] - wa[g]) * (((a[g][1] + b[g][1]) / 2) - Tbar) for g in C)
    entry = sum(wb[g] * (b[g][1] - Tbar) for g in E)
    exit_ = -sum(wa[g] * (a[g][1] - Tbar) for g in X)
    return {"T_a": Ta, "T_b": Tb, "delta": Tb - Ta, "within": within, "between": between,
            "entry": entry, "exit": exit_, "residual": (Tb - Ta) - (within + between + entry + exit_),
            "n_common": len(C), "n_entry": len(E), "n_exit": len(X),
            "rows_a": int(na), "rows_b": int(nb),
            "common_share_a": sum(wa[g] for g in C), "common_share_b": sum(wb[g] for g in C)}


def ols(x, y):
    n = len(x); mx = sum(x) / n; my = sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    syy = sum((b - my) ** 2 for b in y)
    b1 = sxy / sxx
    r = sxy / math.sqrt(sxx * syy) if sxx and syy else 0.0
    return {"b": b1, "a": my - b1 * mx, "r": r, "r2": r * r, "n": n}


def main() -> int:
    if not list(config.EVENTS_DIR.glob("events_*.parquet")):
        print("[attribution] no event parquet — nothing to do")
        return 1
    con = duckdb.connect()
    con.execute("SET memory_limit='4GB'; SET threads=2;")
    con.create_function("site_of", site_of, ["VARCHAR"], "VARCHAR")
    con.execute(f"""CREATE VIEW ev AS
        SELECT site_of(source_domain) AS site, *
        FROM read_parquet('{GLOB}', union_by_name=true) WHERE source_domain <> ''""")

    out: dict = {}

    # ── A · whose narrative ───────────────────────────────────────────────────
    sites = con.execute("""SELECT site, count(*) n, avg(avg_tone) tone,
        count(*) FILTER (WHERE feed='trans') n_trans,
        min(added_day) d_first, max(added_day) d_last FROM ev GROUP BY 1 ORDER BY n DESC""").fetchall()
    total = sum(s[1] for s in sites)
    def rec(s):
        return {"site": s[0], "n": int(s[1]), "share": s[1] / total, "tone": round(s[2], 4),
                "trans_share": round(s[3] / s[1], 4), "origin": origin(s[0]),
                "first": str(s[4]), "last": str(s[5])}
    out["publishers"] = {
        "n_sites": len(sites), "n_rows": int(total),
        "top": [rec(s) for s in sites[:25]],
        "top10_share": sum(s[1] for s in sites[:10]) / total,
        "top25_share": sum(s[1] for s in sites[:25]) / total,
        "top100_share": sum(s[1] for s in sites[:100]) / total,
        "hhi": sum((s[1] / total) ** 2 for s in sites),
        "indonesian_in_top25": sum(1 for s in sites[:25] if origin(s[0]) == "indonesia"),
        "state_share": sum(s[1] for s in sites if s[0] in STATE_SITES) / total,
        "curated_list_size": len(ID_SITES),
        "curated_covers": sum(s[1] for s in sites if s[0] in ID_SITES) / total,
    }

    byyear = con.execute("""SELECT year(added_day) y, site, count(*) n, avg(avg_tone) t
        FROM ev GROUP BY 1,2""").fetchall()
    yr: dict[int, dict[str, tuple]] = {}
    for y, s, n, t in byyear:
        yr.setdefault(int(y), {})[s] = (int(n), float(t) if t is not None else 0.0)
    years = sorted(yr)
    origin_rows = []
    for y in years:
        d = yr[y]; n = sum(v[0] for v in d.values())
        idn = sum(v[0] for g, v in d.items() if origin(g) == "indonesia")
        tid = sum(v[0] * v[1] for g, v in d.items() if origin(g) == "indonesia") / max(idn, 1)
        nf = n - idn
        tf = sum(v[0] * v[1] for g, v in d.items() if origin(g) != "indonesia") / max(nf, 1)
        origin_rows.append({"year": y, "n": n, "indonesia": idn, "foreign": nf,
                            "indonesia_share": idn / n, "tone_indonesia": round(tid, 4),
                            "tone_foreign": round(tf, 4),
                            "tone_all": round(sum(v[0] * v[1] for v in d.values()) / n, 4),
                            "n_sites": len(d)})
    out["origin_by_year"] = origin_rows

    # ── B · the warming: within outlets, between them, or turnover? ───────────
    a, b = yr[years[0]], yr[years[-1]]
    out["tone_decomp"] = {"year_a": years[0], "year_b": years[-1], **gr_decompose(a, b)}
    # same decomposition restricted to each origin class, and year-on-year
    out["tone_decomp_by_origin"] = {
        k: gr_decompose({g: v for g, v in a.items() if origin(g) == k},
                        {g: v for g, v in b.items() if origin(g) == k})
        for k in ("indonesia", "foreign")}
    out["tone_decomp_path"] = [
        {"year": y, **{k: v for k, v in gr_decompose(yr[years[0]], yr[y]).items()
                       if k in ("delta", "within", "between", "entry", "exit")}}
        for y in years[1:]]

    # the biggest continuously-present publishers, their own tone by year
    common_all = set.intersection(*(set(yr[y]) for y in years))
    big = sorted(common_all, key=lambda g: -sum(yr[y].get(g, (0, 0))[0] for y in years))[:10]
    out["stable_publishers"] = [
        {"site": g, "origin": origin(g), "n": sum(yr[y][g][0] for y in years),
         "tone": [round(yr[y][g][1], 4) for y in years],
         "share": [round(yr[y][g][0] / sum(v[0] for v in yr[y].values()), 5) for y in years]}
        for g in big]
    out["stable_publishers_share"] = [
        sum(yr[y][g][0] for g in common_all) / sum(v[0] for v in yr[y].values()) for y in years]
    out["years"] = years

    # ── C · survivorship of the source pool ──────────────────────────────────
    surv = con.execute("""WITH d AS (SELECT site, min(added_day) f, max(added_day) l, count(*) n
        FROM ev GROUP BY 1)
        SELECT count(*) FILTER (WHERE f < '2018-01-01') a,
               count(*) FILTER (WHERE f < '2018-01-01' AND l >= '2026-01-01') b,
               count(*) FILTER (WHERE f >= '2025-01-01') c, count(*) d FROM d""").fetchone()
    out["survivorship"] = {"present_2017": int(surv[0]), "still_present_2026": int(surv[1]),
                           "new_since_2025": int(surv[2]), "sites_total": int(surv[3]),
                           "survival_rate": surv[1] / surv[0]}

    # ── D · geocoding honesty ────────────────────────────────────────────────
    g = con.execute("""SELECT count(*) FILTER (WHERE action_country='ID') in_id,
        count(*) FILTER (WHERE action_country='ID' AND action_adm1='ID') centroid,
        count(*) FILTER (WHERE action_country<>'ID') outside, count(*) tot FROM ev""").fetchone()
    out["geocoding"] = {"in_indonesia": int(g[0]), "country_centroid_only": int(g[1]),
                        "located": int(g[0] - g[1]), "outside_indonesia": int(g[2]),
                        "total": int(g[3]), "centroid_share": g[1] / g[0],
                        "outside_share": g[2] / g[3]}

    # ── E · an attention series built only from foreign publishers ───────────
    rows = con.execute("SELECT added_day, site, count(*) n FROM ev GROUP BY 1,2").fetchall()
    dn: dict[str, list[int]] = {}
    for day, s, n in rows:
        k = str(day); e = dn.setdefault(k, [0, 0])
        e[1] += n
        if origin(s) == "foreign":
            e[0] += n
    import pandas as pd
    led = pd.read_parquet(config.LEDGER)
    led["date"] = pd.to_datetime(led["date"]).dt.strftime("%Y-%m-%d")
    led = led.set_index("date")
    zero = pd.Series(0.0, index=led.index)
    denom = (led["global_rows_en"] if "global_rows_en" in led else zero).fillna(0) + \
            (led["global_rows_trans"] if "global_rows_trans" in led else zero).fillna(0)
    ser = []
    for k in sorted(dn):
        if k not in denom.index or not denom[k]:
            continue
        api = led["api_vol"].get(k)
        ser.append({"d": k, "for": dn[k][0] / denom[k] * 100, "all": dn[k][1] / denom[k] * 100,
                    "api": None if api is None or (isinstance(api, float) and math.isnan(api)) else float(api)})
    mo: dict[str, list] = {}
    for s in ser:
        mo.setdefault(s["d"][:7], []).append(s)
    months = sorted(mo)
    mser = [{"m": m,
             "for": sum(x["for"] for x in mo[m]) / len(mo[m]),
             "all": sum(x["all"] for x in mo[m]) / len(mo[m]),
             "api": (lambda v: sum(v) / len(v) if v else None)([x["api"] for x in mo[m] if x["api"] is not None])}
            for m in months]
    ok = [x for x in mser if x["api"] is not None]
    out["foreign_pulse"] = {
        "months": [x["m"] for x in mser],
        "foreign": [round(x["for"], 5) for x in mser],
        "all": [round(x["all"], 5) for x in mser],
        "api": [None if x["api"] is None else round(x["api"], 5) for x in mser],
        "r_all_api": round(ols([x["all"] for x in ok], [x["api"] for x in ok])["r"], 4),
        "r_foreign_api": round(ols([x["for"] for x in ok], [x["api"] for x in ok])["r"], 4),
        "by_year": [],
    }
    yb: dict[int, list] = {}
    for x in mser:
        yb.setdefault(int(x["m"][:4]), []).append(x)
    for y in sorted(yb):
        v = yb[y]
        out["foreign_pulse"]["by_year"].append({
            "year": y, "foreign": round(sum(k["for"] for k in v) / len(v), 5),
            "all": round(sum(k["all"] for k in v) / len(v), 5),
            "foreign_share": round(sum(k["for"] for k in v) / sum(k["all"] for k in v), 4)})

    # ── F · re-run the anchor test on foreign publishers only ────────────────
    # The case's headline signal is "share of the world's news". If an anchor only
    # fires when Indonesia's own press is counted, it is not a global signal.
    import datetime as dt
    day_index = {s["d"]: i for i, s in enumerate(ser)}
    def med(vals):
        v = sorted(x for x in vals if x is not None)
        return v[len(v) // 2] if v else None
    anchors = []
    stats = json.loads(config.STATS.read_text()) if config.STATS.exists() else {}
    published = {a["day"]: a for a in stats.get("gates", {}).get("G-D2", {}).get("anchors", [])}
    for day, spec in config.ANCHORS.items():
        if day not in day_index:
            continue
        i = day_index[day]; w = spec["window"]
        win = ser[i:i + w]
        base = ser[max(0, i - config.BASELINE_DAYS):i]
        row = {"day": day, "label": spec["label"], "window": w}
        for key, lab in (("for", "foreign"), ("all", "all")):
            bm = med([x[key] for x in base])
            pk = max(x[key] for x in win)
            pkd = max(win, key=lambda x: x[key])["d"]
            row[lab] = {"peak": round(pk, 5), "baseline": round(bm, 5),
                        "ratio": round(pk / bm, 3) if bm else None, "peak_day": pkd}
        row["indonesian"] = {"ratio": round(
            (row["all"]["peak"] - row["foreign"]["peak"]) / max(row["all"]["baseline"] - row["foreign"]["baseline"], 1e-9), 3)}
        pub = published.get(day, {}).get("signatures", {}).get("attention", {})
        row["published_api_ratio"] = pub.get("ratio")
        row["published_verdict"] = published.get(day, {}).get("verdict")
        row["dow"] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.date.fromisoformat(
            row["all"]["peak_day"]).weekday()]
        anchors.append(row)
    out["anchor_foreign"] = anchors

    con.close()
    OUT.write_text(json.dumps(out, indent=1, allow_nan=False))
    WEB.parent.mkdir(parents=True, exist_ok=True)
    WEB.write_text(json.dumps(out, indent=1, allow_nan=False))

    p = out["publishers"]; t = out["tone_decomp"]
    print(f"[attribution] {p['n_sites']:,} sites · top-10 hold {p['top10_share']*100:.1f}% · "
          f"state wire {p['state_share']*100:.1f}% · {p['indonesian_in_top25']}/25 biggest are Indonesian")
    print(f"[attribution] Indonesian publishers carry "
          f"{out['origin_by_year'][0]['indonesia_share']*100:.1f}% ({years[0]}) → "
          f"{out['origin_by_year'][-1]['indonesia_share']*100:.1f}% ({years[-1]}) of all kept rows")
    print(f"[attribution] tone {t['T_a']:+.3f} → {t['T_b']:+.3f} (Δ {t['delta']:+.3f}) = "
          f"within {t['within']:+.3f} · between {t['between']:+.3f} · entry {t['entry']:+.3f} · "
          f"exit {t['exit']:+.3f} (residual {t['residual']:+.2e})")
    print(f"[attribution] source pool: {out['survivorship']['present_2017']:,} sites in 2017, "
          f"{out['survivorship']['still_present_2026']:,} still there in 2026 "
          f"({out['survivorship']['survival_rate']*100:.1f}%)")
    fp = out["foreign_pulse"]["by_year"]
    print(f"[attribution] foreign-published Indonesia coverage as a share of the whole feed: "
          f"{fp[0]['foreign']:.3f}% ({fp[0]['year']}) → {fp[-1]['foreign']:.3f}% ({fp[-1]['year']}) "
          f"= {(fp[-1]['foreign']/fp[0]['foreign']-1)*100:+.1f}%")
    for a in out["anchor_foreign"]:
        print(f"[attribution] anchor {a['day']} {a['label'][:28]:28s} API {a['published_api_ratio']} · "
              f"all-publishers {a['all']['ratio']} · foreign-only {a['foreign']['ratio']} ({a['dow']})")
    print(f"[attribution] -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
