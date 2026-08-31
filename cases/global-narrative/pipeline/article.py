"""Build the review article's data layer from the case's own published output.

Reads exactly what the dashboard serves (web/public/data/*.json) plus the
attribution study (data/attribution.json), and derives every statistic the review
article prints into web/src/data/article.json. The article page imports that file
in its frontmatter and draws all its figures from it, so the prose cannot drift
from the pipeline: if a number changes here, it changes on the page.

Nothing is fetched; nothing is hand-entered. Run after `export_web.py`.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics as stt
import sys
from collections import defaultdict

import config

PUB = config.CASE_DIR / "web" / "public" / "data"
SRC = config.CASE_DIR / "web" / "src" / "data"
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def ols(x, y):
    n = len(x); mx = sum(x) / n; my = sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    syy = sum((b - my) ** 2 for b in y)
    b1 = sxy / sxx
    r = sxy / math.sqrt(sxx * syy) if sxx and syy else 0.0
    resid = [b - (my - b1 * mx + b1 * a) for a, b in zip(x, y)]
    s2 = sum(v * v for v in resid) / (n - 2) if n > 2 else 0.0
    return {"b": b1, "a": my - b1 * mx, "r": r, "r2": r * r, "n": n,
            "se": math.sqrt(s2 / sxx) if sxx else 0.0}


def med(v):
    v = sorted(x for x in v if x is not None)
    return v[len(v) // 2] if v else None


def main() -> int:
    P = json.loads((PUB / "pulse.json").read_text())
    T = json.loads((PUB / "tone.json").read_text())
    TH = json.loads((PUB / "themes.json").read_text())
    EV = json.loads((PUB / "events.json").read_text())
    SO = json.loads((PUB / "sources.json").read_text())
    ST = json.loads((PUB / "status.json").read_text())
    G = json.loads(config.STATS.read_text())
    A = json.loads((config.DATA_DIR / "attribution.json").read_text())

    d, vol, raw, norm, tone = P["dates"], P["vol"], P["raw"], P["norm"], P["tone"]
    N = len(d)
    idx = {dd: i for i, dd in enumerate(d)}
    out: dict = {"vintage": ST["api_last"], "generated": ST["generated"],
                 "window": G["window"], "days": N,
                 "coverage": G["coverage"], "streamed": ST["streamed"],
                 "api_timelines": ST["api_timelines"]}

    # ── 1 · the denominator ───────────────────────────────────────────────────
    def byyear(a, agg=stt.mean):
        g = defaultdict(list)
        for dd, v in zip(d, a):
            if v is not None:
                g[dd[:4]].append(v)
        return {k: agg(v) for k, v in sorted(g.items())}
    vy, ry, ny, ty = byyear(vol), byyear(raw), byyear(norm), byyear(tone)
    ev_tone = {}
    for m, t in zip(EV["months"], EV["monthly"]["tone_mean"]):
        if t is not None:
            ev_tone.setdefault(m[:4], []).append(t)
    ev_tone = {k: stt.mean(v) for k, v in ev_tone.items()}
    years = sorted(vy)
    y0, y1 = years[0], years[-1]
    out["crawl"] = {
        "by_year": [{"year": int(y), "monitored": round(ny[y], 1), "idn": round(ry[y], 1),
                     "share": round(vy[y], 4), "tone": round(ty[y], 4),
                     "ev_tone": round(ev_tone.get(y, float("nan")), 4) if y in ev_tone else None}
                    for y in years],
        "drift": G["gates"]["G-D1"]["crawl_size_drift_max_over_min"],
        "spearman_raw_vs_norm": G["gates"]["G-D1"]["spearman_raw_vs_normalized"],
        "share_change": vy[y1] / vy[y0] - 1,
        "idn_change": ry[y1] / ry[y0] - 1,
        "monitored_change": ny[y1] / ny[y0] - 1,
    }
    pairs = [(math.log(norm[i]), math.log(vol[i])) for i in range(N)
             if vol[i] and norm[i] and vol[i] > 0]
    fs = ols([p[0] for p in pairs], [p[1] for p in pairs])
    pr = [(math.log(norm[i]), math.log(raw[i])) for i in range(N)
          if raw[i] and norm[i] and raw[i] > 0]
    fr = ols([p[0] for p in pr], [p[1] for p in pr])
    dln = math.log(ny[y1] / ny[y0]); dls = math.log(vy[y1] / vy[y0])
    out["crawl"]["elasticity"] = {
        "share_on_crawl": {"b": round(fs["b"], 4), "se": round(fs["se"], 4), "r2": round(fs["r2"], 4), "n": fs["n"]},
        "idn_on_crawl": {"b": round(fr["b"], 4), "se": round(fr["se"], 4), "r2": round(fr["r2"], 4), "n": fr["n"]},
        "dlog_crawl": round(dln, 4), "dlog_share": round(dls, 4),
        "attributable": round(dln * fs["b"] / dls, 4),
        "attributable_lo": round(dln * (fs["b"] + 1.96 * fs["se"]) / dls, 4),
        "attributable_hi": round(dln * (fs["b"] - 1.96 * fs["se"]) / dls, 4),
    }

    # ── 2 · spikes: numerator or news hole? ───────────────────────────────────
    def trail(a, i, k=config.BASELINE_DAYS):
        return med([a[j] for j in range(max(0, i - k), i)])
    anchors = []
    for a in G["gates"]["G-D2"]["anchors"]:
        att = a["signatures"].get("attention", {})
        pd_ = att.get("peak_day")
        if not pd_ or pd_ not in idx:
            continue
        i = idx[pd_]; j = idx[a["day"]]
        br, bn = trail(raw, j), trail(norm, j)
        rn, rd = raw[i] / br, bn / norm[i]
        lr, ld = math.log(rn), math.log(rd)
        anchors.append({"day": a["day"], "label": a["label"], "verdict": a["verdict"],
                        "gated": a["gated"], "ratio": att.get("ratio"),
                        "numer": round(rn, 3), "denom": round(rd, 3),
                        "denom_share": round(ld / (lr + ld), 4) if (lr + ld) else None,
                        "peak_day": pd_, "dow": DOW[dt.date.fromisoformat(pd_).weekday()],
                        "tone_delta": a["signatures"].get("tone_drop", {}).get("delta")})
    out["anchors"] = anchors

    loud = sorted((i for i in range(N) if vol[i] is not None), key=lambda i: -vol[i])[:30]
    top = []
    for i in loud:
        br, bn = trail(raw, i), trail(norm, i)
        if not (br and bn):
            continue
        rn, rd = raw[i] / br, bn / norm[i]
        lr, ld = math.log(rn), math.log(rd)
        top.append({"day": d[i], "share": round(vol[i], 4), "numer": round(rn, 3),
                    "denom": round(rd, 3), "dow": DOW[dt.date.fromisoformat(d[i]).weekday()],
                    "denom_share": round(ld / (lr + ld), 4) if (lr + ld) else None})
    weekend = sum(1 for t in top if t["dow"] in ("Sat", "Sun", "Mon"))
    cal = defaultdict(int)
    for i in range(N):
        cal[DOW[dt.date.fromisoformat(d[i]).weekday()]] += 1
    exp = len(top) * (cal["Sat"] + cal["Sun"] + cal["Mon"]) / N
    spikes = [i for i in range(N) if vol[i] is not None and (trail(vol, i) or 0)
              and vol[i] / trail(vol, i) >= config.ATTENTION_RATIO]
    sf = []
    for i in spikes:
        br, bn = trail(raw, i), trail(norm, i)
        lr, ld = math.log(raw[i] / br), math.log(bn / norm[i])
        if lr + ld:
            sf.append(ld / (lr + ld))
    out["spikes"] = {
        "top": top, "weekend_mon": weekend, "expected": round(exp, 1), "n_top": len(top),
        "n_spikes": len(spikes), "spike_rate": len(spikes) / N,
        "denom_median": round(stt.median(sf), 4),
        "numer_fell": sum(1 for i in spikes if raw[i] < trail(raw, i)),
        "dow": [{"dow": w,
                 "monitored": round(stt.mean([norm[i] for i in range(N)
                                              if norm[i] is not None and DOW[dt.date.fromisoformat(d[i]).weekday()] == w]), 0),
                 "idn": round(stt.mean([raw[i] for i in range(N)
                                        if raw[i] is not None and DOW[dt.date.fromisoformat(d[i]).weekday()] == w]), 0),
                 "share": round(stt.mean([vol[i] for i in range(N)
                                          if vol[i] is not None and DOW[dt.date.fromisoformat(d[i]).weekday()] == w]), 4),
                 "tone": round(stt.mean([tone[i] for i in range(N)
                                         if tone[i] is not None and DOW[dt.date.fromisoformat(d[i]).weekday()] == w]), 4)}
                for w in DOW],
    }

    # ── 3 · attention is not sentiment ────────────────────────────────────────
    ok = [i for i in range(N) if vol[i] is not None and tone[i] is not None]
    mv = {y: stt.mean([vol[i] for i in ok if d[i][:4] == y]) for y in years}
    mt = {y: stt.mean([tone[i] for i in ok if d[i][:4] == y]) for y in years}
    daily = ols([vol[i] for i in ok], [tone[i] for i in ok])
    dfe = ols([vol[i] - mv[d[i][:4]] for i in ok], [tone[i] - mt[d[i][:4]] for i in ok])
    mgv, mgt = defaultdict(list), defaultdict(list)
    for i in ok:
        mgv[d[i][:7]].append(vol[i]); mgt[d[i][:7]].append(tone[i])
    mos = sorted(mgv)
    MV = [stt.mean(mgv[k]) for k in mos]; MT = [stt.mean(mgt[k]) for k in mos]
    monthly = ols(MV, MT)
    ok.sort(key=lambda i: vol[i])
    k = len(ok) // 10
    dec = [{"q": q + 1, "share": round(stt.mean([vol[i] for i in ok[q * k:(q + 1) * k]]), 4),
            "tone": round(stt.mean([tone[i] for i in ok[q * k:(q + 1) * k]]), 4)} for q in range(10)]
    out["loud_dark"] = {
        "daily_r": round(daily["r"], 4), "daily_slope": round(daily["b"], 4),
        "daily_r_yearfe": round(dfe["r"], 4), "daily_slope_yearfe": round(dfe["b"], 4),
        "monthly_r": round(monthly["r"], 4), "n_months": len(mos),
        "by_year": [{"year": int(y), "r": round(ols([vol[i] for i in ok if d[i][:4] == y],
                                                    [tone[i] for i in ok if d[i][:4] == y])["r"], 4)}
                    for y in years],
        "deciles": dec,
        "top_decile_tone": round(stt.mean([tone[i] for i in ok[-k:]]), 4),
        "rest_tone": round(stt.mean([tone[i] for i in ok[:-k]]), 4),
    }

    # ── 4 · two layers, one crawl ─────────────────────────────────────────────
    ea = {m: t for m, t in zip(EV["months"], EV["monthly"]["tone_mean"])}
    common = [m for m in mos if m in ea and ea[m] is not None]
    apiT = {m: stt.mean(mgt[m]) for m in mos}
    mn = defaultdict(list)
    for i in range(N):
        if norm[i] is not None:
            mn[d[i][:7]].append(norm[i])
    mnorm = {m: stt.mean(v) for m, v in mn.items()}
    out["layers"] = {
        "api_vs_event_tone_r": round(ols([apiT[m] for m in common], [ea[m] for m in common])["r"], 4),
        "api_tone_vs_crawl_r": round(ols([mnorm[m] for m in common], [apiT[m] for m in common])["r"], 4),
        "event_tone_vs_crawl_r": round(ols([mnorm[m] for m in common], [ea[m] for m in common])["r"], 4),
        "rho_all": G["gates"]["G-D3"]["share_all"]["spearman_rho"],
        "rho_en": G["gates"]["G-D3"]["share_en"]["spearman_rho"],
        "rho_trans": G["gates"]["G-D3"]["share_trans"]["spearman_rho"],
        "translated_share": G["gates"]["G-D4"]["event_layer_translated_share"],
        "lang": G["gates"]["G-D4"]["api_language_mean_intensity"],
        "top_countries": [{"name": c["name"], "mean": round(c["mean"], 3)} for c in SO["countries"][:12]],
    }

    # ── 5 · the theme layer, and how uneven it is ─────────────────────────────
    rows = []
    for kk in TH["order"]:
        t = TH["themes"][kk]
        have = [m for m, v in zip(TH["months"], t["share"]) if v is not None]
        rows.append({"key": kk, "label": t["label"], "query": t["query"],
                     "months": len(have), "first": have[0], "last": have[-1],
                     "share_mean": round(t["share_mean"], 5)})
    rows.sort(key=lambda r: -r["share_mean"])
    full = max(r["months"] for r in rows)
    out["themes"] = {"rows": rows, "n_requested": len(config.THEME_QUERIES),
                     "n_landed": len(rows), "full_months": full,
                     "n_full": sum(1 for r in rows if r["months"] >= full - 12),
                     "era_rank": TH["era_rank"]}
    # is the theme ratio free of the crawl? correlate each theme's yearly share with crawl size
    corrs = []
    for r in rows:
        t = TH["themes"][r["key"]]
        g = defaultdict(list)
        for m, v in zip(TH["months"], t["share"]):
            if v is not None and m in mnorm:
                g[m[:4]].append((v, mnorm[m]))
        yy = sorted(k for k in g if len(g[k]) >= 6)
        if len(yy) >= 5:
            corrs.append({"key": r["key"], "label": r["label"],
                          "r": round(ols([stt.mean([x[1] for x in g[y]]) for y in yy],
                                         [stt.mean([x[0] for x in g[y]]) for y in yy])["r"], 3),
                          "months": r["months"]})
    out["themes"]["crawl_corr"] = corrs
    out["themes"]["crawl_corr_mean_abs"] = round(stt.mean([abs(c["r"]) for c in corrs]), 3) if corrs else None
    out["themes"]["share_crawl_corr"] = round(ols([ny[y] for y in years], [vy[y] for y in years])["r"], 3)

    # ── 6 · eras, with what is under them ─────────────────────────────────────
    out["eras"] = []
    for e in P["eras"]:
        sel = [i for i in range(N) if e["start"] <= d[i] <= e["end"]]
        out["eras"].append({
            "label": e["label"], "start": e["start"], "end": e["end"],
            "share": round(e["vol_mean"], 4), "tone": round(e["tone_mean"], 4),
            "idn": round(stt.mean([raw[i] for i in sel if raw[i] is not None]), 0),
            "monitored": round(stt.mean([norm[i] for i in sel if norm[i] is not None]), 0),
            "top": [t["label"] for t in e["top_themes"]]})

    # ── 7 · the attribution study, folded in whole ────────────────────────────
    out["attr"] = A
    out["gates"] = {k: {"verdict": v["verdict"]} for k, v in G["gates"].items()}
    out["gates"]["G-D2"].update({"pass": G["gates"]["G-D2"]["pass"],
                                 "fail": G["gates"]["G-D2"]["fail"],
                                 "pending": G["gates"]["G-D2"]["pending"],
                                 "n": len(G["gates"]["G-D2"]["anchors"])})
    out["darkest"] = [{"day": x["day"], "tone": x["tone"], "vol": x["vol"],
                       "title": (x["articles"][0]["title"] if x.get("articles") else None)}
                      for x in T["darkest"][:6]]
    out["g20"] = T.get("g20")

    SRC.mkdir(parents=True, exist_ok=True)
    (SRC / "article.json").write_text(json.dumps(out, indent=1, allow_nan=False))
    c = out["crawl"]; el = c["elasticity"]
    print(f"[article] crawl {c['by_year'][0]['monitored']:,.0f} → {c['by_year'][-1]['monitored']:,.0f}/day "
          f"({c['monitored_change']*100:+.0f}%) · Indonesia articles {c['idn_change']*100:+.0f}% · "
          f"published share {c['share_change']*100:+.0f}%")
    print(f"[article] {el['attributable']*100:.0f}% of the decade's share rise is the crawl "
          f"({el['attributable_lo']*100:.0f}–{el['attributable_hi']*100:.0f}%)")
    print(f"[article] loudest 30 days: {out['spikes']['weekend_mon']} fall Sat/Sun/Mon vs "
          f"{out['spikes']['expected']} expected · median {out['spikes']['denom_median']*100:.0f}% of a "
          f"spike is the news hole")
    print(f"[article] loud vs dark: daily r {out['loud_dark']['daily_r']:+.3f} "
          f"(within year {out['loud_dark']['daily_r_yearfe']:+.3f}) vs monthly r "
          f"{out['loud_dark']['monthly_r']:+.3f} — the page prints the monthly one")
    print(f"[article] themes: {out['themes']['n_landed']} of {out['themes']['n_requested']} landed; "
          f"{out['themes']['n_full']} have a full window")
    print(f"[article] -> {SRC / 'article.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
