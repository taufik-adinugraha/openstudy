"""Stage 8 · risk — ignition probability per cell per day, at 1, 3 and 7 days' lead.

WHAT MAKES THIS HARD IS NOT THE MODEL.  IT IS THE SCORING.
Fire is rare and violently seasonal.  A model that predicts "September in Riau" and nothing else
posts an AUC around 0.9 and is worth nothing to anybody, because everyone already knows about
September.  So the gates are built around baselines that already contain the easy knowledge:

  climatology   per-cell day-of-year detection frequency, smoothed +-15 days, and computed ONLY
                on the training folds.  Fitting it on the full record and then scoring against it
                would be a baseline that has seen the test set — a surprisingly common way to
                make a model look good.
  persistence   "it burned here in the last 7 days".  Cheap, strong, and the thing an operator
                would actually do without a model.
  CEMS FWI      the operational Canadian Fire Weather Index, an index we did not design and
                cannot tune.  Given the fairest possible treatment: isotonically calibrated to
                probability on the same training folds, so it is compared at its best.
                ** PENDING while the EWDS policy click is outstanding. **

FIVE THINGS DONE DELIBERATELY

1.  BLOCKED-BY-SEASON CROSS-VALIDATION, NEVER RANDOM.  Adjacent cell-days are the same row
    twice; a random split puts 14 September in train and 15 September in test and reports skill
    that does not exist.  Each fold holds out one whole calendar year.
2.  A SEPARATE CALIBRATION SEASON INSIDE EACH FOLD.  The model trains on N-2 years, isotonic
    regression is fitted on a year the model never saw, and only then is the test year scored.
    Calibrating on the test year would be scoring a model on data used to fit its own output
    transform.
3.  NEGATIVE SUBSAMPLING WITH WEIGHTS, AND EVALUATION ON THE FULL PANEL.  Training on 10 M rows
    on a shared 4-vCPU box is not viable, so negatives are sampled and carry weight 1/rate.
    Evaluation never uses the sample: every metric and every reliability bin is computed on the
    complete, unsampled test year, or the base rate — and therefore the Brier score — would be
    wrong by the sampling factor.
4.  TWO PATHS, SCORED SEPARATELY.  ``forecast`` sees only days <= t; ``reanalysis`` also sees the
    weather actually observed over the lead window.  The gap is the cost of not knowing the
    weather, and it is published rather than assumed away.
5.  THE ANCHORS ARE NEVER TRAINED ON.  2015 and 2019 carry ``is_anchor`` from features.py and
    are excluded from every fold, then scored blind for G-J5.

OUTPUT
------
``data/risk_days.parquet``      cell x day x lead, calibrated probability, for the export days
``data/risk_national.parquet``  day x lead: national mean/max risk and cells above threshold
``data/risk_oof.parquet``       out-of-fold predictions and baselines — what validate.py scores
``data/risk_meta.json``         metrics per lead per path, reliability bins, SHAP by family
"""

from __future__ import annotations

import argparse
import json
from datetime import date

import config
import util
from util import log

PANEL_DIR = config.DATA_DIR / "panel"
OOF_OUT = config.DATA_DIR / "risk_oof.parquet"
DAYS_OUT = config.DATA_DIR / "risk_days.parquet"
NAT_OUT = config.DATA_DIR / "risk_national.parquet"
META_OUT = config.DATA_DIR / "risk_meta.json"
MODEL_DIR = config.DATA_DIR / "model"
SCRATCH = config.DATA_DIR / "risk_scratch"

NEG_RATE = 0.06             # keep every positive, 6 % of negatives, weight 1/0.06
CLIM_WINDOW = 15            # +-15 days around the day of year
LGB_PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=63,
                  min_data_in_leaf=200, feature_fraction=0.8, bagging_fraction=0.8,
                  bagging_freq=1, lambda_l2=1.0, num_threads=3, verbose=-1)
N_ROUNDS = 400


def panel_years() -> list[int]:
    return sorted(int(p.stem) for p in PANEL_DIR.glob("*.parquet"))


def load_year(y: int, cols: list[str] | None = None):
    import pandas as pd
    return pd.read_parquet(PANEL_DIR / f"{y}.parquet", columns=cols)


