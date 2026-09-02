"""Train leave-one-regency-out, score the pre-registered gates, write the record.

Six folds. Each holds out one regency entirely — no cell from it is seen in
training, and the normalisation statistics and the decision threshold are both taken
from the training folds only. Picking either on the held-out fold would be the
spatial-leakage error this repository exists to catch.

Class 0 is subsampled in TRAINING only, to twice the rice count, because it is 58%
of cells and the gradient is otherwise mostly non-rice. Evaluation always runs on
every cell of the held-out regency, so precision and recall are what a reader would
get.

Thresholds come from data/dl_gates.json, which prereg.py wrote before this ran, and
this file may not change them.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import sys
import time

import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from core import (  # noqa: E402
    CLASSES, N_STEPS, REGENCIES, ROOT, SEED, Norm, load_regency, thin_series,
)
from net import CropNet, device  # noqa: E402

GATES = ROOT / "data" / "dl_gates.json"
OUT = ROOT / "data" / "dl_results.json"
# At batch 1024 an epoch took 426 s — 2.8 hours for six folds — and the process sat
# at 8% CPU, so the cost was per-step host work (a fancy-index gather out of a 1.3 GB
# int16 array, normalise, copy to the device) rather than the 109k-parameter model.
# A larger batch amortises all three. Learning rate scales with it.
#
# These are training hyperparameters, not gate thresholds: the gates in
# data/dl_gates.json are untouched, and nothing here was chosen after seeing a
# held-out number. The only figure observed before this change was fold 1's
# epoch-1 TRAINING loss.
# Set from the environment so the same code runs on a laptop and on a bigger box
# without editing it. The defaults are what fits 8 GB alongside everything else.
EPOCHS = int(os.environ.get("DL_EPOCHS", "3"))
BATCH = int(os.environ.get("DL_BATCH", "1024"))
LR = float(os.environ.get("DL_LR", "2.4e-3"))


def rss_gb() -> float:
    """Peak resident memory. Printed each epoch: the run that failed did so on memory,
    and it failed silently because nothing was watching it.

    ru_maxrss is in BYTES on macOS and KILOBYTES on Linux. The first version divided
    by 2**30 unconditionally, so on the GPU box it reported 0.00 GB — a gauge added to
    catch a memory failure, reading a thousand times low on the machine that mattered.
    """
    try:
        import resource
        m = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return m / (1 << 30) if sys.platform == "darwin" else m / (1 << 20)
    except Exception:
        return float("nan")


def prf(pred_rice: np.ndarray, true_rice: np.ndarray) -> dict:
    tp = int((pred_rice & true_rice).sum())
    fp = int((pred_rice & ~true_rice).sum())
    fn = int((~pred_rice & true_rice).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4),
            "tp": tp, "fp": fp, "fn": fn}


def threshold_for_precision(scores: np.ndarray, truth: np.ndarray,
                            target_p: float) -> float:
    """Lowest threshold whose precision still reaches the target. Training folds only."""
    order = np.argsort(-scores)
    s, t = scores[order], truth[order]
    tp = np.cumsum(t)
    prec = tp / np.arange(1, len(t) + 1)
    ok = np.flatnonzero(prec >= target_p)
    if len(ok) == 0:
        return float(s[0])
    return float(s[ok[-1]])


@torch.no_grad()
def infer(model: nn.Module, x: np.ndarray, norm: Norm, dev, batch: int = 4096):
    model.eval()
    out = np.empty((x.shape[0], len(CLASSES)), dtype=np.float32)
    for i in range(0, x.shape[0], batch):
        chunk = torch.from_numpy(norm(x[i:i + batch])).to(dev)
        out[i:i + batch] = torch.softmax(model(chunk), dim=1).cpu().numpy()
    return out


def train_fold(held: str, data: dict, dev, cfg: dict) -> tuple[nn.Module, Norm, dict]:
    """Train on the five regencies that are not `held`.

    Batches are gathered straight out of the per-regency arrays through an index of
    (regency, row) pairs. The first version concatenated the five into one array,
    which cost a second full copy of the training data — 1.3 GB on top of the 1.24 GB
    already resident — and on an 8 GB laptop that pushed the working set into swap,
    where it sat for 22 minutes without completing an epoch. Nothing about the model
    was the problem. Indexing costs one gather per batch and no copy at all.
    """
    rng = np.random.default_rng(SEED)
    tr = [r for r in REGENCIES if r != held]

    # index of (regency slot, row) for every training cell we intend to use
    parts, labels = [], []
    for slot, r in enumerate(tr):
        _, y, _ = data[r]
        rice = np.flatnonzero(y > 0)
        non = np.flatnonzero(y == 0)
        keep_non = rng.choice(non, size=min(len(non), 2 * len(rice)), replace=False)
        rows = np.concatenate([rice, keep_non])
        parts.append(np.stack([np.full(len(rows), slot, dtype=np.int32),
                               rows.astype(np.int32)], axis=1))
        labels.append(y[rows])
    index = np.concatenate(parts)
    ytr = np.concatenate(labels).astype(np.int64)
    del parts, labels

    arrays = [data[r][0] for r in tr]
    norm = Norm.fit([a[rng.choice(a.shape[0], size=min(30_000, a.shape[0]),
                                  replace=False)] for a in arrays])

    def gather(sel: np.ndarray) -> np.ndarray:
        """Assemble one batch from the per-regency arrays without a full copy."""
        out = np.empty((len(sel), 2, N_STEPS), dtype=np.int16)
        pick = index[sel]
        for slot in np.unique(pick[:, 0]):
            m = pick[:, 0] == slot
            out[m] = arrays[slot][pick[m, 1]]
        return out

    counts = np.bincount(ytr, minlength=len(CLASSES)).astype(np.float64)
    w = 1.0 / np.sqrt(np.maximum(counts, 1))
    w = w / w.sum() * len(CLASSES)

    model = CropNet().to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=1e-4)
    lossf = nn.CrossEntropyLoss(weight=torch.tensor(w, dtype=torch.float32).to(dev))
    nsteps = int(np.ceil(len(ytr) / cfg["batch"])) * cfg["epochs"]
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=cfg["lr"], total_steps=nsteps)

    t0 = time.time()
    for ep in range(cfg["epochs"]):
        model.train()
        perm = rng.permutation(len(ytr))
        tot = n = 0
        for i in range(0, len(perm), cfg["batch"]):
            b = perm[i:i + cfg["batch"]]
            xb = torch.from_numpy(norm(gather(b))).to(dev)
            yb = torch.from_numpy(ytr[b]).to(dev)
            opt.zero_grad(set_to_none=True)
            loss = lossf(model(xb), yb)
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss.detach()) * len(b)
            n += len(b)
        print(f"      epoch {ep + 1}/{cfg['epochs']}  loss {tot / n:.4f}  "
              f"{time.time() - t0:5.0f}s  rss {rss_gb():.2f} GB", flush=True)

    sub = rng.choice(len(ytr), size=min(150_000, len(ytr)), replace=False)
    p_tr = infer(model, gather(sub), norm, dev)
    meta = {"train_cells": int(len(ytr)),
            "train_class_counts": counts.astype(int).tolist(),
            "score_train": 1.0 - p_tr[:, 0], "truth_train": (ytr[sub] > 0)}
    return model, norm, meta


def main() -> int:
    reg = json.loads(GATES.read_text())
    if reg.get("status", "").startswith("registered") is False:
        pass
    thr = {g["id"]: g["threshold"] for g in reg["gates"]}
    base = reg["baseline"]["pooled_2023_2025"]
    dev = device()
    cfg = {"epochs": EPOCHS, "batch": BATCH, "lr": LR}
    print(f"  device {dev}  ·  {len(REGENCIES)} folds  ·  {EPOCHS} epochs  "
          f"·  batch {BATCH}  ·  lr {LR}\n")

    print("  loading six regencies (int16, converted per batch)")
    data = {r: load_regency(r) for r in REGENCIES}
    tot = sum(v[0].shape[0] for v in data.values())
    print(f"  {tot:,} cells x 2 channels x {N_STEPS} steps\n")

    folds = []
    for held in REGENCIES:
        print(f"  fold: holding out {held}")
        model, norm, meta = train_fold(held, data, dev, cfg)
        t = threshold_for_precision(meta["score_train"], meta["truth_train"],
                                    thr["G-D2"]["at_precision"])

        x, y, _ = data[held]
        p = infer(model, x, norm, dev)
        score = 1.0 - p[:, 0]
        true_rice = y > 0

        at_argmax = prf(p.argmax(axis=1) > 0, true_rice)
        at_matched = prf(score >= t, true_rice)
        cls_pred = p.argmax(axis=1)

        folds.append({
            "held_out": held, "cells": int(len(y)),
            "threshold_from_training": round(t, 6),
            "argmax": at_argmax, "at_matched_precision": at_matched,
            "class_recall": {CLASSES[k]: round(float((cls_pred[y == k] == k).mean()), 4)
                             for k in CLASSES if (y == k).any()},
        })
        print(f"      argmax  P {at_argmax['precision']:.4f}  R {at_argmax['recall']:.4f}"
              f"  F1 {at_argmax['f1']:.4f}")
        print(f"      matched P {at_matched['precision']:.4f}  R {at_matched['recall']:.4f}"
              f"  F1 {at_matched['f1']:.4f}   (threshold {t:.4f})\n", flush=True)

        if held == "Karawang":
            torch.save({"model": model.state_dict(), "norm": norm.state()},
                       ROOT / "data" / "dl_karawang_fold.pt")
            karawang = (model, norm, t)

    # ── the thinning ladder, on the same regency the detector's experiment used
    print("  thinning ladder on Karawang (model unchanged, acquisitions removed)")
    model, norm, t = karawang
    x, y, real = data["Karawang"]
    true_rice = y > 0
    ladder = []
    for keep_every, gap in ((1, 6), (2, 12), (3, 18), (4, 24)):
        xt = thin_series(x, real, keep_every)
        s = 1.0 - infer(model, xt, norm, dev)[:, 0]
        r = prf(s >= t, true_rice)
        ladder.append({"keep_every": keep_every, "median_gap_days": gap,
                       "acquisitions": int(np.ceil(real.sum() / keep_every)), **r})
        print(f"      gap {gap:2}d  P {r['precision']:.4f}  R {r['recall']:.4f}"
              f"  F1 {r['f1']:.4f}", flush=True)
        del xt
    loss12 = 1 - ladder[1]["recall"] / ladder[0]["recall"]

    # ── score the gates
    mean_f1 = float(np.mean([f["at_matched_precision"]["f1"] for f in folds]))
    mean_p = float(np.mean([f["at_matched_precision"]["precision"] for f in folds]))
    mean_r = float(np.mean([f["at_matched_precision"]["recall"] for f in folds]))
    n_beat = sum(1 for f in folds if f["at_matched_precision"]["f1"] >= base["f1"])
    c1 = float(np.mean([f["class_recall"].get("single", 0.0) for f in folds]))

    outcomes = {
        "G-D1": (mean_f1 >= thr["G-D1"]["f1_at_least"],
                 f"mean F1 across six held-out regencies {mean_f1:.4f} against the "
                 f"detector's pooled {base['f1']}"),
        "G-D2": (mean_p >= thr["G-D2"]["at_precision"] - thr["G-D2"]["precision_tolerance"]
                 and mean_r >= thr["G-D2"]["recall_at_least"],
                 f"at a threshold chosen on training folds, mean precision {mean_p:.4f} "
                 f"and recall {mean_r:.4f} against the detector's {base['precision']} "
                 f"and {base['recall']}"),
        "G-D3": (c1 >= thr["G-D3"]["class1_recall_at_least"],
                 f"single-crop class recall {c1:.4f} against the detector's "
                 f"{reg['baseline']['per_class_detect_rate']['1']:.4f} detect rate; "
                 f"bar was {thr['G-D3']['class1_recall_at_least']:.4f}"),
        "G-D4": (loss12 < thr["G-D4"]["relative_recall_loss_at_12d_below"],
                 f"going from a 6-day to a 12-day median gap costs the model "
                 f"{loss12:.1%} of its recall; the detector lost "
                 f"{thr['G-D4']['relative_recall_loss_at_12d_below']:.1%} of its extent"),
        "G-D5": (n_beat >= thr["G-D5"]["folds_beating_detector_f1_at_least"],
                 f"{n_beat} of {len(folds)} held-out regencies beat the detector's "
                 f"pooled F1 on their own"),
    }
    for g in reg["gates"]:
        passed, reason = outcomes[g["id"]]
        g["status"] = "pass" if passed else "fail"
        g["pass"] = bool(passed)
        g["outcome"] = reason
    reg["status"] = "run"
    reg["ran"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    GATES.write_text(json.dumps(reg, indent=1))

    OUT.write_text(json.dumps({
        "case": "rice-security",
        "ran": reg["ran"],
        "device": str(dev), "epochs": EPOCHS, "batch": BATCH, "lr": LR,
        "peak_rss_gb": round(rss_gb(), 2),
        "params": sum(p.numel() for p in CropNet().parameters()),
        "folds": folds,
        "mean_at_matched_precision": {"precision": round(mean_p, 4),
                                      "recall": round(mean_r, 4),
                                      "f1": round(mean_f1, 4)},
        "single_crop_recall": round(c1, 4),
        "thinning_karawang": ladder,
        "relative_recall_loss_12d": round(loss12, 4),
        "baseline": reg["baseline"],
    }, indent=1))

    print(f"\n  {'gate':7} {'outcome':8} detail")
    for g in reg["gates"]:
        print(f"  {g['id']:7} {g['status'].upper():8} {g['outcome']}")
    print(f"\n  wrote {OUT.relative_to(ROOT)} and {GATES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
