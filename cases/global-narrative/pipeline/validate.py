"""Gates G-D1..G-D4 (spec N4) over the daily ledger -> data/stats.json.

G-D1  normalization honesty: the crawl's size drifts across years; the raw
      and normalized attention curves must both exist so the dashboard can
      show the drift once, in the methodology, and share-only everywhere else.
G-D2  anchors: each dated event in config.ANCHORS must show its expected
      signature UNPROMPTED against a trailing 28-day baseline (numbers printed).
G-D3  two-source coherence: event-layer Indonesia share (own denominator) vs
      the API's normalized volume, Spearman rho >= 0.6 on the shared window.
G-D4  language honesty: translated-vs-English and foreign-vs-domestic shares
      are computed and surfaced.

Verdicts: PASS / FAIL / PENDING (the events backfill has not reached that
window yet — never a FAIL, never a PASS). Lab rule: if the data contradicts
the story, the STORY is rewritten; thresholds are never tuned to pass.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys

import numpy as np
import pandas as pd

import config


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else round(v, 4)


def load() -> pd.DataFrame:
    df = pd.read_parquet(config.LEDGER)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


# ── G-D1 ──────────────────────────────────────────────────────────────────────
def g_d1(df: pd.DataFrame) -> dict:
    if "api_norm" not in df or df["api_norm"].notna().sum() < 365:
        return {"verdict": "PENDING", "why": "TimelineVolRaw curve not cached yet"}
    yearly = df["api_norm"].groupby(df.index.year).mean().dropna()
    yearly = yearly[yearly.index < dt.date.today().year + 1]
    drift = float(yearly.max() / yearly.min())
    raw_vs_norm = float(df[["api_vol_raw", "api_vol"]].dropna().corr(method="spearman").iloc[0, 1]) \
        if "api_vol_raw" in df else None
    out = {"verdict": "PASS", "crawl_size_drift_max_over_min": round(drift, 2),
           "monitored_articles_per_day_by_year": {str(int(y)): int(v) for y, v in yearly.items()},
           "spearman_raw_vs_normalized": _num(raw_vs_norm),
           "rule": "every attention chart is a share; raw counts appear once, in the methodology"}
    print(f"[validate] G-D1 PASS — crawl size drifts {drift:.2f}x across years "
          f"({int(yearly.min()):,} → {int(yearly.max()):,} articles/day); raw≈norm ρ={raw_vs_norm:.2f}")
    return out


# ── G-D2 ──────────────────────────────────────────────────────────────────────
def _baseline(df: pd.DataFrame, d0: pd.Timestamp) -> pd.DataFrame:
    return df.loc[d0 - pd.Timedelta(days=config.BASELINE_DAYS): d0 - pd.Timedelta(days=1)]


def _sig_attention(win, base, col="api_vol", ratio_min=config.ATTENTION_RATIO) -> dict:
    if col not in win or win[col].notna().sum() == 0 or base[col].notna().sum() < 7:
        return {"verdict": "PENDING", "why": f"{col} not available"}
    peak = win[col].max()
    peak_day = win[col].idxmax().strftime("%Y-%m-%d")
    bmed = base[col].median()
    ratio = peak / bmed if bmed and bmed > 0 else float("inf")
    return {"verdict": "PASS" if ratio >= ratio_min else "FAIL", "peak": _num(peak), "peak_day": peak_day,
            "baseline_median": _num(bmed), "ratio": _num(ratio), "threshold": ratio_min}


def _sig_tone(win, base, direction: str) -> dict:
    col = "api_tone"
    if col not in win or win[col].notna().sum() == 0 or base[col].notna().sum() < 7:
        return {"verdict": "PENDING", "why": "tone curve not available"}
    bmean = base[col].mean()
    if direction == "drop":
        ext = win[col].min(); day = win[col].idxmin(); delta = bmean - ext
    else:
        ext = win[col].max(); day = win[col].idxmax(); delta = ext - bmean
    return {"verdict": "PASS" if delta >= config.TONE_DROP else "FAIL", "extreme": _num(ext),
            "extreme_day": day.strftime("%Y-%m-%d"), "baseline_mean": _num(bmean), "delta": _num(delta),
            "threshold": config.TONE_DROP}


def _sig_protest(win, base) -> dict:
    col = "protest_n"
    if col not in win or win[col].notna().sum() < len(win) or base[col].notna().sum() < 14:
        return {"verdict": "PENDING", "why": "event layer not backfilled for this window yet"}
    peak = win[col].max(); day = win[col].idxmax()
    bmed = max(float(base[col].median()), 1.0)
    ratio = peak / bmed
    return {"verdict": "PASS" if ratio >= config.PROTEST_RATIO else "FAIL", "peak": int(peak),
            "peak_day": day.strftime("%Y-%m-%d"), "baseline_median": _num(bmed), "ratio": _num(ratio),
            "threshold": config.PROTEST_RATIO}


def _quarter_min_tone(df: pd.DataFrame, d0: pd.Timestamp, window: int) -> dict:
    q = df.loc[d0.to_period("Q").start_time: d0.to_period("Q").end_time, "api_tone"].dropna()
    if q.empty:
        return {"verdict": "PENDING", "why": "tone curve not available"}
    day = q.idxmin()
    inside = d0 <= day < d0 + pd.Timedelta(days=window)
    return {"verdict": "PASS" if inside else "FAIL", "quarter_min_day": day.strftime("%Y-%m-%d"),
            "quarter_min_tone": _num(q.min()), "rank_of_anchor_window": int((q.rank()[(q.index >= d0) & (q.index < d0 + pd.Timedelta(days=window))]).min())}


def g_d2(df: pd.DataFrame) -> dict:
    anchors = []
    n_pass = n_fail = n_pending = 0
    for day, spec in config.ANCHORS.items():
        d0 = pd.Timestamp(day)
        win = df.loc[d0: d0 + pd.Timedelta(days=spec["window"] - 1)]
        base = _baseline(df, d0)
        sigs: dict[str, dict] = {}
        for s in spec["expect"]:
            if s == "attention":
                sigs[s] = _sig_attention(win, base)
            elif s == "tone_drop":
                sigs[s] = _sig_tone(win, base, "drop")
            elif s == "protest":
                sigs[s] = _sig_protest(win, base)
            elif s.startswith("theme_"):
                sigs[s] = _sig_attention(win, base, col=f"{s}_vol")
        if spec.get("quarter_min_tone"):
            sigs["quarter_min_tone"] = _quarter_min_tone(df, d0, spec["window"])
        report = {s: _sig_tone(win, base, "rise") if s == "tone_rise" else {} for s in spec.get("report_only", [])}
        verdicts = [v["verdict"] for v in sigs.values()]
        verdict = "FAIL" if "FAIL" in verdicts else "PENDING" if "PENDING" in verdicts else "PASS"
        n_pass += verdict == "PASS"; n_fail += verdict == "FAIL"; n_pending += verdict == "PENDING"
        anchors.append({"day": day, "label": spec["label"], "window_days": spec["window"], "verdict": verdict,
                        "signatures": sigs, "report_only": report})
        detail = "; ".join(
            f"{k}={v['verdict']}" + (f" ratio {v['ratio']}" if "ratio" in v else "") + (f" Δtone {v['delta']}" if "delta" in v else "")
            for k, v in sigs.items())
        extra = "; ".join(f"{k}(report) Δ={v.get('delta')}" for k, v in report.items() if v)
        print(f"[validate] G-D2 {verdict:7s} {day} {spec['label']:32s} {detail}{'; ' + extra if extra else ''}")
    verdict = "FAIL" if n_fail else "PENDING" if n_pending else "PASS"
    print(f"[validate] G-D2 {verdict}: {n_pass} pass, {n_fail} fail, {n_pending} pending of {len(anchors)} anchors")
    return {"verdict": verdict, "pass": n_pass, "fail": n_fail, "pending": n_pending, "anchors": anchors,
            "baseline_days": config.BASELINE_DAYS}


# ── G-D3 ──────────────────────────────────────────────────────────────────────
def g_d3(df: pd.DataFrame) -> dict:
    out: dict = {}
    if "share_all" not in df or "api_vol" not in df:
        return {"verdict": "PENDING", "why": "event layer or API volume missing"}
    for col in ("share_all", "share_en", "share_trans"):
        if col not in df:
            continue
        both = df[[col, "api_vol"]].dropna()
        both = both[(both[col] > 0)]
        if len(both) < 60:
            out[col] = {"n_days": int(len(both)), "verdict": "PENDING"}
            continue
        rho = float(both.corr(method="spearman").iloc[0, 1])
        out[col] = {"n_days": int(len(both)), "spearman_rho": round(rho, 3),
                    "first": both.index.min().strftime("%Y-%m-%d"), "last": both.index.max().strftime("%Y-%m-%d")}
    main = out.get("share_all", {})
    if "spearman_rho" not in main:
        verdict = "PENDING"
    else:
        verdict = "PASS" if main["spearman_rho"] >= config.G_D3_MIN_RHO else "FAIL"
    print(f"[validate] G-D3 {verdict} — " + ", ".join(
        f"{k}: ρ={v.get('spearman_rho', '—')} over {v['n_days']} days" for k, v in out.items()))
    return {"verdict": verdict, "threshold": config.G_D3_MIN_RHO, **out}


# ── G-D4 ──────────────────────────────────────────────────────────────────────
def g_d4(df: pd.DataFrame) -> dict:
    out: dict = {}
    if "n_trans" in df and df["n_events"].notna().any():
        tot = df["n_events"].sum()
        out["event_layer_translated_share"] = round(float(df["n_trans"].sum() / tot), 4) if tot else None
        out["event_layer_days"] = int(df["n_events"].notna().sum())
    if {"api_foreign_vol", "api_vol"} <= set(df.columns):
        both = df[["api_foreign_vol", "api_vol"]].dropna()
        both = both[both["api_vol"] > 0]
        out["api_foreign_share_mean"] = round(float((both["api_foreign_vol"] / both["api_vol"]).clip(upper=1).mean()), 4)
        out["api_foreign_share_by_year"] = {str(y): round(float(v), 4) for y, v in
                                            (both["api_foreign_vol"] / both["api_vol"]).clip(upper=1).groupby(both.index.year).mean().items()}
    if config.CURVES.exists():
        cur = pd.read_parquet(config.CURVES)
        lang = cur[(cur["qid"] == "indonesia") & (cur["mode"] == "TimelineLang")]
        if not lang.empty:
            m = lang.groupby("series")["value"].mean().sort_values(ascending=False)
            out["api_language_mean_intensity"] = {k: round(float(v), 4) for k, v in m.head(12).items()}
    verdict = "PASS" if out.get("api_foreign_share_mean") is not None else "PENDING"
    print(f"[validate] G-D4 {verdict} — translated share of event layer: {out.get('event_layer_translated_share')}, "
          f"foreign share of API coverage: {out.get('api_foreign_share_mean')}")
    return {"verdict": verdict, **out}


def main() -> int:
    if not config.LEDGER.exists():
        print("[validate] ledger missing — run `events.py ledger` first")
        return 1
    df = load()
    stats = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "window": {"first": df.index.min().strftime("%Y-%m-%d"), "last": df.index.max().strftime("%Y-%m-%d")},
        "coverage": {
            "api_days": int(df["api_vol"].notna().sum()) if "api_vol" in df else 0,
            "event_days": int(df["n_events"].notna().sum()) if "n_events" in df else 0,
            "event_first": df["n_events"].dropna().index.min().strftime("%Y-%m-%d") if "n_events" in df and df["n_events"].notna().any() else None,
            "event_last": df["n_events"].dropna().index.max().strftime("%Y-%m-%d") if "n_events" in df and df["n_events"].notna().any() else None,
            "event_rows": int(df["n_events"].sum()) if "n_events" in df else 0,
        },
        "gates": {"G-D1": g_d1(df), "G-D2": g_d2(df), "G-D3": g_d3(df), "G-D4": g_d4(df)},
    }
    verdicts = [g["verdict"] for g in stats["gates"].values()]
    stats["verdict"] = "FAIL" if "FAIL" in verdicts else "PENDING" if "PENDING" in verdicts else "PASS"
    config.STATS.write_text(json.dumps(stats, indent=1, allow_nan=False))
    print(f"[validate] overall {stats['verdict']} -> {config.STATS.name}")
    return 1 if stats["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