def feature_cols(df, path: str) -> list[str]:
    import features as F
    drop = {"cell", "day", "clat", "clon", "adm1_name", "adm1_iso", "country",
            "n_fire", "frp_sum", "nbr_fire", "is_anchor"}
    drop |= {f"y{L}" for L in config.LEAD_DAYS}
    cols = [c for c in df.columns if c not in drop and df[c].dtype.kind in "fiub"]
    if path == "forecast":
        cols = [c for c in cols if c not in F.FORECAST_DROP]
    return cols


# ── baselines ─────────────────────────────────────────────────────────────────────────
def climatology(train_years: list[int], lead: int):
    """Per-cell day-of-year detection frequency from the TRAINING years only.

    Smoothed over a +-CLIM_WINDOW day window so a cell that burned on 12 September 2014 and
    13 September 2017 is not credited with a spike on the 12th and a hole on the 13th.
    """
    import numpy as np
    import pandas as pd
    rows = []
    for y in train_years:
        d = load_year(y, ["cell", "day", f"y{lead}"])
        d["doy"] = pd.to_datetime(d["day"]).dt.dayofyear
        rows.append(d[["cell", "doy", f"y{lead}"]])
    d = pd.concat(rows, ignore_index=True)
    g = d.groupby(["cell", "doy"])[f"y{lead}"].agg(["sum", "count"]).reset_index()
    # circular smoothing over the day-of-year axis
    full = (g.pivot(index="cell", columns="doy", values="sum")
              .reindex(columns=range(1, 367)).fillna(0.0))
    cnt = (g.pivot(index="cell", columns="doy", values="count")
             .reindex(columns=range(1, 367)).fillna(0.0))
    k = 2 * CLIM_WINDOW + 1
    s_sum = np.apply_along_axis(
        lambda a: np.convolve(np.r_[a[-CLIM_WINDOW:], a, a[:CLIM_WINDOW]],
                              np.ones(k), "valid"), 1, full.to_numpy())
    s_cnt = np.apply_along_axis(
        lambda a: np.convolve(np.r_[a[-CLIM_WINDOW:], a, a[:CLIM_WINDOW]],
                              np.ones(k), "valid"), 1, cnt.to_numpy())
    prior = float(d[f"y{lead}"].mean())
    # Laplace-style shrinkage toward the national prior: a cell with 30 observations should not
    # be allowed to claim a probability of exactly zero
    p = (s_sum + 10 * prior) / (s_cnt + 10)
    out = pd.DataFrame(p, index=full.index, columns=range(1, 367)).stack().reset_index()
    out.columns = ["cell", "doy", "p_clim"]
    out["p_clim"] = out["p_clim"].astype("float32")
    return out


def isotonic_fit(p, y, w=None):
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1 - 1e-6)
    iso.fit(p, y, sample_weight=w)
    return iso


# ── metrics ───────────────────────────────────────────────────────────────────────────
def brier(p, y):
    import numpy as np
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2))


def bss(p, y, ref):
    b, br = brier(p, y), brier(ref, y)
    return float(1.0 - b / br) if br > 0 else float("nan")


def auc(p, y):
    from sklearn.metrics import roc_auc_score
    import numpy as np
    y = np.asarray(y)
    if y.min() == y.max():
        return float("nan")
    return float(roc_auc_score(y, p))


def reliability(p, y, bins: int = 12):
    import numpy as np
    import pandas as pd
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    # quantile bins: equal-width bins put 99 % of a rare-event forecast in the first bin and
    # publish a reliability diagram with one dot on it
    qs = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)))
    if len(qs) < 3:
        return []
    idx = np.clip(np.digitize(p, qs[1:-1]), 0, len(qs) - 2)
    out = []
    for b in range(len(qs) - 1):
        m = idx == b
        if m.sum() < 50:
            continue
        out.append({"bin": b, "n": int(m.sum()), "p_mean": float(p[m].mean()),
                    "observed": float(y[m].mean()),
                    "p_lo": float(qs[b]), "p_hi": float(qs[b + 1])})
    return out


