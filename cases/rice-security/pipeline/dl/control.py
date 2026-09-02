"""The control the deep model needs: gradient boosting on hand-made features.

WHY THIS EXISTS
---------------
run.py reports mean F1 0.8386 against the unsupervised detector's 0.6321, and binary
recall 0.8836 against 0.5118. Both are real numbers. Neither shows that the
convolutions did the work, because the deep model has an advantage the detector never
had: it was TRAINED on the map it is scored against.

Separating "supervision helped" from "the architecture helped" needs a model with the
same labels, the same folds, the same threshold protocol and the same input — and no
learned representation. If gradient boosting on thirty hand-written features reaches
the same F1, the CNN contributed nothing and the honest write-up says so.

THE FEATURES ARE CHOSEN TO BE STRONG, NOT WEAK
----------------------------------------------
A control that is deliberately feeble proves nothing. The most informative feature set
here is not generic summary statistics but the Fourier amplitudes at the cropping
frequencies: over a 1,512-day record, one crop a year is a cycle of about 60.8 six-day
steps, two a year is 30.4, three is 20.3. Cropping intensity IS a frequency-domain
question, so a Fourier feature set is the strongest simple rival available and the
fairest thing to put against a convolution — which, with dilations 1 to 16, is
approximating the same basis by other means.

Same six leave-one-regency-out folds, same seed, same decision-threshold-from-training
rule. Nothing here sees the held-out regency.
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from core import CLASSES, REGENCIES, ROOT, SEED, load_regency, to_db  # noqa: E402

OUT = ROOT / "data" / "dl_control.json"
GATES = ROOT / "data" / "dl_gates.json"

STEP_DAYS = 6
# cycles per record for 1, 2 and 3 crops a year over 252 six-day steps
YEARS = 252 * STEP_DAYS / 365.25
CROP_HARMONICS = [1, 2, 3]


def features(x: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """~30 features per cell from the same (2, 252) series the network reads."""
    d = to_db(x)                                    # [N, 2, 252] float32 dB
    cols, names = [], []

    for ci, cn in enumerate((("vv"), ("vh"))):
        s = d[:, ci, :]
        q = np.percentile(s, [5, 25, 50, 75, 95], axis=1)
        cols += [s.mean(1), s.std(1), s.min(1), s.max(1),
                 q[4] - q[0], q[3] - q[1], q[2]]
        names += [f"{cn}_{n}" for n in
                  ("mean", "std", "min", "max", "p95_p05", "iqr", "median")]

        # Fourier power at the cropping frequencies — the physically right basis
        cen = s - s.mean(1, keepdims=True)
        sp = np.abs(np.fft.rfft(cen, axis=1))
        for h in CROP_HARMONICS:
            k = int(round(h * YEARS))               # bin for h cycles per year
            band = sp[:, max(k - 1, 1):k + 2].max(1)
            cols.append(band)
            names.append(f"{cn}_fft_{h}pery")
        tot = sp[:, 1:].sum(1) + 1e-6
        for h in CROP_HARMONICS:
            k = int(round(h * YEARS))
            cols.append(sp[:, max(k - 1, 1):k + 2].max(1) / tot)
            names.append(f"{cn}_fftshare_{h}pery")
        cols.append(sp[:, 1:].argmax(1).astype(np.float32))
        names.append(f"{cn}_dominant_bin")

        # how often the series dips hard — flooding leaves a specular trough
        thr = (q[2] - 2.0)[:, None]
        below = s < thr
        cols.append(below.sum(1).astype(np.float32))
        names.append(f"{cn}_steps_below_med_minus2db")
        crossings = np.diff(below.astype(np.int8), axis=1) == 1
        cols.append(crossings.sum(1).astype(np.float32))
        names.append(f"{cn}_trough_entries")

        # first differences: how fast it moves
        df = np.diff(s, axis=1)
        cols += [np.abs(df).mean(1), df.std(1)]
        names += [f"{cn}_absdiff_mean", f"{cn}_diff_std"]

    cols.append(d[:, 0, :].mean(1) - d[:, 1, :].mean(1))
    names.append("vv_minus_vh_mean")
    return np.stack(cols, axis=1).astype(np.float32), names


def prf(pred: np.ndarray, truth: np.ndarray) -> dict:
    tp = int((pred & truth).sum()); fp = int((pred & ~truth).sum())
    fn = int((~pred & truth).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}


def threshold_for_precision(scores, truth, target_p):
    o = np.argsort(-scores)
    t = truth[o]
    prec = np.cumsum(t) / np.arange(1, len(t) + 1)
    ok = np.flatnonzero(prec >= target_p)
    return float(scores[o][ok[-1]]) if len(ok) else float(scores[o][0])


def main() -> int:
    # HistGradientBoosting rather than LightGBM: the mac wheels need libomp, which is
    # not installable without touching the host. Same model class, and the air-quality
    # case in this repository already uses this estimator. The two folds that did run
    # under LightGBM on the GPU box scored 0.7646 and 0.7549, so the substitution can
    # be checked rather than assumed.
    from sklearn.ensemble import HistGradientBoostingClassifier as HGB

    reg = json.loads(GATES.read_text())
    target_p = reg["gates"][1]["threshold"]["at_precision"]
    base = reg["baseline"]["pooled_2023_2025"]

    t0 = time.time()
    feats, labels, names = {}, {}, None
    for r in REGENCIES:
        x, y, _ = load_regency(r)
        f, names = features(x)
        feats[r], labels[r] = f, y
        print(f"  {r:12} {f.shape[0]:7,} cells x {f.shape[1]} features  "
              f"{time.time() - t0:5.0f}s", flush=True)
        del x

    rng = np.random.default_rng(SEED)
    folds = []
    for held in REGENCIES:
        tr = [r for r in REGENCIES if r != held]
        Xtr = np.concatenate([feats[r] for r in tr])
        ytr = np.concatenate([labels[r] for r in tr]).astype(np.int64)
        rice = np.flatnonzero(ytr > 0); non = np.flatnonzero(ytr == 0)
        keep = np.concatenate([rice, rng.choice(
            non, size=min(len(non), 2 * len(rice)), replace=False)])
        Xtr, ytr = Xtr[keep], ytr[keep]

        # binary, matching what the deep model actually delivered
        common = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=63,
                      random_state=SEED, early_stopping=False)
        b = HGB(**common).fit(Xtr, (ytr > 0).astype(int))
        # four-class, matching what it was trained for
        m = HGB(**common).fit(Xtr, ytr)

        sub = rng.choice(len(ytr), size=min(200_000, len(ytr)), replace=False)
        thr = threshold_for_precision(b.predict_proba(Xtr[sub])[:, 1],
                                      (ytr[sub] > 0), target_p)

        Xte, yte = feats[held], labels[held]
        s = b.predict_proba(Xte)[:, 1]
        cls = m.predict(Xte)
        row = {"held_out": held, "threshold_from_training": round(thr, 6),
               "binary": prf(s >= thr, yte > 0),
               "class_recall": {CLASSES[k]: round(float((cls[yte == k] == k).mean()), 4)
                                for k in CLASSES if (yte == k).any()},
               }
        folds.append(row)
        print(f"  {held:12} P {row['binary']['precision']:.4f}  "
              f"R {row['binary']['recall']:.4f}  F1 {row['binary']['f1']:.4f}   "
              f"single={row['class_recall'].get('single', float('nan')):.3f}", flush=True)
        del Xtr, ytr

    mp = float(np.mean([f["binary"]["precision"] for f in folds]))
    mr = float(np.mean([f["binary"]["recall"] for f in folds]))
    mf = float(np.mean([f["binary"]["f1"] for f in folds]))
    ms = float(np.mean([f["class_recall"].get("single", 0.0) for f in folds]))

    ranked = []          # HGB has no gain importance; permutation importance is a
                         # separate, slower job and not what this control is for

    OUT.write_text(json.dumps({
        "case": "rice-security", "artefact": "hand-feature control for the deep model",
        "ran": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "why": ("Same labels, same folds, same threshold rule, same input series, no "
                "learned representation. Its purpose is to find out whether the "
                "convolutions or the supervision produced the deep model's margin."),
        "n_features": len(names), "features": names,
        "folds": folds,
        "mean": {"precision": round(mp, 4), "recall": round(mr, 4), "f1": round(mf, 4),
                 "single_crop_recall": round(ms, 4)},
        "baseline_detector": base,
        "estimator": "sklearn HistGradientBoostingClassifier, 300 iters, 63 leaves",
    }, indent=1))

    print(f"\n  {'':22} {'P':>7} {'R':>7} {'F1':>7} {'single':>8}")
    print(f"  {'hand features + LGBM':22} {mp:7.4f} {mr:7.4f} {mf:7.4f} {ms:8.4f}")
    print(f"  {'deep CNN (run.py)':22} {0.8054:7.4f} {0.8836:7.4f} {0.8386:7.4f} {0.1768:8.4f}")
    print(f"  {'unsupervised detector':22} {base['precision']:7.4f} "
          f"{base['recall']:7.4f} {base['f1']:7.4f} {0.0356:8.4f}")
    print(f"\n  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
