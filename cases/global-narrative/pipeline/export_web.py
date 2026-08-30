"""Stage export — NaN-safe web view-models (spec N3 "export", N5 page anatomy).

Reads data/narrative_daily.parquet (+ docapi_curves.parquet, events parquet,
stats.json) and writes small JSON files the dashboard fetches at runtime:

  pulse.json    daily attention share 2017->now (+7-day smooth), tone, raw/norm
                for the methodology, anchors with their gate numbers, eras
  tone.json     daily tone, darkest/brightest days with headline samples,
                monthly volume-vs-tone points, the G20 report-only result
  themes.json   monthly theme shares (theme volume / Indonesia volume), tone
                per theme, top days, era rankings
  sources.json  source-country intensity matrix, language mix, foreign vs
                domestic press, top outlets from the event layer
  events.json   monthly event typology (QuadClass, protest, violence),
                Goldstein/tone, own-denominator share, top actor countries
  map.json      0.25-degree grid of event locations (count / tone / protest)
  status.json   dual vintage stamps + pipeline coverage + gate verdicts
  web/src/data/summary.json  build-time headline numbers

Every float goes through clean(): NaN/inf -> null, numpy -> python; json.dumps
runs with allow_nan=False so a NaN can never reach the browser (the trade and
nightlights cases learned this the hard way). Runs fine on partial data — a
missing layer yields an honest "pending" status, not a crash.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import config

OUT = config.CASE_DIR / "web" / "public" / "data"
SRC_DATA = config.CASE_DIR / "web" / "src" / "data"

THEME_LABELS = {
    "nickel": "Nickel", "palm_oil": "Palm oil", "ikn": "New capital (IKN)", "election": "Elections",
    "flood": "Floods", "earthquake": "Quakes & volcanoes", "protest": "Protests", "covid": "COVID-19",
    "terror": "Terrorism", "papua": "Papua", "coal": "Coal & mining", "forest": "Forests, haze & fires",
    "cyber": "Cyber", "tourism": "Tourism & Bali", "football": "Football", "asean": "ASEAN", "g20": "G20",
    "ev_battery": "EV & batteries", "economy": "Macro & rupiah", "china": "China ties",
}
ROOT_LABELS = {
    "01": "Public statement", "02": "Appeal", "03": "Intent to cooperate", "04": "Consult",
    "05": "Diplomatic cooperation", "06": "Material cooperation", "07": "Provide aid", "08": "Yield",
    "09": "Investigate", "10": "Demand", "11": "Disapprove", "12": "Reject", "13": "Threaten",
    "14": "Protest", "15": "Force posture", "16": "Reduce relations", "17": "Coerce", "18": "Assault",
    "19": "Fight", "20": "Mass violence",
}
IDN_BBOX = (-11.5, 6.5, 94.5, 141.5)   # lat_min, lat_max, lon_min, lon_max


# ── helpers ───────────────────────────────────────────────────────────────────
def clean(o, nd: int = 4):
    """Recursively make `o` JSON-safe: NaN/inf -> None, numpy/pandas scalars -> python."""
    if isinstance(o, dict):
        return {str(k): clean(v, nd) for k, v in o.items()}
    if isinstance(o, (list, tuple, np.ndarray, pd.Series, pd.Index)):
        return [clean(v, nd) for v in list(o)]
    if isinstance(o, (pd.Timestamp, dt.datetime, dt.date)):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, nd)
    if o is pd.NA or o is pd.NaT:
        return None
    return o


def dump(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    text = json.dumps(clean(obj), allow_nan=False, separators=(",", ":"))
    (OUT / name).write_text(text)
    print(f"[export] {name:14s} {len(text) / 1e3:8.1f} kB")


def roll(s: pd.Series, w: int = 7) -> pd.Series:
    return s.rolling(w, center=True, min_periods=max(1, w // 2)).mean()


def col(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name] if name in df else pd.Series(np.nan, index=df.index, dtype="float64")


def monthly(df: pd.DataFrame, how: str = "mean") -> pd.DataFrame:
    g = df.resample("MS")
    return g.sum(min_count=1) if how == "sum" else g.mean(numeric_only=True)


def month_keys(idx) -> list[str]:
    return [d.strftime("%Y-%m") for d in idx]


def _articles_for(day: str, window: int, allow_fetch: bool, n: int = 6) -> list[dict]:
    """Headline sample from the ArtList cache (fetching if allowed and missing)."""
    import doc_api
    start = dt.date.fromisoformat(day)
    params = {"query": "Indonesia", "mode": "ArtList", "maxrecords": 100, "sort": "HybridRel",
              "startdatetime": start.strftime("%Y%m%d") + "000000",
              "enddatetime": (start + dt.timedelta(days=window)).strftime("%Y%m%d") + "000000"}
    path = doc_api._cache_path(f"artlist__{day}", params)
    if path.exists():
        js = json.loads(path.read_text())
    elif allow_fetch:
        js = doc_api.fetch(f"artlist__{day}", params) or {}
    else:
        return []
    seen, out = set(), []
    for a in js.get("articles", []):
        t = (a.get("title") or "").strip()
        key = t.lower()[:60]
        if not t or key in seen:
            continue
        seen.add(key)
        out.append({"title": t[:140], "domain": a.get("domain"), "country": a.get("sourcecountry"),
                    "lang": a.get("language"), "url": a.get("url"), "seen": (a.get("seendate") or "")[:8]})
        if len(out) >= n:
            break
    return out


# ── view-models ───────────────────────────────────────────────────────────────
def export_pulse(df: pd.DataFrame, stats: dict, allow_fetch: bool) -> dict:
    vol = col(df, "api_vol")
    tone = col(df, "api_tone")
    anchors = []
    for a in stats.get("gates", {}).get("G-D2", {}).get("anchors", []):
        d0 = pd.Timestamp(a["day"])
        win = df.loc[d0: d0 + pd.Timedelta(days=a["window_days"] - 1)]
        att = a["signatures"].get("attention", {})
        anchors.append({"day": a["day"], "label": a["label"], "verdict": a["verdict"], "window": a["window_days"],
                        "peak_day": att.get("peak_day"), "peak": att.get("peak"), "ratio": att.get("ratio"),
                        "tone_min": clean(win["api_tone"].min()) if "api_tone" in win and len(win) else None,
                        "signatures": {k: v.get("verdict") for k, v in a["signatures"].items()},
                        "articles": _articles_for(a["day"], a["window_days"], allow_fetch, n=4)})
    eras = []
    theme_cols = [c for c in df.columns if c.startswith("theme_") and c.endswith("_vol")]
    for start, end, label in config.ERAS:
        sub = df.loc[start:end]
        if sub.empty or sub["api_vol"].notna().sum() == 0:
            continue
        shares = {c[6:-4]: float((sub[c] / sub["api_vol"]).replace([np.inf], np.nan).mean()) for c in theme_cols}
        shares = {k: v for k, v in shares.items() if not math.isnan(v)}
        top = sorted(shares.items(), key=lambda kv: -kv[1])[:3]
        peak_day = sub["api_vol"].idxmax()
        eras.append({"label": label, "start": max(pd.Timestamp(start), sub.index.min()), "end": min(pd.Timestamp(end), sub.index.max()),
                     "vol_mean": sub["api_vol"].mean(), "tone_mean": sub["api_tone"].mean() if "api_tone" in sub else None,
                     "peak_day": peak_day, "peak_vol": sub["api_vol"].max(),
                     "top_themes": [{"key": k, "label": THEME_LABELS.get(k, k), "share": v} for k, v in top],
                     "events": int(sub["n_events"].sum()) if "n_events" in sub and sub["n_events"].notna().any() else None,
                     "protests": int(sub["protest_n"].sum()) if "protest_n" in sub and sub["protest_n"].notna().any() else None,
                     "days": int(len(sub))})
    return {"dates": [d.strftime("%Y-%m-%d") for d in df.index], "vol": vol, "vol7": roll(vol),
            "tone": tone, "tone7": roll(tone), "raw": col(df, "api_vol_raw"), "norm": col(df, "api_norm"),
            "anchors": anchors, "eras": eras,
            "vol_mean": vol.mean(), "vol_peak_day": vol.idxmax() if vol.notna().any() else None, "vol_peak": vol.max()}


def _extremes(df: pd.DataFrame, ascending: bool, n: int, sep_days: int = 14) -> list[pd.Timestamp]:
    tone = col(df, "api_tone").dropna()
    vol = col(df, "api_vol")
    # only days with at least median attention — a quiet day's tone is noise
    ok = tone[vol.reindex(tone.index) >= vol.median() * 0.8]
    picked: list[pd.Timestamp] = []
    for day in ok.sort_values(ascending=ascending).index:
        if all(abs((day - p).days) >= sep_days for p in picked):
            picked.append(day)
        if len(picked) >= n:
            break
    return picked


def export_tone(df: pd.DataFrame, stats: dict, allow_fetch: bool) -> dict:
    tone = col(df, "api_tone")
    base = tone.rolling(config.BASELINE_DAYS, min_periods=14).mean().shift(1)

    def day_rec(d: pd.Timestamp, fetch_ok: bool) -> dict:
        return {"day": d, "tone": tone[d], "vol": col(df, "api_vol")[d], "delta": tone[d] - base[d],
                "protests": int(df.loc[d, "protest_n"]) if "protest_n" in df and pd.notna(df.loc[d, "protest_n"]) else None,
                "articles": _articles_for(d.strftime("%Y-%m-%d"), 2, fetch_ok, n=5)}

    darkest = [day_rec(d, allow_fetch) for d in _extremes(df, True, 10)]
    brightest = [day_rec(d, allow_fetch) for d in _extremes(df, False, 6)]
    mcols = [c for c in ("api_vol", "api_tone", "n_events", "protest_n") if c in df]
    m = monthly(df[mcols]) if mcols else pd.DataFrame(index=pd.DatetimeIndex([]))
    for c in ("api_vol", "api_tone"):
        if c not in m:
            m[c] = np.nan
    g20 = next((a for a in stats.get("gates", {}).get("G-D2", {}).get("anchors", []) if a["day"] == "2022-11-15"), None)
    yearly_tone = tone.groupby(tone.index.year).mean()
    return {"dates": [d.strftime("%Y-%m-%d") for d in df.index], "tone": tone, "tone7": roll(tone), "tone28": roll(tone, 28),
            "mean": tone.mean(), "darkest": darkest, "brightest": brightest,
            "monthly": {"months": month_keys(m.index), "vol": m["api_vol"], "tone": m["api_tone"],
                        "events": m["n_events"] if "n_events" in m else None},
            "yearly_tone": {str(int(y)): v for y, v in yearly_tone.items()},
            "g20": g20, "foreign_tone": col(df, "api_foreign_tone"), "domestic_tone": col(df, "api_domestic_tone")}


def export_themes(df: pd.DataFrame) -> dict:
    if "api_vol" not in df:
        return {"months": [], "themes": {}, "order": [], "era_rank": []}
    m = monthly(df)
    months = month_keys(m.index)
    themes = {}
    for key, label in THEME_LABELS.items():
        vc = f"theme_{key}_vol"
        if vc not in df:
            continue
        share_m = (m[vc] / m["api_vol"]).replace([np.inf, -np.inf], np.nan)
        daily_share = (df[vc] / df["api_vol"]).replace([np.inf, -np.inf], np.nan)
        peaks = df[vc].dropna().nlargest(40)
        top_days, last = [], None
        for d, v in peaks.items():
            if last is None or all(abs((d - t).days) >= 30 for t in last):
                top_days.append({"day": d, "vol": v, "share": daily_share[d]})
                last = (last or []) + [d]
            if len(top_days) >= 3:
                break
        themes[key] = {"label": label, "query": config.THEME_QUERIES[key], "vol": m[vc], "share": share_m,
                       "share_mean": daily_share.mean(), "tone": m[f"theme_{key}_tone"] if f"theme_{key}_tone" in m else None,
                       "top_days": top_days}
    order = sorted(themes, key=lambda k: -(themes[k]["share_mean"] or 0))
    era_rank = []
    for start, end, label in config.ERAS:
        sub = df.loc[start:end]
        if sub.empty or sub["api_vol"].notna().sum() == 0:
            continue
        ranks = sorted(((k, float((sub[f"theme_{k}_vol"] / sub["api_vol"]).replace([np.inf], np.nan).mean())) for k in themes),
                       key=lambda kv: -kv[1])
        era_rank.append({"era": label, "themes": [{"key": k, "share": v} for k, v in ranks[:6]]})
    return {"months": months, "indonesia_vol": m["api_vol"], "order": order, "themes": themes, "era_rank": era_rank}


def export_sources(df: pd.DataFrame, cur: pd.DataFrame | None, con) -> dict:
    out: dict = {"countries": [], "languages": [], "outlets": {}}
    if cur is not None:
        sc = cur[(cur["qid"] == "indonesia") & (cur["mode"] == "TimelineSourceCountry")]
        if not sc.empty:
            w = sc.pivot_table(index="date", columns="series", values="value", aggfunc="first")
            wm = w.resample("MS").mean()
            out["months"] = month_keys(wm.index)
            order = w.mean().sort_values(ascending=False).index
            out["countries"] = [{"name": c, "mean": w[c].mean(), "values": wm[c]} for c in order]
            out["country_note"] = ("TimelineSourceCountry: for each publishing country, the share of ITS OWN "
                                   "monitored articles that mention Indonesia — intensity of interest, not "
                                   "share of the world's Indonesia coverage.")
        lang = cur[(cur["qid"] == "indonesia") & (cur["mode"] == "TimelineLang")]
        if not lang.empty:
            lw = lang.pivot_table(index="date", columns="series", values="value", aggfunc="first")
            lm = lw.resample("MS").mean()
            order = lw.mean().sort_values(ascending=False).index[:8]
            out["languages"] = [{"name": c, "mean": lw[c].mean(), "values": lm[c]} for c in order]
            out["lang_months"] = month_keys(lm.index)
    m = monthly(df[[c for c in ("api_vol", "api_foreign_vol", "api_domestic_vol", "api_foreign_tone", "api_domestic_tone") if c in df]])
    if "api_foreign_vol" in m:
        out["press"] = {"months": month_keys(m.index), "foreign": m["api_foreign_vol"], "domestic": m["api_domestic_vol"],
                        "foreign_share": (m["api_foreign_vol"] / m["api_vol"]).clip(upper=1),
                        "foreign_tone": m.get("api_foreign_tone"), "domestic_tone": m.get("api_domestic_tone")}
    if con is not None:
        glob = str(config.EVENTS_DIR / "events_*.parquet")
        for feed in ("en", "trans"):
            rows = con.execute(f"""
                SELECT source_domain, count(*) n, avg(avg_tone) tone
                FROM read_parquet('{glob}', union_by_name=true)
                WHERE feed = '{feed}' AND source_domain <> '' GROUP BY 1 ORDER BY n DESC LIMIT 25""").fetchall()
            out["outlets"][feed] = [{"domain": d, "n": int(n), "tone": t} for d, n, t in rows]
        tr = con.execute(f"""SELECT feed, count(*) FROM read_parquet('{glob}', union_by_name=true) GROUP BY 1""").fetchall()
        out["feed_rows"] = {f: int(n) for f, n in tr}
    return out


def export_events(df: pd.DataFrame, con) -> dict:
    out: dict = {"coverage": {}}
    files = sorted(config.EVENTS_DIR.glob("events_*.parquet"))
    months_done = sorted(f.stem.split("_")[1] for f in files)
    first = f"{config.EVENTS_START_YEAR}01"
    now = dt.datetime.now(dt.timezone.utc)
    months_total = (now.year - config.EVENTS_START_YEAR) * 12 + now.month
    out["coverage"] = {"months_done": months_done, "months_total": months_total,
                       "complete": len(months_done) >= months_total}
    if "n_events" not in df or df["n_events"].notna().sum() == 0:
        return out
    ev = df[df["n_events"].notna()]
    sums = [c for c in ("n_events", "n_en", "n_trans", "quad_verbal_coop", "quad_material_coop", "quad_verbal_conflict",
                        "quad_material_conflict", "protest_n", "violence_n", "mentions") if c in ev] + [c for c in ev if c.startswith("root_")]
    ms = monthly(ev[sums], "sum")
    mm = monthly(ev[[c for c in ("goldstein_mean", "goldstein_wmean", "tone_mean", "share_all", "share_en", "share_trans", "api_vol") if c in ev]])
    days_in_month = ev["n_events"].resample("MS").count()
    out["months"] = month_keys(ms.index)
    out["days_in_month"] = days_in_month
    out["monthly"] = {c: ms[c] for c in sums if not c.startswith("root_")}
    out["monthly"].update({c: mm[c] for c in mm})
    out["root_mix"] = [{"code": c[5:], "label": ROOT_LABELS.get(c[5:], c), "n": int(ev[c].sum())} for c in ev if c.startswith("root_")]
    out["daily"] = {"dates": [d.strftime("%Y-%m-%d") for d in ev.index], "protest": ev["protest_n"], "share": ev.get("share_all"),
                    "n": ev["n_events"], "goldstein": ev.get("goldstein_mean")}
    out["coverage"].update({"first": ev.index.min(), "last": ev.index.max(), "days": int(len(ev)), "rows": int(ev["n_events"].sum()),
                            "translated_share": float(ev["n_trans"].sum() / ev["n_events"].sum()) if "n_trans" in ev else None})
    if con is not None:
        glob = str(config.EVENTS_DIR / "events_*.parquet")
        actors = con.execute(f"""
            WITH e AS (SELECT year(added_day) y, actor1_country c FROM read_parquet('{glob}', union_by_name=true)
                       UNION ALL SELECT year(added_day), actor2_country FROM read_parquet('{glob}', union_by_name=true))
            SELECT c, y, count(*) n FROM e WHERE c <> '' AND c <> 'IDN' GROUP BY 1, 2""").df()
        tot = actors.groupby("c")["n"].sum().sort_values(ascending=False).head(24)
        years = sorted(actors["y"].unique().tolist())
        piv = actors[actors["c"].isin(tot.index)].pivot_table(index="c", columns="y", values="n", aggfunc="sum").reindex(tot.index).fillna(0)
        out["actors"] = {"years": [int(y) for y in years], "rows": [{"country": c, "n": int(tot[c]), "by_year": [int(piv.loc[c, y]) if y in piv else 0 for y in years]} for c in tot.index]}
    return out


def export_map(con) -> dict:
    if con is None:
        return {"years": [], "all": [], "byYear": {}}
    glob = str(config.EVENTS_DIR / "events_*.parquet")
    la0, la1, lo0, lo1 = IDN_BBOX
    base = f"""FROM read_parquet('{glob}', union_by_name=true)
               WHERE action_country = 'ID' AND action_lat BETWEEN {la0} AND {la1} AND action_lon BETWEEN {lo0} AND {lo1}
                 AND action_adm1 <> 'ID'"""   # adm1 == 'ID' -> geocoded only to "Indonesia" (country centroid)
    cells = con.execute(f"""
        SELECT round(action_lat * 4) / 4 AS lat, round(action_lon * 4) / 4 AS lon, count(*) n,
               avg(avg_tone) tone, count(*) FILTER (WHERE event_root = '14') protest,
               mode(action_name) AS cname
        {base} GROUP BY 1, 2 HAVING count(*) >= 2 ORDER BY n DESC""").fetchall()
    by_year = con.execute(f"""
        SELECT year(added_day) y, round(action_lat * 4) / 4 AS lat, round(action_lon * 4) / 4 AS lon, count(*) n,
               avg(avg_tone) tone, count(*) FILTER (WHERE event_root = '14') protest
        {base} GROUP BY 1, 2, 3 HAVING count(*) >= 2""").fetchall()
    centroid = con.execute(f"""
        SELECT count(*) FROM read_parquet('{glob}', union_by_name=true)
        WHERE action_country = 'ID' AND action_adm1 = 'ID'""").fetchone()[0]
    located = con.execute(f"SELECT count(*) {base}").fetchone()[0]
    years: dict[str, list] = {}
    for y, lat, lon, n, tone, protest in by_year:
        years.setdefault(str(int(y)), []).append([float(lat), float(lon), int(n), tone, int(protest)])
    return {"years": sorted(years), "all": [[float(a), float(b), int(n), t, int(p), (nm or "")[:48]] for a, b, n, t, p, nm in cells],
            "byYear": years, "located": int(located), "country_level_only": int(centroid)}


def export_status(df: pd.DataFrame, stats: dict, events: dict) -> dict:
    cache = list(config.DOCAPI_CACHE.glob("*.json")) if config.DOCAPI_CACHE.exists() else []
    api_refreshed = max((f.stat().st_mtime for f in cache), default=None)
    api_refreshed = dt.datetime.fromtimestamp(api_refreshed, dt.timezone.utc).isoformat(timespec="minutes") if api_refreshed else None
    streamed = {}
    if config.EVENTS_STATS.exists():
        st = pd.read_csv(config.EVENTS_STATS)
        streamed = {"files": int(st["files"].sum()), "gb": round(float(st["bytes"].sum() / 1e9), 2), "rows_scanned": int(st["rows"].sum()),
                    "rows_kept": int(st["kept"].sum()), "missing": int(st["missing"].sum()), "failed": int(st["failed"].sum())}
    cov = events.get("coverage", {})
    return {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "api_refreshed": api_refreshed,
            "api_first": df["api_vol"].dropna().index.min() if "api_vol" in df and df["api_vol"].notna().any() else None,
            "api_last": df["api_vol"].dropna().index.max() if "api_vol" in df and df["api_vol"].notna().any() else None,
            "api_timelines": len([f for f in cache if "artlist" not in f.name]),
            "events_first": cov.get("first"), "events_last": cov.get("last"), "events_days": cov.get("days"),
            "events_months_done": len(cov.get("months_done", [])), "events_months_total": cov.get("months_total"),
            "events_complete": cov.get("complete", False), "streamed": streamed,
            "gates": {k: v.get("verdict") for k, v in stats.get("gates", {}).items()}, "verdict": stats.get("verdict")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true", help="never call the DOC API for headline samples (cache only)")
    a = ap.parse_args()
    allow_fetch = not a.no_fetch
    if not config.LEDGER.exists():
        print("[export] ledger missing — writing status only")
        dump("status.json", {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "pending": True})
        return 0
    df = pd.read_parquet(config.LEDGER)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    stats = json.loads(config.STATS.read_text()) if config.STATS.exists() else {}
    cur = pd.read_parquet(config.CURVES) if config.CURVES.exists() else None
    con = None
    if list(config.EVENTS_DIR.glob("events_*.parquet")):
        import duckdb
        con = duckdb.connect()
        con.execute("SET memory_limit='2GB'; SET threads=2;")

    pulse = export_pulse(df, stats, allow_fetch); dump("pulse.json", pulse)
    dump("tone.json", export_tone(df, stats, allow_fetch))
    dump("themes.json", export_themes(df))
    dump("sources.json", export_sources(df, cur, con))
    events = export_events(df, con); dump("events.json", events)
    dump("map.json", export_map(con))
    status = export_status(df, stats, events); dump("status.json", status)
    if config.STATS.exists():
        dump("stats.json", stats)
    if con is not None:
        con.close()

    SRC_DATA.mkdir(parents=True, exist_ok=True)
    g2 = stats.get("gates", {}).get("G-D2", {})
    summary = {
        "generated": status["generated"], "apiLast": status["api_last"], "eventsLast": status["events_last"],
        "volMean": pulse["vol_mean"], "volPeakDay": pulse["vol_peak_day"], "volPeak": pulse["vol_peak"],
        "toneMean": col(df, "api_tone").mean(), "days": int(len(df)),
        "eventRows": events.get("coverage", {}).get("rows"), "eventsComplete": status["events_complete"],
        "anchorsPass": g2.get("pass"), "anchorsTotal": len(g2.get("anchors", [])), "verdict": stats.get("verdict"),
        "translatedShare": events.get("coverage", {}).get("translated_share"),
        "foreignShare": stats.get("gates", {}).get("G-D4", {}).get("api_foreign_share_mean"),
        "crawlDrift": stats.get("gates", {}).get("G-D1", {}).get("crawl_size_drift_max_over_min"),
    }
    (SRC_DATA / "summary.json").write_text(json.dumps(clean(summary), indent=1, allow_nan=False))
    print(f"[export] summary.json -> {SRC_DATA} (verdict {summary['verdict']}, events complete: {summary['eventsComplete']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