# ── training ──────────────────────────────────────────────────────────────────────────
def sample_train(years: list[int], lead: int, cols: list[str]):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(17)
    X, Y, W = [], [], []
    for y in years:
        d = load_year(y)
        d = d[d[f"y{lead}"].notna()]
        pos = d[d[f"y{lead}"] == 1]
        neg = d[d[f"y{lead}"] == 0]
        take = rng.random(len(neg)) < NEG_RATE
        sub = pd.concat([pos, neg[take]], ignore_index=True)
        X.append(sub[cols])
        Y.append(sub[f"y{lead}"].to_numpy())
        W.append(np.where(sub[f"y{lead}"].to_numpy() == 1, 1.0, 1.0 / NEG_RATE))
    return (pd.concat(X, ignore_index=True), np.concatenate(Y), np.concatenate(W))


def fit_fold(train_years, calib_year, test_year, lead, path):
    """One (lead, path, held-out season) fold.  Returns the scored test year."""
    import lightgbm as lgb
    import numpy as np
    import pandas as pd

    probe = load_year(train_years[0])
    cols = feature_cols(probe, path)
    del probe
    Xtr, ytr, wtr = sample_train(train_years, lead, cols)
    ds = lgb.Dataset(Xtr, label=ytr, weight=wtr, free_raw_data=True)
    booster = lgb.train(LGB_PARAMS, ds, num_boost_round=N_ROUNDS)
    del Xtr, ds

    cal = load_year(calib_year)
    cal = cal[cal[f"y{lead}"].notna()]
    pc = booster.predict(cal[cols])
    iso = isotonic_fit(pc, cal[f"y{lead}"].to_numpy())
    # PERSISTENCE IS CALIBRATED TOO, on the same held-out season, for the same reason the FWI is.
    # "It burned here in the last 7 days" scored as a bare 0/1 probability collects an appalling
    # Brier score and hands the model a skill score of +0.79 that means nothing.  Turning the
    # trailing count into a probability by isotonic regression is what an operator using
    # persistence would actually get, and it is the only version worth beating.
    iso_p = isotonic_fit(cal["fire_7d"].fillna(0).to_numpy(), cal[f"y{lead}"].to_numpy())
    del cal

    te = load_year(test_year)
    te = te[te[f"y{lead}"].notna()].reset_index(drop=True)
    p_raw = booster.predict(te[cols])
    out = te[["cell", "day", "clat", "clon", "adm1_name", f"y{lead}"]].copy()
    out = out.rename(columns={f"y{lead}": "y"})
    out["p"] = np.clip(iso.predict(p_raw), 1e-6, 1 - 1e-6).astype("float32")
    out["p_persist"] = np.clip(iso_p.predict(te["fire_7d"].fillna(0).to_numpy()),
                               1e-6, 1 - 1e-6).astype("float32")
    out["persist_hit"] = (te["fire_7d"].fillna(0) > 0).astype("int8")
    out["lead"] = lead
    out["path"] = path
    out["fold"] = test_year
    return out, booster, cols, iso


def predict_only(days_back: int = 45) -> None:
    """The daily-refresh path: reuse the fitted boosters, score only the newest days.

    Deliberately refuses to run without a saved model rather than silently retraining — a cron
    job that quietly refits on a partial panel would publish a different model every morning and
    nobody would notice until the numbers moved.
    """
    import lightgbm as lgb
    import numpy as np
    import pandas as pd
    models = sorted(MODEL_DIR.glob("lgbm_lead*.txt"))
    util.require(bool(models), "--predict-only needs a fitted model; run `make risk` first")
    yrs = panel_years()
    latest = yrs[-1]
    d = load_year(latest)
    d["day"] = pd.to_datetime(d["day"])
    cutoff = d["day"].max() - pd.Timedelta(days=days_back)
    d = d[d["day"] >= cutoff].reset_index(drop=True)
    util.require(len(d) > 0, "no recent panel rows to score")
    out, nat = [], []
    for p in models:
        lead = int(p.stem.replace("lgbm_lead", ""))
        b = lgb.Booster(model_file=str(p))
        cols = b.feature_name()
        missing = [c for c in cols if c not in d.columns]
        util.require(not missing, f"panel is missing {missing[:5]} — retrain rather than guess")
        s = d.copy()
        s["p"] = np.clip(b.predict(s[cols]), 1e-6, 1 - 1e-6).astype("float32")
        s["lead"] = lead
        out.append(s[["cell", "day", "clat", "clon", "p", "lead", "n_fire"]])
        nat.append(s.groupby("day").agg(risk_mean=("p", "mean"), risk_max=("p", "max"),
                                        cells_hi=("p", lambda x: int((x > 0.2).sum())),
                                        fires=("n_fire", "sum")).reset_index().assign(lead=lead))
    new = pd.concat(out, ignore_index=True)
    if DAYS_OUT.exists():
        old = pd.read_parquet(DAYS_OUT)
        old["day"] = pd.to_datetime(old["day"])
        new = pd.concat([old[~old["day"].isin(set(new["day"]))], new], ignore_index=True)
    new.to_parquet(DAYS_OUT, index=False, compression="zstd")
    nn = pd.concat(nat, ignore_index=True)
    if NAT_OUT.exists():
        on = pd.read_parquet(NAT_OUT)
        on["day"] = pd.to_datetime(on["day"])
        nn = pd.concat([on[~on["day"].isin(set(nn["day"]))], nn], ignore_index=True)
    nn.to_parquet(NAT_OUT, index=False, compression="zstd")
    log(f"risk --predict-only: refreshed {new['day'].nunique()} days x {len(models)} leads "
        f"from the saved model (no refit)")


