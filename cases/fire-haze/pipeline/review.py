"""Stage R · review — the adversarial re-scoring of this case, written for the review article.

Nothing here refits or edits a published number.  It re-derives the case's own headline
statistics from the case's own outputs, under the tests the published pipeline did not run, and
writes ``data/review.json``.  ``pipeline/article.py`` turns that into the article's data layer,
so every number the review article prints is computed here rather than typed by hand.

WHAT IS RE-DERIVED, AND WHY

A · Metrics under imbalance.  The published gate is AUC.  Fire is a 3.8 % event, and on a rare
    event AUC is dominated by the enormous majority of easy true negatives: a model can post
    0.875 and still be wrong four times in five when it actually raises an alert.  Average
    precision is the honest companion, and precision at an operational alert budget is what a
    fire service would experience.  Both are computed against the same baselines the case uses.

B · Where the skill comes from.  The published cross-validation blocks by SEASON.  It does not
    block by SPACE, so the same 0.25 deg cell sits in train and test in every fold, and seven of
    the model's features are that cell's own fire history.  The ROC is therefore decomposed:
      · within-day AUC   — given a day, does it find the right cells?   (a spatial question)
      · within-cell AUC  — given a cell, does it find the right days?   (a temporal question)
    plus a deliberately stupid spatial-only baseline: each cell's mean fire rate in the OTHER
    folds, constant in time, using no weather at all.

C · The spatially blocked split.  B is diagnostic; this is the decisive test.  Cells are grouped
    into 2 deg blocks, blocks are assigned at random to K spatial folds, and for every
    (test season, spatial fold) the model is refitted on the other seasons AND the other blocks,
    then scored on exactly the published out-of-fold row set.  Directly comparable with the
    published AUC because the rows are the same rows; only the training set changed.

D · The FWI comparison, audited.  The published "beats the FWI" line is checked for what it was
    actually computed on: which seasons the CEMS record covers, how many rows survive the join,
    and whether the model half and the FWI half of the same table row were scored on the same
    sample.  Every FWI component is scored separately, because the composite index assumes a
    pine-litter fuel model and the drought codes are the part that should matter over peat.

E · The trajectory, replayed over every episode instead of one.  Ensemble survival and spread as
    functions of travel time from the stored parcel paths; how much of the province attribution
    survives truncating the trajectory at 24 and 48 hours; and the distribution of attribution
    over all receptor-days, so a single episode can be placed in its population.

Run:  uv run python pipeline/review.py                     A, B, D, E   (minutes)
      uv run python pipeline/review.py --only spatial      C            (tens of minutes)
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone

import config
import util
from util import log

OUT = config.DATA_DIR / "review.json"
OOF = config.DATA_DIR / "risk_oof.parquet"

SPATIAL_BLOCK_DEG = 2.0     # >> the 0.25 deg grid, and >> the range of fire autocorrelation
N_SPATIAL_FOLDS = 4
SPATIAL_LEADS = (1, 7)      # the two ends of the published claim
FWI_COMPONENTS = ["fwi", "ffmc", "dmc", "dc", "bui", "kbdi"]


# ── metrics ───────────────────────────────────────────────────────────────────────────
def auc(y, p):
    from sklearn.metrics import roc_auc_score
    import numpy as np
    y = np.asarray(y)
    return None if y.min() == y.max() else float(roc_auc_score(y, p))


def ap(y, p):
    from sklearn.metrics import average_precision_score
    import numpy as np
    y = np.asarray(y)
    return None if y.min() == y.max() else float(average_precision_score(y, p))


def brier(y, p):
    import numpy as np
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def prec_at_budget(y, p, frac):
    """Precision and recall if you alert on the top `frac` of cell-days.

    This is the number a fire service experiences.  A 1 % alert budget over ~1,950 land cells is
    about twenty cells a day, which is roughly what a patrol schedule can actually visit.
    """
    import numpy as np
    y = np.asarray(y)
    p = np.asarray(p, float)
    k = max(1, int(round(len(p) * frac)))
    idx = np.argpartition(-p, k - 1)[:k]
    tp = int(y[idx].sum())
    pos = int(y.sum())
    return {"budget": frac, "k": k, "precision": tp / k,
            "recall": (tp / pos) if pos else None, "tp": tp}


def _rankdata(a):
    """Average ranks, ties shared.  scipy is not a dependency of this case."""
    import numpy as np
    a = np.asarray(a, float)
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), float)
    sa = a[order]
    i = 0
    n = len(sa)
    while i < n:
        j = i
        while j + 1 < n and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def grouped_auc(df, key, pcol, ycol="y"):
    """AUC restricted to comparisons INSIDE a group — a Mann-Whitney U pooled over groups.

    Pooling U rather than averaging per-group AUCs weights each group by the positive/negative
    pairs it contributes, which is what "the ROC with the between-group variation removed"
    means.  Ties count a half, exactly as roc_auc_score does.
    """
    import numpy as np
    tot_u = 0.0
    tot_pairs = 0.0
    for _, g in df.groupby(key, sort=False, observed=True):
        yv = g[ycol].to_numpy()
        npos = int(yv.sum())
        nneg = len(yv) - npos
        if npos == 0 or nneg == 0:
            continue
        r = _rankdata(g[pcol].to_numpy())
        tot_u += float(r[yv == 1].sum()) - npos * (npos + 1) / 2.0
        tot_pairs += npos * nneg
    if tot_pairs <= 0:
        return None, 0
    return tot_u / tot_pairs, int(tot_pairs)


# ── A + B · re-score the published out-of-fold predictions ────────────────────────────
def rescore(leads) -> dict:
    import pandas as pd
    util.require(OOF.exists(), "no risk_oof.parquet — run `make risk` first")
    oof = pd.read_parquet(OOF)
    oof = oof[oof["path"] == "forecast"].copy()
    out = {"sample_rows": int(len(oof)),
           "folds": sorted(int(f) for f in oof["fold"].unique()),
           "n_cells": int(oof["cell"].nunique()),
           "per_lead": {}}
    for L in leads:
        s = oof[oof["lead"] == L].copy()
        if s.empty:
            continue
        y = s["y"].to_numpy()
        base = float(y.mean())
        row = {"n": int(len(s)), "positives": int(y.sum()), "base_rate": base, "scores": {}}

        for k, col in (("model", "p"), ("climatology", "p_clim"), ("persistence", "p_persist")):
            if s[col].isna().all():
                continue
            m = {"auc": auc(y, s[col]), "ap": ap(y, s[col]), "brier": brier(y, s[col])}
            m["ap_lift"] = (m["ap"] / base) if m["ap"] else None
            m["at_1pct"] = prec_at_budget(y, s[col], 0.01)
            m["at_5pct"] = prec_at_budget(y, s[col], 0.05)
            row["scores"][k] = m

        s["daykey"] = s["day"].values.astype("datetime64[D]").astype("int64")
        dec = {}
        for k, col in (("model", "p"), ("climatology", "p_clim"), ("persistence", "p_persist")):
            if s[col].isna().all():
                continue
            a_day, n_day = grouped_auc(s, "daykey", col)
            a_cell, n_cell = grouped_auc(s, "cell", col)
            dec[k] = {"within_day_auc": a_day, "within_day_pairs": n_day,
                      "within_cell_auc": a_cell, "within_cell_pairs": n_cell}
        row["decomposition"] = dec

        # the deliberately stupid spatial-only baseline: this cell's rate in the OTHER folds
        parts = []
        for f in sorted(s["fold"].unique()):
            others = s[s["fold"] != f]
            rate = others.groupby("cell")["y"].mean()
            prior = float(others["y"].mean())
            t = s[s["fold"] == f].copy()
            t["p_cellrate"] = t["cell"].map(rate).fillna(prior).astype("float32")
            parts.append(t)
        t = pd.concat(parts, ignore_index=True)
        yy = t["y"].to_numpy()
        row["cell_rate_only"] = {
            "auc": auc(yy, t["p_cellrate"]), "ap": ap(yy, t["p_cellrate"]),
            "at_1pct": prec_at_budget(yy, t["p_cellrate"], 0.01),
            "note": ("each cell's own fire rate in the other folds — constant in time, "
                     "using no weather at all"),
        }
        row["model_vs_cellrate_spearman"] = float(
            pd.Series(_rankdata(t["p"].to_numpy())).corr(
                pd.Series(_rankdata(t["p_cellrate"].to_numpy()))))
        out["per_lead"][str(L)] = row
        log(f"  lead {L}d: AUC {row['scores']['model']['auc']:.4f}  AP "
            f"{row['scores']['model']['ap']:.4f} (base {base:.4f})  "
            f"within-cell AUC {dec['model']['within_cell_auc']:.4f}  "
            f"cell-rate-only AUC {row['cell_rate_only']['auc']:.4f}")
    return out


# ── D · the FWI comparison, audited ───────────────────────────────────────────────────
def fwi_audit(leads) -> dict:
    import numpy as np
    import pandas as pd
    import pyarrow.dataset as ds
    import pyarrow.compute as pc
    p = config.DATA_DIR / "fwi.parquet"
    if not p.exists():
        return {"status": "no fwi.parquet on disk"}
    dset = ds.dataset(p, format="parquet")

    day_tbl = dset.to_table(columns=["day"])
    yr = pd.Series(day_tbl.column("day").to_pandas()).dt.year
    cover = yr.value_counts().sort_index()
    del day_tbl, yr
    out = {"status": "ok",
           "total_rows": int(cover.sum()),
           "years_on_disk": [int(y) for y in cover.index],
           "rows_by_year": {str(int(k)): int(v) for k, v in cover.items()},
           "per_lead": {}}

    oof = pd.read_parquet(OOF)
    oof = oof[oof["path"] == "forecast"]
    folds = sorted(int(f) for f in oof["fold"].unique())
    have = set(int(y) for y in cover.index)
    out["oof_folds"] = folds
    out["fold_years_with_fwi"] = [y for y in folds if y in have]
    out["fold_years_without_fwi"] = [y for y in folds if y not in have]
    out["anchor_years_with_fwi"] = [int(y) for y in config.ANCHOR_YEARS if int(y) in have]
    out["anchor_years_without_fwi"] = [int(y) for y in config.ANCHOR_YEARS if int(y) not in have]

    # only the fold years exist in the join, so read only those
    keep_years = [y for y in folds if y in have]
    if not keep_years:
        out["status"] = "no overlap between the CEMS record and the model's held-out seasons"
        return out
    lo = pd.Timestamp(f"{min(keep_years)}-01-01")
    hi = pd.Timestamp(f"{max(keep_years)}-12-31")
    f = dset.to_table(columns=["cell", "day"] + FWI_COMPONENTS,
                      filter=(pc.field("day") >= pc.scalar(lo))
                      & (pc.field("day") <= pc.scalar(hi))).to_pandas()
    f["day"] = pd.to_datetime(f["day"])
    f = f[f["day"].dt.year.isin(keep_years)]
    log(f"  FWI: {len(f):,} rows over {keep_years}")

    for L in leads:
        s = oof[oof["lead"] == L].copy()
        if s.empty:
            continue
        s["day"] = pd.to_datetime(s["day"])
        j = s.merge(f, on=["cell", "day"], how="left")
        per_fold = {str(int(k)): {"oof_rows": int(len(g)),
                                  "rows_with_fwi": int(g["fwi"].notna().sum()),
                                  "share": float(g["fwi"].notna().mean())}
                    for k, g in j.groupby("fold")}
        matched = j[j["fwi"].notna()]
        rec = {"oof_rows": int(len(j)), "rows_with_fwi": int(len(matched)),
               "join_share": float(len(matched) / max(len(j), 1)),
               "per_fold": per_fold,
               "folds_with_any_fwi": sorted(int(k) for k, v in per_fold.items()
                                            if v["rows_with_fwi"] > 0)}
        if len(matched) > 5000:
            y = matched["y"].to_numpy()
            rec["base_rate_matched"] = float(y.mean())
            comps = {}
            for c in FWI_COMPONENTS:
                if matched[c].notna().sum() < 5000:
                    continue
                v = matched[c].fillna(matched[c].median())
                comps[c] = {"auc": auc(y, v), "ap": ap(y, v),
                            "at_1pct": prec_at_budget(y, v, 0.01)}
            rec["components"] = comps
            rec["like_for_like"] = {
                "n": int(len(matched)),
                "base_rate": float(y.mean()),
                "model_auc": auc(y, matched["p"]),
                "model_ap": ap(y, matched["p"]),
                "model_at_1pct": prec_at_budget(y, matched["p"], 0.01),
                "fwi_auc": comps.get("fwi", {}).get("auc"),
                "fwi_ap": comps.get("fwi", {}).get("ap"),
                "climatology_auc": auc(y, matched["p_clim"]) if matched["p_clim"].notna().all()
                else None,
                "best_component": (max(comps.items(), key=lambda kv: kv[1]["auc"] or 0)[0]
                                   if comps else None),
            }
            # and the same again, one held-out season at a time, so the sample is visible
            rec["by_fold"] = {}
            for k, g in matched.groupby("fold"):
                if len(g) < 5000 or g["y"].nunique() < 2:
                    continue
                yy = g["y"].to_numpy()
                rec["by_fold"][str(int(k))] = {
                    "n": int(len(g)), "base_rate": float(yy.mean()),
                    "model_auc": auc(yy, g["p"]), "fwi_auc": auc(yy, g["fwi"]),
                    "model_ap": ap(yy, g["p"]), "fwi_ap": ap(yy, g["fwi"])}
        out["per_lead"][str(L)] = rec
        log(f"  FWI lead {L}d: {rec['rows_with_fwi']:,} of {rec['oof_rows']:,} out-of-fold rows "
            f"carry an FWI value ({rec['join_share']:.1%}); "
            f"seasons {rec['folds_with_any_fwi']}")
    return out


# ── C · the spatially blocked refit ───────────────────────────────────────────────────
def _block_map(df):
    """2 deg blocks, assigned to N_SPATIAL_FOLDS groups by a seeded shuffle.

    Assigning blocks at random rather than in stripes matters: a stripe pattern puts every
    held-out block against a training block and leaks straight back across the seam.
    """
    import numpy as np
    bi = np.floor(df["clat"].to_numpy() / SPATIAL_BLOCK_DEG).astype(int)
    bj = np.floor(df["clon"].to_numpy() / SPATIAL_BLOCK_DEG).astype(int)
    key = bi * 1000 + bj
    uniq = np.unique(key)
    rng = np.random.default_rng(2026)
    assign = rng.permutation(len(uniq)) % N_SPATIAL_FOLDS
    lut = dict(zip(uniq.tolist(), assign.tolist()))
    return np.array([lut[k] for k in key]), len(uniq)


def spatial_test(leads) -> dict:
    """Refit under space-AND-season blocking, scoring exactly the published OOF row set."""
    import lightgbm as lgb
    import numpy as np
    import pandas as pd
    import risk as R

    yrs = R.panel_years()
    train_pool = [y for y in yrs if y not in config.ANCHOR_YEARS]
    folds = [y for y in train_pool if y not in (yrs[0], yrs[-1])]
    probe = R.load_year(train_pool[0], ["cell", "clat", "clon"]).drop_duplicates("cell")
    b, n_blocks = _block_map(probe)
    probe["blk"] = b
    blk = dict(zip(probe["cell"].tolist(), probe["blk"].tolist()))
    log(f"spatial: {len(probe):,} cells in {n_blocks} blocks of {SPATIAL_BLOCK_DEG} deg -> "
        f"{N_SPATIAL_FOLDS} folds; seasons {folds}")
    out = {"block_deg": SPATIAL_BLOCK_DEG, "n_spatial_folds": N_SPATIAL_FOLDS,
           "n_blocks": int(n_blocks), "n_cells": int(len(probe)), "seasons": folds,
           "cells_per_fold": {str(k): int(v)
                              for k, v in probe["blk"].value_counts().sort_index().items()},
           "per_lead": {}}

    for L in leads:
        cols = None
        scored = []
        for test_year in folds:
            tr = [y for y in train_pool if y != test_year]
            calib = tr[-1] if tr[-1] != test_year else tr[-2]
            tr = [y for y in tr if y != calib]
            te = R.load_year(test_year)
            te = te[te[f"y{L}"].notna()].reset_index(drop=True)
            te["blk"] = te["cell"].map(blk)
            if cols is None:
                cols = R.feature_cols(te, "forecast")
            cal_full = R.load_year(calib)
            cal_full = cal_full[cal_full[f"y{L}"].notna()]
            cal_full["blk"] = cal_full["cell"].map(blk)
            trd = []
            for y in tr:
                d = R.load_year(y)
                d = d[d[f"y{L}"].notna()]
                d["blk"] = d["cell"].map(blk)
                trd.append(d)
            trd = pd.concat(trd, ignore_index=True)
            rng = np.random.default_rng(17)
            for bi in range(N_SPATIAL_FOLDS):
                t = te[te["blk"] == bi]
                if t.empty:
                    continue
                sub = trd[trd["blk"] != bi]
                pos = sub[sub[f"y{L}"] == 1]
                neg = sub[sub[f"y{L}"] == 0]
                take = rng.random(len(neg)) < R.NEG_RATE
                smp = pd.concat([pos, neg[take]], ignore_index=True)
                w = np.where(smp[f"y{L}"].to_numpy() == 1, 1.0, 1.0 / R.NEG_RATE)
                booster = lgb.train(R.LGB_PARAMS,
                                    lgb.Dataset(smp[cols], label=smp[f"y{L}"].to_numpy(),
                                                weight=w),
                                    num_boost_round=R.N_ROUNDS)
                del smp, pos, neg
                cal = cal_full[cal_full["blk"] != bi]
                iso = R.isotonic_fit(booster.predict(cal[cols]), cal[f"y{L}"].to_numpy())
                q = t[["cell", "day", f"y{L}"]].rename(columns={f"y{L}": "y"}).copy()
                q["p"] = np.clip(iso.predict(booster.predict(t[cols])), 1e-6, 1 - 1e-6)
                q["fold"] = test_year
                q["blk"] = bi
                scored.append(q)
                log(f"  lead {L}d season {test_year} block {bi}: train {len(sub):,} "
                    f"test {len(t):,} AUC {auc(q['y'], q['p']):.4f}")
                del booster, sub, cal
            del trd, te, cal_full
        s = pd.concat(scored, ignore_index=True)
        y = s["y"].to_numpy()
        base = float(y.mean())
        rec = {"n": int(len(s)), "positives": int(y.sum()), "base_rate": base,
               "auc": auc(y, s["p"]), "ap": ap(y, s["p"]),
               "at_1pct": prec_at_budget(y, s["p"], 0.01),
               "at_5pct": prec_at_budget(y, s["p"], 0.05),
               "per_season": {str(int(k)): {"n": int(len(g)), "auc": auc(g["y"], g["p"]),
                                            "ap": ap(g["y"], g["p"])}
                              for k, g in s.groupby("fold")},
               "per_block": {str(int(k)): {"n": int(len(g)), "auc": auc(g["y"], g["p"]),
                                           "ap": ap(g["y"], g["p"]),
                                           "base_rate": float(g["y"].mean())}
                             for k, g in s.groupby("blk")}}
        rec["ap_lift"] = (rec["ap"] / base) if rec["ap"] else None
        out["per_lead"][str(L)] = rec
        log(f"spatial lead {L}d: AUC {rec['auc']:.4f}  AP {rec['ap']:.4f}  base {base:.4f}")
    return out


# ── E · the trajectory, replayed over every episode ───────────────────────────────────
def trajectory_review() -> dict:
    import numpy as np
    import pandas as pd
    bt = config.DATA_DIR / "back_traj.parquet"
    at = config.DATA_DIR / "attribution.parquet"
    if not bt.exists():
        return {"status": "no trajectory output on disk"}
    tr = pd.read_parquet(bt)
    tr["day"] = pd.to_datetime(tr["day"])
    out = {"status": "ok", "parcel_rows": int(len(tr)),
           "receptor_days": int(tr.groupby(["receptor", "day"]).ngroups),
           "years": sorted(int(v) for v in tr["day"].dt.year.unique())}

    # E1 · ensemble survival and spread as functions of travel time
    HRS = [6, 12, 24, 36, 48, 60, 72]
    surv = {h: [0, 0] for h in HRS}          # [reached, released]
    spread = {h: [] for h in HRS}
    for (_rec, _day), g in tr.groupby(["receptor", "day"], sort=False):
        n = len(g)
        pos = {h: [] for h in HRS}
        for la, lo, hh in zip(g["lat"], g["lon"], g["hours"]):
            la = np.asarray(la, float)
            lo = np.asarray(lo, float)
            hh = np.abs(np.asarray(hh, float))
            for h in HRS:
                k = np.where(np.abs(hh - h) < 1e-6)[0]
                if len(k) and np.isfinite(la[k[0]]) and np.isfinite(lo[k[0]]):
                    pos[h].append((la[k[0]], lo[k[0]]))
        for h in HRS:
            surv[h][0] += len(pos[h])
            surv[h][1] += n
            if len(pos[h]) >= 5:
                a = np.array(pos[h])
                mla, mlo = a[:, 0].mean(), a[:, 1].mean()
                dx = (a[:, 1] - mlo) * 111.32 * math.cos(math.radians(mla))
                dy = (a[:, 0] - mla) * 110.57
                spread[h].append(float(np.sqrt((dx ** 2 + dy ** 2).mean())))
    out["ensemble"] = [{"hours": h,
                        "surviving_share": surv[h][0] / max(surv[h][1], 1),
                        "n_receptor_days": len(spread[h]),
                        "spread_km_median": float(np.median(spread[h])) if spread[h] else None,
                        "spread_km_p90": float(np.quantile(spread[h], 0.9)) if spread[h] else None}
                       for h in HRS]
    log("  ensemble spread: " + ", ".join(
        f"{r['hours']}h {r['spread_km_median']:.0f}km/{r['surviving_share']:.0%}"
        for r in out["ensemble"] if r["spread_km_median"] is not None))

    # E2 · how much of the province attribution survives truncating the trajectory
    fires = config.DATA_DIR / "fires_daily.parquet"
    static = config.DATA_DIR / "cell_static.parquet"
    if fires.exists() and static.exists():
        st = pd.read_parquet(static, columns=["cell", "adm1_name"])
        cell_prov = dict(zip(st["cell"].tolist(), st["adm1_name"].tolist()))
        fd = pd.read_parquet(fires, columns=["cell", "day", "frp_sum"])
        fd["day"] = pd.to_datetime(fd["day"])
        gid = list(tr.groupby(["receptor", "day"], sort=False))
        rng = np.random.default_rng(11)
        pick = rng.permutation(len(gid))[:800]
        stab = []
        for i in pick:
            (rec, day), g = gid[i]
            win = fd[(fd["day"] >= day - pd.Timedelta(days=4)) & (fd["day"] <= day)]
            if win.empty:
                continue
            fsum = win.groupby("cell", as_index=False)["frp_sum"].sum()
            shares = {}
            for cut in (24, 48, 72):
                la, lo = [], []
                for a, b_, hh in zip(g["lat"], g["lon"], g["hours"]):
                    a = np.asarray(a, float)
                    b_ = np.asarray(b_, float)
                    hh = np.abs(np.asarray(hh, float))
                    m = hh <= cut + 1e-9
                    la.append(a[m])
                    lo.append(b_[m])
                la = np.concatenate(la) if la else np.array([])
                lo = np.concatenate(lo) if lo else np.array([])
                ok = np.isfinite(la) & np.isfinite(lo)
                if ok.sum() < 10:
                    continue
                clat, clon = util.snap_cell(la[ok], lo[ok])
                visits = pd.Series(util.cell_key(clat, clon)).value_counts()
                f2 = fsum.copy()
                f2["visits"] = f2["cell"].map(visits).fillna(0.0)
                f2["score"] = f2["frp_sum"] * f2["visits"]
                tot = float(f2["score"].sum())
                if tot <= 0:
                    continue
                f2["prov"] = f2["cell"].map(cell_prov)
                shares[cut] = (f2.groupby("prov")["score"].sum() / tot).sort_values(
                    ascending=False)
            if 72 not in shares or len(shares) < 2:
                continue
            top72 = shares[72].index[0]
            row = {"receptor": str(rec), "day": str(pd.Timestamp(day).date()),
                   "top_province": str(top72), "share72": float(shares[72].iloc[0]),
                   "n_prov72": int(len(shares[72]))}
            for cut in (24, 48):
                if cut in shares:
                    row[f"top{cut}_same"] = bool(shares[cut].index[0] == top72)
                    row[f"share{cut}_of_top72"] = float(shares[cut].get(top72, 0.0))
            stab.append(row)
        if stab:
            df = pd.DataFrame(stab)
            out["truncation"] = {
                "episodes_tested": int(len(df)),
                "top_province_same_at_48h": float(df["top48_same"].mean())
                if "top48_same" in df else None,
                "top_province_same_at_24h": float(df["top24_same"].mean())
                if "top24_same" in df else None,
                "median_top_share_72h": float(df["share72"].median()),
                "median_n_provinces_72h": float(df["n_prov72"].median()),
                "note": ("the same parcels, the same fires, the same residence-time x FRP "
                         "weighting — only the assumed travel window changes"),
            }
            log(f"  truncation: top province unchanged at 48 h on "
                f"{out['truncation']['top_province_same_at_48h']:.1%} of "
                f"{len(df)} episodes, at 24 h on "
                f"{out['truncation']['top_province_same_at_24h']:.1%}")

    # E3 · the population a single episode sits in
    if at.exists():
        a = pd.read_parquet(at)
        a["day"] = pd.to_datetime(a["day"])
        ngroups = a.groupby(["receptor", "day"]).ngroups
        nas = a[a["province"] == "no attributable source"]
        real = a[a["province"] != "no attributable source"]
        top = (real.sort_values("share", ascending=False)
                   .groupby(["receptor", "day"], as_index=False).first())
        out["attribution"] = {
            "receptor_days": int(ngroups),
            "no_attributable_source_days": int(nas.groupby(["receptor", "day"]).ngroups),
            "no_attributable_source_share":
                float(nas.groupby(["receptor", "day"]).ngroups / max(ngroups, 1)),
            "median_top_share": float(top["share"].median()),
            "median_top_agreement": float(top["agreement"].median()),
            "top_share_over_half": float((top["share"] > 0.5).mean()),
            "agreement_over_half": float((top["agreement"] > 0.5).mean()),
            "provinces_named": int(real["province"].nunique()),
        }
        sg = top[top["receptor"] == "singapore"]
        if len(sg):
            counts = sg["province"].value_counts()
            out["attribution"]["singapore"] = {
                "episode_days": int(len(sg)),
                "distinct_top_provinces": int(len(counts)),
                "top_provinces": [{"province": str(k), "days": int(v),
                                   "share_of_days": float(v / len(sg))}
                                  for k, v in counts.head(8).items()],
                "median_top_share": float(sg["share"].median()),
                "median_agreement": float(sg["agreement"].median()),
                "top_share_over_half": float((sg["share"] > 0.5).mean()),
            }

    bc = config.DATA_DIR / "bearing_check.parquet"
    if bc.exists():
        b2 = pd.read_parquet(bc)
        out["bearing"] = {
            "rows": int(len(b2)),
            "within_30": float((b2["diff_deg"] <= 30).mean()),
            "median_diff": float(b2["diff_deg"].median()),
            "p90_diff": float(b2["diff_deg"].quantile(0.9)),
            "per_receptor": {str(k): {"n": int(len(g)),
                                      "within_30": float((g["diff_deg"] <= 30).mean()),
                                      "median_diff": float(g["diff_deg"].median()),
                                      "p90_diff": float(g["diff_deg"].quantile(0.9))}
                             for k, g in b2.groupby("receptor")},
        }
    return out


def main() -> None:
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--only", default=None,
                     help="comma list of: rescore,fwi,traj,spatial (default: all but spatial)")
    args = ap_.parse_args()
    leads = list(config.LEAD_DAYS)
    want = set(args.only.split(",")) if args.only else {"rescore", "fwi", "traj"}

    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    if "rescore" in want:
        log("review A+B: re-scoring the published out-of-fold predictions")
        out["rescore"] = rescore(leads)
    if "fwi" in want:
        log("review D: auditing the FWI comparison")
        out["fwi"] = fwi_audit(leads)
    if "traj" in want:
        log("review E: replaying the trajectories over every episode")
        out["trajectory"] = trajectory_review()
    if "spatial" in want:
        log("review C: the spatially blocked refit")
        out["spatial"] = spatial_test(SPATIAL_LEADS)

    out["generated"] = datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(_nan_safe(out), indent=1))
    log(f"review -> {OUT}")


def _nan_safe(o):
    if isinstance(o, dict):
        return {k: _nan_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_nan_safe(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


if __name__ == "__main__":
    main()
