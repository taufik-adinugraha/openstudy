"""Pre-register the deep-learning gates, before the model is trained.

House rule: thresholds are fixed before the runs, and every gate publishes its
outcome either way. This file is run first and writes data/dl_gates.json with the
thresholds and with nothing filled in. run.py then fills the outcomes and is not
permitted to change a threshold.

WHAT IS BEING ASKED
-------------------
The case already ships an unsupervised detector: thresholds on Sentinel-1
backscatter, scored against an independent published rice map (Open-SEA-Rice-10,
2021, CC BY 4.0). Pooled over 2023-2025 it reaches precision 0.826, recall 0.512.
Its recall is limited, and the case's own thinning experiment showed the binding
constraint is revisit frequency rather than cell size: at a 12-day median gap the
detector keeps 20% of the extent it recovers at 6 days.

So: does a temporal model that sees the whole backscatter curve do better, and does
it degrade less when the looks are taken away?

THE ADVANTAGE THIS MODEL HAS, STATED UP FRONT
---------------------------------------------
It is not a fair fight and pretending otherwise would make the result worthless.

  1. SUPERVISION. The detector was never shown the map. This model is TRAINED on the
     map and then scored against the map. Whatever it learns includes "what this
     particular product calls rice", including that product's own errors. Holding out
     whole regencies is what makes the comparison admissible; it does not remove the
     advantage.
  2. HORIZON. The detector works a year at a time. This model reads all 252 six-day
     steps from 2022-07 to 2026-08 at once, so it can use inter-annual structure the
     detector never sees. The comparator is therefore the detector's POOLED figure —
     the union of its detections across three years — not its per-year figure.
  3. LABEL VINTAGE. The map is 2021; the radar record opens 2022-07. Cropping
     intensity is not fixed, so some disagreement is the world changing rather than
     either method being wrong. This bounds how high any score here can legitimately
     go, for both methods equally.

A win on G-D1 alone therefore demonstrates very little. G-D2 and G-D4 are the gates
that carry information: whether the gain survives at the detector's own precision,
and whether the model is less revisit-limited. G-D4 is the one worth running.
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEST = ROOT / "data" / "dl_gates.json"

# the detector's own scores, read from the audit the pipeline already published
AUDIT = json.loads((ROOT / "data" / "audit.json").read_text())
POOLED = next(r for r in AUDIT["I_prf"]["rows"] if r["year"] == "pooled")
PER_CLASS = AUDIT["B_by_crop_class"]["summary"]
LADDER = AUDIT["E_thinning"]["ladder"]


def gates() -> list[dict]:
    thin6 = next(r for r in LADDER if r["median_gap_days"] == 6)
    thin12 = next(r for r in LADDER if r["median_gap_days"] == 12)
    det_thin_loss = 1 - thin12["extent_recall_vs_prior"] / thin6["extent_recall_vs_prior"]
    return [
        {
            "id": "G-D1", "hard": True,
            "name": "Binary rice detection F1 on held-out regencies beats the detector",
            "threshold": {"f1_at_least": POOLED["f1"]},
            "why": ("The weakest of these gates, and it is listed first so it cannot be "
                    "mistaken for the finding. A supervised model trained on the "
                    "benchmark ought to beat an unsupervised detector that never saw "
                    "it; failing this would mean something is wrong with the setup."),
        },
        {
            "id": "G-D2", "hard": True,
            "name": "At the detector's own precision, recall is higher",
            "threshold": {"at_precision": POOLED["precision"],
                          "recall_at_least": POOLED["recall"],
                          "precision_tolerance": 0.02},
            "tolerance_reason": ("The threshold is picked on the training folds and "
                                 "applied to a regency the model has never seen, so "
                                 "the precision it lands on there will not be exactly "
                                 "the precision it was tuned for. Two points of slack "
                                 "is registered here, before training, because "
                                 "deciding it afterwards would be choosing the bar to "
                                 "fit the result."),
            "why": ("Precision is the statistic a detector can always buy by finding "
                    "less, so the comparison has to be made at a matched operating "
                    "point. The decision threshold is chosen on the TRAINING folds to "
                    "hit this precision, then applied unchanged to the held-out "
                    "regency — picking it on the test fold would be the same error the "
                    "case criticises elsewhere."),
        },
        {
            "id": "G-D3", "hard": False,
            "name": "Single-crop cells, which the detector almost entirely misses",
            "threshold": {"class1_recall_at_least": PER_CLASS["1"]["detect_rate"] * 2},
            "why": (f"The detector finds {PER_CLASS['1']['detect_rate']:.1%} of "
                    f"single-crop cells against {PER_CLASS['2']['detect_rate']:.1%} of "
                    "double-crop. One flooding-and-drydown cycle in four years is a "
                    "thin signal for a threshold rule and should be an easier target "
                    "for a model that sees curve shape. Doubling it is the bar; soft, "
                    "because the map's own single-crop class is its smallest and "
                    "probably its noisiest."),
        },
        {
            "id": "G-D4", "hard": True,
            "name": "The model is less revisit-limited than the detector",
            "threshold": {"relative_recall_loss_at_12d_below": round(det_thin_loss, 4)},
            "why": (f"The detector loses {det_thin_loss:.1%} of the extent it recovers "
                    "when the median gap goes from 6 to 12 days. Re-run identically: "
                    "same regency, same cells, drop the same acquisitions, re-fill the "
                    "grid from what survives, re-run inference with the model "
                    "unchanged. If the model loses less, the deficit was partly "
                    "recoverable from curve shape and the published diagnosis is "
                    "incomplete. If it loses as much, the revisit limit is physical "
                    "and now confirmed by an independent method class. THIS IS THE "
                    "GATE WORTH RUNNING — it is informative whichever way it falls."),
        },
        {
            "id": "G-D5", "hard": True,
            "name": "The gain is not one regency",
            "threshold": {"folds_beating_detector_f1_at_least": 4},
            "why": ("Six leave-one-regency-out folds. Four of six must beat the "
                    "detector's pooled F1 on their own held-out regency. A mean that "
                    "rests on one fold is the spatial-leakage failure this repository "
                    "exists to catch, arriving by a different route."),
        },
    ]


def main() -> None:
    doc = {
        "case": "rice-security",
        "artefact": "deep-learning cropping-intensity model",
        "registered": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "registered — not yet run",
        "task": {
            "input": "252 six-day steps x 2 polarisations (VV, VH) per 100 m cell, "
                     "Savitzky-Golay smoothed, 2022-07-07 to 2026-08-25",
            "target": "Open-SEA-Rice-10 class: 0 non-rice, 1 single, 2 double, 3 triple",
            "split": "leave-one-regency-out, 6 folds",
            "regencies": ["Bojonegoro", "Grobogan", "Indramayu", "Karawang",
                          "Lamongan", "Subang"],
        },
        "baseline": {
            "what": "the case's existing unsupervised backscatter detector",
            "pooled_2023_2025": {k: POOLED[k] for k in
                                 ("precision", "recall", "f1", "detected_cells")},
            "per_class_detect_rate": {k: PER_CLASS[k]["detect_rate"]
                                      for k in ("1", "2", "3")},
        },
        "declared_advantages": [
            "trained on the benchmark it is scored against; the detector was not",
            "reads the whole 4-year record at once; the detector works one year at a time",
            "the map is 2021 and the radar opens 2022-07, which bounds both methods",
        ],
        "gates": gates(),
    }
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps(doc, indent=1))
    print(f"  registered {len(doc['gates'])} gates, before training")
    for g in doc["gates"]:
        print(f"    {g['id']}  {'hard' if g['hard'] else 'soft'}  {g['name'][:62]}")
        print(f"          {json.dumps(g['threshold'])}")
    print(f"\n  wrote {DEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