def main() -> None:
    import numpy as np
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--predict-only", action="store_true",
                    help="daily refresh: reuse the fitted model, score the newest days only")
    args = ap.parse_args()
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    util.require(PANEL_DIR.exists() and any(PANEL_DIR.glob("*.parquet")),
                 "no panel — run features first")
    if args.predict_only:
        predict_only()
        return

    yrs = panel_years()
    train_pool = [y for y in yrs if y not in config.ANCHOR_YEARS]
    # a year needs the full record either side of it to be a fair fold; the first and last are
    # short (the archive starts 2012-01-20 and the current year is incomplete)
    folds = [y for y in train_pool if y not in (yrs[0], yrs[-1])]
    # Three is the floor, not the target.  A blocked-by-season CV with two folds is not a
    # cross-validation, it is two experiments; with three the variance is still large and the
    # fold count is published beside every metric so nobody reads a small-sample AUC as settled.
    util.require(len(folds) >= 3,
                 f"only {len(folds)} usable folds from {len(yrs)} ERA5 years — need at least 3. "
                 f"The CDS queue is the constraint; rerun `make era5` then `make risk`.")
    if len(folds) < 5:
        log(f"  NOTE: only {len(folds)} folds available — every metric below carries more "
            f"sampling variance than the same metric on the full record would, and the fold "
            f"count is written into risk_meta.json so the page can say so.")
    log(f"risk: {len(yrs)} years, anchors {config.ANCHOR_YEARS} held out entirely, "
        f"{len(folds)} blocked-by-season folds")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    for stale in SCRATCH.glob("*.parquet"):
        stale.unlink()
    meta = {"leads": {}, "folds": folds, "n_folds": len(folds),
            "anchors": list(config.ANCHOR_YEARS), "era5_years": yrs,
            "neg_sample_rate": NEG_RATE, "n_rounds": N_ROUNDS,
            "fold_caveat": (f"{len(folds)} blocked-by-season folds — the ERA5 record is bounded "
                            f"by the CDS queue, not by the method, so these metrics carry more "
                            f"sampling variance than the same metrics on a full 15-year record")
            if len(folds) < 5 else None}
    final = {}
    for lead in config.LEAD_DAYS:
        clim_cache = {}
        for path in ("forecast", "reanalysis"):
            for test_year in folds:
                tr = [y for y in train_pool if y != test_year]
                calib = tr[-1] if tr[-1] != test_year else tr[-2]
                tr = [y for y in tr if y != calib]
                res, booster, cols, iso = fit_fold(tr, calib, test_year, lead, path)
                if path == "forecast":
                    key = (lead, test_year)
                    if key not in clim_cache:
                        clim_cache[key] = climatology(tr, lead)
                    c = clim_cache[key]
                    res["doy"] = pd.to_datetime(res["day"]).dt.dayofyear
                    res = res.merge(c, on=["cell", "doy"], how="left")
                    res["p_clim"] = res["p_clim"].fillna(res["y"].mean()).astype("float32")
                    res = res.drop(columns=["doy"])
                else:
                    res["p_clim"] = np.nan
                # one fold, one file.  Every (lead, path, fold) slice is ~715 k rows; holding all
                # of them — 11 folds x 3 leads x 2 paths — would be ~47 M rows in memory, which
                # this does not have.  Metrics are computed one (lead, path) slice at a time.
                res.to_parquet(SCRATCH / f"oof_{lead}_{path}_{test_year}.parquet",
                               index=False, compression="zstd")
                log(f"  lead {lead}d {path:11s} fold {test_year}: "
                    f"n={len(res):,} pos={int(res['y'].sum()):,} "
                    f"AUC={auc(res['p'], res['y']):.3f}")
                del res
                final[(lead, path)] = (booster, cols, iso)

    def oof_slice(lead: int, path: str):
        """One (lead, path) worth of out-of-fold predictions, with the shared climatology.

        The climatology baseline is path-independent, so the reanalysis path is scored against
        exactly the same reference the forecast path is — otherwise the two BSS numbers would be
        against two different denominators and the comparison between them would mean nothing.
        """
        parts = sorted(SCRATCH.glob(f"oof_{lead}_{path}_*.parquet"))
        if not parts:
            return pd.DataFrame()
        s = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
        if path != "forecast":
            fp = sorted(SCRATCH.glob(f"oof_{lead}_forecast_*.parquet"))
            if fp:
                cl = pd.concat([pd.read_parquet(p, columns=["cell", "day", "p_clim"])
                                for p in fp], ignore_index=True)
                s = s.drop(columns=["p_clim"]).merge(cl, on=["cell", "day"], how="left")
        return s

    for lead in config.LEAD_DAYS:
        meta["leads"][str(lead)] = {}
        for path in ("forecast", "reanalysis"):
            s = oof_slice(lead, path)
            if s.empty:
                meta["leads"][str(lead)][path] = {"status": "no folds"}
                continue
            m = {
                "n": int(len(s)), "positives": int(s["y"].sum()),
                "base_rate": float(s["y"].mean()),
                "auc": auc(s["p"], s["y"]),
                "brier": brier(s["p"], s["y"]),
                "brier_climatology": brier(s["p_clim"], s["y"]),
                "brier_persistence": brier(s["p_persist"], s["y"]),
                "bss_vs_climatology": bss(s["p"], s["y"], s["p_clim"]),
                "bss_vs_persistence": bss(s["p"], s["y"], s["p_persist"]),
                "auc_climatology": auc(s["p_clim"], s["y"]),
                "auc_persistence": auc(s["p_persist"], s["y"]),
                "persistence_note": ("persistence is the trailing 7-day fire count, isotonically "
                                     "calibrated to a probability on the same held-out season as "
                                     "the model — a bare 0/1 baseline would flatter us"),
                "reliability": reliability(s["p"], s["y"]),
            }
            meta["leads"][str(lead)][path] = m
            log(f"  lead {lead}d {path:11s}: AUC {m['auc']:.3f}  BSS(clim) "
                f"{m['bss_vs_climatology']:+.3f}  BSS(persist) {m['bss_vs_persistence']:+.3f}")
            if path == "forecast":
                # keep a light copy for the FWI comparison and for anyone who wants to re-score
                s.sample(n=min(400_000, len(s)), random_state=5).to_parquet(
                    SCRATCH / f"oofkeep_{lead}.parquet", index=False, compression="zstd")
            del s
        f, r = (meta["leads"][str(lead)]["forecast"], meta["leads"][str(lead)]["reanalysis"])
        if "auc" in f and "auc" in r and f["auc"] is not None and r["auc"] is not None:
            meta["leads"][str(lead)]["foresight_gap_auc"] = r["auc"] - f["auc"]

    # ── the FWI baseline — the external index gate G-J2 actually scores against ────────
    meta["fwi"] = score_against_fwi()
    keep = [pd.read_parquet(p) for p in sorted(SCRATCH.glob("oofkeep_*.parquet"))]
    if keep:
        pd.concat(keep, ignore_index=True).to_parquet(OOF_OUT, index=False, compression="zstd")
    for p in SCRATCH.glob("oof_*.parquet"):
        p.unlink()

    # ── final model on every non-anchor year, then the anchors scored blind ────────────
    log("risk: fitting the final model on all non-anchor years, then scoring the anchors blind")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    for stale in SCRATCH.glob("pred_*.parquet"):
        stale.unlink()
    anchors, nat_rows = [], []
    for lead in config.LEAD_DAYS:
        tr = [y for y in train_pool if y != train_pool[-1]]
        calib = train_pool[-1]
        probe = load_year(tr[0])
        cols = feature_cols(probe, "forecast")
        del probe
        import lightgbm as lgb
        Xtr, ytr, wtr = sample_train(tr, lead, cols)
        booster = lgb.train(LGB_PARAMS, lgb.Dataset(Xtr, label=ytr, weight=wtr),
                            num_boost_round=N_ROUNDS)
        del Xtr
        cal = load_year(calib)
        cal = cal[cal[f"y{lead}"].notna()]
        iso = isotonic_fit(booster.predict(cal[cols]), cal[f"y{lead}"].to_numpy())
        del cal
        booster.save_model(str(MODEL_DIR / f"lgbm_lead{lead}.txt"))
        if lead == config.LEAD_DAYS[0]:
            meta["shap_families"] = shap_by_family(booster, cols, load_year(train_pool[-1]))
            meta["importance"] = dict(sorted(
                zip(cols, booster.feature_importance("gain").tolist()),
                key=lambda kv: -kv[1])[:30])
        for y in yrs:
            d = load_year(y)
            d = d[d[f"y{lead}"].notna()].reset_index(drop=True)
            p = np.clip(iso.predict(booster.predict(d[cols])), 1e-6, 1 - 1e-6).astype("float32")
            d["p"] = p
            d["lead"] = lead
            if y in config.ANCHOR_YEARS:
                anchors.append(d[["cell", "day", "clat", "clon", "adm1_name",
                                  f"y{lead}", "p", "lead"]]
                               .rename(columns={f"y{lead}": "y"}))
            nat = (d.groupby("day")
                     .agg(risk_mean=("p", "mean"), risk_max=("p", "max"),
                          cells_hi=("p", lambda s: int((s > 0.2).sum())),
                          fires=("n_fire", "sum"))
                     .reset_index())
            nat["lead"] = lead
            nat_rows.append(nat)
            # ** DO NOT ACCUMULATE THE PREDICTIONS IN MEMORY. **  Fifteen years x three leads x
            # 715 k cell-days is 32 M rows; held as frames that is well over the 3 GB cap this
            # runs under.  Each (lead, year) goes straight to a scratch parquet and the export
            # selection — which needs the national series to exist first — reads them back in a
            # second pass and keeps only the chosen days.
            sp = SCRATCH / f"pred_{lead}_{y}.parquet"
            d[["cell", "day", "clat", "clon", "p", "lead", "n_fire"]].to_parquet(
                sp, index=False, compression="zstd")
            del d
    nat = pd.concat(nat_rows, ignore_index=True)
    nat.to_parquet(NAT_OUT, index=False, compression="zstd")

    # export days: the anchors' seasons, the worst days on record, and the recent tail
    sev = nat[nat["lead"] == config.LEAD_DAYS[0]].sort_values("fires", ascending=False)
    pick = set(pd.to_datetime(sev["day"].head(config.EXPORT_EPISODE_DAYS // 2)))
    pick |= set(pd.to_datetime(nat["day"]).sort_values().unique()[-60:])
    for a in config.ANCHOR_YEARS:
        s = nat[(nat["lead"] == config.LEAD_DAYS[0])
                & (pd.to_datetime(nat["day"]).dt.year == a)]
        pick |= set(pd.to_datetime(s.sort_values("fires", ascending=False)["day"].head(60)))
    keep = []
    for sp in sorted(SCRATCH.glob("pred_*.parquet")):
        part = pd.read_parquet(sp)
        part["day"] = pd.to_datetime(part["day"])
        part = part[part["day"].isin(pick)]
        if len(part):
            keep.append(part)
        sp.unlink()
    sel = pd.concat(keep, ignore_index=True) if keep else pd.DataFrame()
    sel.to_parquet(DAYS_OUT, index=False, compression="zstd")
    log(f"risk: exported {sel['day'].nunique():,} days x {sel['cell'].nunique():,} cells "
        f"x {sel['lead'].nunique()} leads -> {DAYS_OUT.name}")

    if anchors:
        ac = pd.concat(anchors, ignore_index=True)
        ac.to_parquet(config.DATA_DIR / "risk_anchors.parquet", index=False, compression="zstd")
        meta["anchor_scores"] = {
            str(a): {"auc": auc(g["p"], g["y"]), "n": int(len(g)),
                     "positives": int(g["y"].sum())}
            for a, g in ac[ac["lead"] == config.LEAD_DAYS[0]].groupby(
                pd.to_datetime(ac[ac["lead"] == config.LEAD_DAYS[0]]["day"]).dt.year)}
        log(f"  anchors scored blind: {meta['anchor_scores']}")

    META_OUT.write_text(json.dumps(util_nan_safe(meta), indent=1))
    util.manifest_put("risk", leads=list(config.LEAD_DAYS),
                      auc={str(L): meta["leads"][str(L)]["forecast"]["auc"]
                           for L in config.LEAD_DAYS})


def util_nan_safe(o):
    import math
    if isinstance(o, dict):
        return {k: util_nan_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [util_nan_safe(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


def score_against_fwi() -> dict:
    """G-J2's second half: beat the operational Canadian FWI, not just climatology.

    The FWI is given the fairest possible treatment — isotonically calibrated to probability on
    the same folds — because a raw index scored as a probability would lose to anything.
    Returns a PENDING record, with the exact URL to click, while EWDS is closed.
    """
    import numpy as np
    import pandas as pd
    p = config.DATA_DIR / "fwi.parquet"
    if not p.exists():
        return {"status": "PENDING",
                "reason": "cems-fire-historical-v1 is on EWDS and the account has not accepted "
                          "'Terms of use of the CEMS Early Warning Data Store (rev. 11)'.  The "
                          "token authenticates (HTTP 200 on the EWDS account endpoint) — this is "
                          "a one-time browser click, not a credential fault.",
                "url": config.POLICY_URLS["ewds"][0],
                "effect": "G-J2's climatology half is scored; its FWI half is PENDING."}
    # only the two columns and only the days the folds cover: the full CEMS table is 2.16 M rows
    # a year, and merging fifteen years of it against the out-of-fold sample is a gigabyte of
    # join for a correlation
    fwi = pd.read_parquet(p, columns=["cell", "day", "fwi"])
    fwi["day"] = pd.to_datetime(fwi["day"])
    out = {}
    for lead in config.LEAD_DAYS:
        keep = SCRATCH / f"oofkeep_{lead}.parquet"
        if not keep.exists():
            out[str(lead)] = {"status": "no out-of-fold predictions"}
            continue
        s = pd.read_parquet(keep)
        s["day"] = pd.to_datetime(s["day"])
        f = fwi[fwi["day"].isin(set(s["day"].unique()))]
        s = s.merge(f, on=["cell", "day"], how="left")
        s = s[s["fwi"].notna()]
        if s.empty:
            out[str(lead)] = {"status": "no overlap"}
            continue
        half = s["fold"].isin(sorted(s["fold"].unique())[: max(1, s["fold"].nunique() // 2)])
        iso = isotonic_fit(s.loc[half, "fwi"], s.loc[half, "y"])
        t = s[~half]
        p_fwi = np.clip(iso.predict(t["fwi"]), 1e-6, 1 - 1e-6)
        out[str(lead)] = {"status": "ok", "n": int(len(t)),
                          "auc_fwi": auc(t["fwi"], t["y"]),
                          "auc_model": auc(t["p"], t["y"]),
                          "brier_fwi": brier(p_fwi, t["y"]),
                          "bss_vs_fwi": bss(t["p"], t["y"], p_fwi)}
    return {"status": "ok", "per_lead": out}


def shap_by_family(booster, cols, sample) -> dict:
    """Mean |SHAP| aggregated to the feature families, so chapter 02 can say *why*."""
    import numpy as np
    import features as F
    s = sample[sample[f"y{config.LEAD_DAYS[0]}"].notna()].sample(
        n=min(40000, len(sample)), random_state=3)
    sv = booster.predict(s[cols], pred_contrib=True)[:, :-1]
    mean_abs = np.abs(sv).mean(axis=0)
    per_col = dict(zip(cols, mean_abs.tolist()))
    fam = {}
    for name, members in F.FAMILIES.items():
        v = [per_col[c] for c in members if c in per_col]
        if v:
            fam[name] = float(sum(v))
    tot = sum(fam.values()) or 1.0
    return {"absolute": fam, "share": {k: v / tot for k, v in fam.items()},
            "per_feature": dict(sorted(per_col.items(), key=lambda kv: -kv[1])[:25])}


if __name__ == "__main__":
    main()
