"""Build the review article's data layer from the case's own published output.

Every number the article prints comes from here — the view-model JSONs the case
already ships, the partner statistics it already computed, and review.json from
pipeline/review.py. Nothing in the article's prose is typed by hand, so it cannot
drift from the pipeline. The article pins a data vintage the way a paper pins its
sample.

Usage:  uv run python pipeline/article.py
Writes: web/src/data/article.json
"""

from __future__ import annotations

import json
import math
import statistics as st
import sys

import config

WEB = config.CASE_DIR / "web"
PUB = WEB / "public" / "data"
SRC = WEB / "src" / "data"


def _j(p):
    return json.loads(p.read_text())


def _win(series: dict, years) -> float:
    v = [series.get(str(y), series.get(y, 0)) for y in years]
    return sum(v) / len(v) if v else 0.0


def main() -> int:
    traj = _j(PUB / "trajectory.json")
    nickel = _j(PUB / "nickel.json")
    inputs = _j(PUB / "nickel_inputs.json")
    partners = _j(PUB / "partners.json")
    imports = _j(PUB / "imports.json")
    gates = _j(PUB / "gates_partners.json")
    balance = _j(PUB / "balance.json")
    pm = _j(SRC / "partner_summary.json")
    R = _j(SRC / "review.json")

    years = partners["years"]
    latest = years[-1]
    out: dict = {
        "vintage": latest,
        "release": config.BACI_RELEASE,
        "firstYear": years[0],
        "nYears": len(years),
        "peers": list(config.PEERS),
    }

    # ── 1 · the rank, as the case publishes it and as it reproduces ──────────
    rk, nby = traj["ranks"], traj["n"]
    yy = [str(y) for y in range(years[0], latest + 1)]
    idn = [rk["IDN"][y] for y in yy]
    d = [idn[i + 1] - idn[i] for i in range(len(idn) - 1)]
    out["rank"] = {
        "years": [int(y) for y in yy],
        "published": idn,
        "n": [int(nby[y]) for y in yy],
        "now": rk["IDN"][str(latest)], "nNow": int(nby[str(latest)]),
        "decadeAgo": rk["IDN"][str(latest - 10)], "nThen": int(nby[str(latest - 10)]),
        "decadeChange": rk["IDN"][str(latest - 10)] - rk["IDN"][str(latest)],
        "meanAbsYoY": st.mean(abs(x) for x in d),
        "sdYoY": st.pstdev(d),
        "maxAbsYoY": max(abs(x) for x in d),
        "biggestSwing": max(range(len(d)), key=lambda i: abs(d[i])),
    }
    out["rank"]["biggestSwingYears"] = [int(yy[out["rank"]["biggestSwing"]]),
                                        int(yy[out["rank"]["biggestSwing"] + 1])]
    out["rank"]["biggestSwingSize"] = d[out["rank"]["biggestSwing"]]
    # the validation year the case chose vs the year it displays
    out["rank"]["validationYear"] = 2023
    out["rank"]["validationRank"] = rk["IDN"]["2023"]
    out["rank"]["validationN"] = int(nby["2023"])
    out["rank"]["gateWindow"] = list(config.GATE_IDN_RANK)
    out["rank"]["gateHoldsAtLatest"] = (config.GATE_IDN_RANK[0] <= rk["IDN"][str(latest)]
                                        <= config.GATE_IDN_RANK[1])

    # ── 2 · the never-computed rank-correlation gate, and the Atlas ──────────
    E = R["eci"]
    rows = E["rows"]
    out["gateB1"] = {
        "threshold": E["gateSpearman"],
        "rows": [{"year": r["year"], "rho": r["spearman"], "n": r["nCommon"]} for r in rows],
        "min": E["spearmanMin"], "mean": E["spearmanMean"], "max": E["spearmanMax"],
        "yearsBelow": E["yearsBelowGate"],
        "atlasSource": E["atlasSource"],
    }
    out["reproduction"] = E["reproduction"]
    out["atlas"] = {
        "configBenchmark": E["atlasBenchmarkInConfig"],
        "rows": [{"year": r["year"], "published": r["publishedRank"], "publishedN": r["publishedN"],
                  "mine": r["rank"], "mineN": r["n"],
                  "onAtlasSample": r["onAtlasSample"], "onAtlasSampleN": r["onAtlasSampleN"],
                  "atlas": r["atlasRank"], "atlasN": r["atlasN"],
                  "pct": r["pct"], "atlasPct": r["atlasPct"]} for r in rows],
    }
    late = [r for r in rows if r["onAtlasSample"] and r["atlasRank"]]
    out["atlas"]["meanGapOnSameSample"] = st.mean(r["onAtlasSample"] - r["atlasRank"] for r in late)
    out["atlas"]["meanPctGap"] = st.mean(r["pct"] - r["atlasPct"] for r in late)
    out["atlas"]["yearsWorseThanAtlas"] = sum(1 for r in late if r["pct"] > r["atlasPct"])
    # keep the same key names the rows carry, so the page cannot read a key that
    # silently does not exist
    mapped = {r["year"]: r for r in out["atlas"]["rows"]}
    out["atlas"]["y2023"] = mapped[2023]
    out["atlas"]["latest"] = mapped[latest]

    # ── 3 · how dense the ECI distribution is where Indonesia sits ───────────
    det = E["detail"][str(latest)]
    out["density"] = {
        "year": latest, "sd": det["sd"], "within005": det["within005"],
        "eciTenPlaces": det["eciTenPlaces"],
        "tenPlacesInSd": det["eciTenPlaces"] / det["sd"],
        "neighbours": det["neighbours"],
        "y2023": {k: E["detail"]["2023"][k] for k in ("within005", "eciTenPlaces", "neighbours")},
    }
    # a two-place decade move, expressed in the index the rank is built on
    out["density"]["decadeMoveInSd"] = (abs(out["rank"]["decadeChange"])
                                        / 10 * det["eciTenPlaces"] / det["sd"])

    # ── 4 · the peer claim the page makes ───────────────────────────────────
    ap = E["atlasPeers"]
    out["peerLadder"] = []
    for c in ["IDN"] + list(config.PEERS):
        o1, o2 = rk[c][str(latest - 10)], rk[c][str(latest)]
        a1 = ap.get(c, {}).get(str(latest - 10))
        a2 = ap.get(c, {}).get(str(latest))
        out["peerLadder"].append({
            "iso3": c, "from": o1, "to": o2, "gain": o1 - o2,
            "atlasFrom": a1, "atlasTo": a2,
            "atlasGain": (a1 - a2) if (a1 and a2) else None,
            "gain30": rk[c][str(years[0])] - o2,
        })
    out["peerClaim"] = {"threshold": 20, "named": ["VNM", "PHL", "IND"]}
    out["peerClaim"]["maxGain"] = max(p["gain"] for p in out["peerLadder"]
                                      if p["iso3"] in out["peerClaim"]["named"])
    out["peerClaim"]["maxAtlasGain"] = max(p["atlasGain"] for p in out["peerLadder"]
                                           if p["iso3"] in out["peerClaim"]["named"]
                                           and p["atlasGain"] is not None)

    # ── 5 · the nickel chain: dollars, tonnes and unit value ────────────────
    N = R["nickel"]
    ore = {int(k): v for k, v in nickel["ore"].items()}
    proc = {int(k): v for k, v in nickel["processed"].items()}
    base, peak = N["base"], N["peak"]
    out["nickel"] = {
        "base": base, "peak": peak,
        "value": N["processed"]["vRatio"],
        "tonnes": N["processed"]["qRatio"],
        "unitValue": N["processed"]["uvRatio"],
        "uvBase": N["processed"]["uvBase"] * 1e3,     # kUSD/tonne -> USD/tonne
        "uvPeak": N["processed"]["uvPeak"] * 1e3,
        "shareVolume": N["processed"]["shareVolume"],
        "vBase": N["processed"]["vBase"], "vPeak": N["processed"]["vPeak"],
        "qBase": N["processed"]["qBase"], "qPeak": N["processed"]["qPeak"],
        "narrowValue": N["narrow"]["vRatio"], "narrowTonnes": N["narrow"]["qRatio"],
        "narrowUvBase": N["narrow"]["uvBase"] * 1e3, "narrowUvPeak": N["narrow"]["uvPeak"] * 1e3,
        "composition": N["composition"]["groups"],
        "compositionBaseTotal": N["composition"]["baseTotal"],
        "compositionPeakTotal": N["composition"]["peakTotal"],
        "nonNickelPeakShare": N["composition"]["nonNickelPeakShare"],
        "topLines": N["composition"]["top"][:8],
        "series": [{"year": int(y), "v": r["v"], "q": r["q"],
                    "uv": (r["v"] / r["q"] * 1e3) if r["q"] else None}
                   for y, r in sorted(N["series"].items(), key=lambda kv: int(kv[0]))],
        "oreBase": _win(ore, base), "orePeak": _win(ore, peak),
    }
    out["nickel"]["gain"] = out["nickel"]["vPeak"] - out["nickel"]["vBase"]
    out["nickel"]["oreForegone"] = out["nickel"]["oreBase"] - out["nickel"]["orePeak"]

    # ── 6 · the imported input bill the gain is bought with ─────────────────
    ins = inputs["inputs"]
    rowsI = []
    for code, d0 in ins.items():
        s = d0["series"]
        b, p = _win(s, base), _win(s, peak)
        rowsI.append({"code": code, "label": d0["label"], "base": b, "peak": p,
                      "add": p - b, "growth": d0["growth"]})
    rowsI.sort(key=lambda r: -r["add"])
    added = sum(r["add"] for r in rowsI)
    bulk = [r for r in rowsI if r["code"] in ("2701", "2704", "2610", "2521", "2522")]
    out["inputs"] = {
        "rows": rowsI, "added": added,
        "threshold": inputs["thresholds"]["h3"],
        "bulkAdded": sum(r["add"] for r in bulk),
        "bulkRatio": (sum(r["peak"] for r in bulk) / sum(r["base"] for r in bulk)),
        "capGrowth": inputs["capGrowth"], "totalGrowth": inputs["totalGrowth"],
        "excess": inputs["excess"], "verdict": inputs["verdict"],
        "h1": inputs["h1"], "h2": inputs["h2"], "h3": inputs["h3"],
        "capShare": [{"year": int(y), "share": inputs["capgoods"][y] / inputs["totalImports"][y]}
                     for y in sorted(inputs["capgoods"]) if y in inputs["totalImports"]],
        "shareOfGain": added / out["nickel"]["gain"],
        "addedPerExtraTonne": added * 1e3 / (out["nickel"]["qPeak"] - out["nickel"]["qBase"]),
    }
    # the waterfall the page states as one ratio
    out["ledger"] = [
        {"k": "Extra processed-nickel exports", "v": out["nickel"]["gain"], "sign": 1},
        {"k": "Ore exports given up", "v": -out["nickel"]["oreForegone"], "sign": -1},
        {"k": "Extra imported smelter inputs", "v": -added, "sign": -1},
    ]
    out["ledger"].append({"k": "Left on the trade account",
                          "v": sum(r["v"] for r in out["ledger"]), "sign": 0})

    # ── 7 · the export reconciliation, and whether complexity feels it ──────
    G = R["gap"]
    out["gap"] = {
        "year": G["year"], "baci": G["baci"], "bps": G["bps"], "comtrade": G["comtrade"],
        "excess": G["excess"], "deviation": G["deviation"],
        "tolerance": gates["G-B5"]["tolerance"], "pass": gates["G-B5"]["pass"],
        "byYear": G["byYear"], "nPartners": G["nPartners"],
        "chapters": G["chapters"][:8], "aggregateCodes": G["aggregateCodes"],
        "importDeviation": gates["G-B6"]["deviation"], "importPass": gates["G-B6"]["pass"],
        "importTolerance": gates["G-B6"]["tolerance"],
    }
    out["counterfactual"] = E["counterfactual"]

    # ── 8 · concentration, all partners against a fixed roster ──────────────
    P = R["panel"]
    out["conc"] = {
        "panelExp": P["panelSizeExp"], "panelImp": P["panelSizeImp"],
        "rows": P["rows"], "survivingShare": P["survivingShare"],
        "troughAll": P["troughAll"], "troughPanel": P["troughPanel"],
    }
    he = partners["hhiExp"]; hi = partners["hhiImp"]
    itE = min(range(len(he)), key=lambda i: he[i])
    itI = min(range(len(hi)), key=lambda i: hi[i])
    out["conc"]["exp"] = {"first": he[0], "trough": he[itE], "troughYear": years[itE],
                          "last": he[-1], "backAboveStart": he[-1] > he[0]}
    out["conc"]["imp"] = {"first": hi[0], "trough": hi[itI], "troughYear": years[itI],
                          "last": hi[-1], "backAboveStart": hi[-1] > hi[0],
                          "yearsBelowStart": sum(1 for v in hi if v < hi[0]),
                          "crossedBackYear": next((years[i] for i, v in enumerate(hi)
                                                   if i > itI and v > hi[0]), None)}
    out["conc"]["series"] = {"years": years, "hhiExp": he, "hhiImp": hi,
                             "top5Exp": partners["top5Exp"], "top5Imp": partners["top5Imp"],
                             "nExp": partners["nPartnersExp"], "nImp": partners["nPartnersImp"]}

    # ── 9 · context for the decisions section ──────────────────────────────
    ly = str(latest)
    imp_ch = imports["byYear"][ly][:6]
    out["context"] = {
        "totalExp": pm["totalExp"], "totalImp": pm["totalImp"], "balance": pm["balance"],
        "topExp": pm["topExp"][:5], "topImp": pm["topImp"][:5],
        "chinaExpShare": pm["chinaShare"][ly], "chinaImpShare": pm["chinaImpShare"][ly],
        "topImportChapters": imp_ch,
        "deficits": sorted(
            [{"code": r["c"], "name": balance["names"][str(r["c"])]["name"],
              "bal": r["e"] - r["i"]} for r in balance["rows"][ly]],
            key=lambda r: r["bal"])[:5],
    }

    SRC.mkdir(parents=True, exist_ok=True)
    (SRC / "article.json").write_text(json.dumps(out, indent=1))
    print(f"[article] rank {out['rank']['now']}/{out['rank']['nNow']} · "
          f"decade {out['rank']['decadeChange']:+d} · mean |ΔYoY| "
          f"{out['rank']['meanAbsYoY']:.2f}")
    print(f"[article] G-B1 Spearman min {out['gateB1']['min']:.3f} mean "
          f"{out['gateB1']['mean']:.3f} — {len(out['gateB1']['yearsBelow'])} years below "
          f"{out['gateB1']['threshold']}")
    print(f"[article] nickel {out['nickel']['value']:.1f}x value, "
          f"{out['nickel']['tonnes']:.1f}x tonnes, unit value "
          f"{out['nickel']['unitValue']:.2f}x")
    print(f"[article] ten ranks span {out['density']['tenPlacesInSd']:.3f} sd of ECI")
    print(f"[article] -> {SRC / 'article.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
