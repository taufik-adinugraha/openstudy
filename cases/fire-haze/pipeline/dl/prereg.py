"""Pre-register the spatial deep-learning gates for fire-haze, before any model.

Written before a single weight exists. run.py fills the outcomes and may not change a
threshold.

WHAT IS BEING ASKED
-------------------
The case already ships a LightGBM model over per-cell tabular features — dryness,
atmosphere, soil, fuel — that scores AUC 0.870 one day ahead and 0.814 at seven days
under 2-degree spatial blocking, against the operational Fire Weather Index's 0.791.

Fire spreads and smoke advects, so a per-cell model is structurally blind to a
neighbour's dryness and to the upwind state. The features sit on a complete, regular
0.25-degree ERA5 raster — 61 rows by 97 columns, 5,917 cells of which 1,955 are land —
so a convolution can see the neighbourhood at no cost in new data. The question is
whether that helps, and by how much.

WHAT THIS CASE HAS THAT THE RICE CASE DID NOT
---------------------------------------------
Rice's deep model was compared against an unsupervised detector that had never seen
the labels, so most of its apparent margin turned out to be supervision rather than
architecture — 37 hand-written features recovered 96% of it. That control had to be
built afterwards.

Here the control already exists and is the baseline. LightGBM trains on the same
labels, the same folds and the same features; the only thing the deep model adds is
the spatial neighbourhood. There is no supervision asymmetry to argue about, so a
margin here means something a margin there did not.

And the ablation is registered up front rather than discovered later: G-F5 runs the
identical architecture with the neighbourhood removed. If a 1x1-kernel version scores
the same, the gain came from being a neural network, not from seeing space.

THE PREDICTION THAT MAKES THIS FALSIFIABLE
------------------------------------------
If spatial context is what is being added, its value must GROW with forecast lead:
tomorrow's fire is mostly about this cell's own dryness, next week's depends on
synoptic conditions moving across the map. So the gain at seven days must exceed the
gain at one day. A model that improves equally at every lead is a better function
approximator, not a spatial one, and G-F2 is written to catch exactly that.

DECLARED LIMITS
---------------
  1. ERA5 is a 0.25-degree reanalysis and already spatially smooth, so neighbouring
     cells are correlated and some spatial information is present in the per-cell
     features already. This bounds how much a convolution can add, and it bounds it
     for the honest reason rather than a modelling failure.
  2. Only three training folds, 2016-2018. The case's own note says the ERA5 record is
     "bounded by the CDS queue, not by the method", so this constrains both models
     equally.
  3. 1,955 of 5,917 cells are land. Sea cells carry no target and are masked in the
     loss; they still enter the convolution's field of view, which is correct — smoke
     crosses water.
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEST = ROOT / "data" / "dl_gates.json"

RISK = json.loads((ROOT / "data" / "risk_meta.json").read_text())
REVIEW = json.loads((ROOT / "data" / "review.json").read_text())

# The spatially blocked figures are the comparator, not the higher forecast-path ones:
# the deep model will be blocked the same way, and comparing a blocked model against an
# unblocked baseline would flatter it for free.
BLOCKED = {int(k): v["auc"] for k, v in REVIEW["spatial"]["per_lead"].items()}
FWI = RISK["fwi"]["per_lead"]["1"]
ANCHORS = {int(y): s["auc"] for y, s in RISK["anchor_scores"].items()}
# feature families as the panel itself defines them; shap_families.per_feature is a
# feature->value map, not a family->list map, and using it here cost a traceback
PANEL = json.loads((ROOT / "data" / "panel_meta.json").read_text())
FAMILIES = {k: len(v) for k, v in PANEL.get("families", {}).items()}

GRID = {"rows": 61, "cols": 97, "cells": 5917, "land_cells": 1955, "deg": 0.25,
        "lat": [-9.0, 6.0], "lon": [95.0, 119.0]}


def gates() -> list[dict]:
    lead1, lead7 = BLOCKED[1], BLOCKED[7]
    return [
        {
            "id": "G-F1", "hard": True,
            "name": "Beats the tabular model at one day, spatially blocked",
            "threshold": {"auc_at_lead1_above": round(lead1, 6)},
            "why": ("The floor. Same labels, same folds, same features, plus the "
                    "neighbourhood. Failing this means the architecture is losing "
                    "information the tabular model keeps, which would be a finding "
                    "about the model rather than about fire."),
        },
        {
            "id": "G-F2", "hard": True,
            "name": "The gain grows with forecast lead",
            "threshold": {"auc_gain_at_lead7_exceeds_gain_at_lead1": True,
                          "baseline_lead1": round(lead1, 6),
                          "baseline_lead7": round(lead7, 6)},
            "why": ("THE GATE THAT CARRIES THE ARGUMENT. Tomorrow's fire is mostly "
                    "this cell's own dryness; next week's depends on conditions "
                    "moving across the map. So if the convolution is buying spatial "
                    "context, the gain must be larger at seven days than at one. "
                    "Equal gains at every lead would mean a better function "
                    "approximator, not a spatial model, and this gate fails in that "
                    "case even if G-F1 passes handsomely."),
        },
        {
            "id": "G-F3", "hard": False,
            "name": "Still beats the operational index it is meant to replace",
            "threshold": {"auc_above": round(FWI["auc_fwi"], 6),
                          "on_rows": FWI["n"]},
            "why": (f"The Fire Weather Index scores {FWI['auc_fwi']:.3f} on the "
                    f"{FWI['n']:,} rows where it is defined. Soft, because the "
                    "existing tabular model already clears it at "
                    f"{FWI['auc_model']:.3f} — listed so the comparison a practitioner "
                    "actually cares about is on the record either way."),
        },
        {
            "id": "G-F4", "hard": True,
            "name": "Holds up on the two catastrophic haze years, held out entirely",
            "threshold": {"anchor_auc_at_least": {str(y): round(a - 0.02, 6)
                                                  for y, a in ANCHORS.items()}},
            "why": ("2015 and 2019 are the seasons the instrument exists for, and the "
                    "tabular model scores "
                    + " and ".join(f"{a:.3f}" for a in ANCHORS.values())
                    + " on them with neither year in training. Two points of slack, "
                    "registered here, because anchor years are single seasons and "
                    "noisier than a pooled fold. A model that only works in ordinary "
                    "years is useless for the purpose."),
        },
        {
            "id": "G-F5", "hard": True,
            "name": "The neighbourhood is what helps — 1x1 ablation must be worse",
            "threshold": {"full_model_auc_exceeds_1x1_ablation_by": 0.005},
            "why": ("The control the rice case had to add afterwards, registered "
                    "before the fact. Same architecture, same depth, same parameter "
                    "budget, kernels reduced to 1x1 so the model sees only its own "
                    "cell. If the ablation scores within 0.005 AUC of the full model, "
                    "the spatial field of view contributed nothing and the write-up "
                    "says the gain was from being a neural network."),
        },
        {
            "id": "G-F6", "hard": True,
            "name": "The gain is not one region",
            "threshold": {"spatial_folds_improving_at_least": 3,
                          "of_folds": REVIEW["spatial"]["n_spatial_folds"]},
            "why": ("Four 2-degree spatial folds over 73 blocks. Three of four must "
                    "improve on the tabular model within their own held-out blocks. "
                    "Sumatra and Kalimantan burn differently; a mean that rests on one "
                    "of them is not a result."),
        },
    ]


def main() -> None:
    doc = {
        "case": "fire-haze",
        "artefact": "spatial deep-learning fire-risk model",
        "registered": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "registered — not yet run",
        "task": {
            "input": ("a (days, 61, 97, C) raster: the case's own per-cell features on "
                      "the native 0.25 deg ERA5 grid, no regridding"),
            "grid": GRID,
            "target": "fire in the cell at lead 1, 3 and 7 days",
            "families": FAMILIES,
            "split": ("2 deg spatial blocks, 4 folds over 73 blocks, plus "
                      "season-held-out folds — the design the tabular model already "
                      "uses, reused unchanged"),
            "anchors": sorted(ANCHORS),
        },
        "baseline": {
            "what": "the case's existing LightGBM over per-cell tabular features",
            "spatially_blocked_auc": {str(k): round(v, 6) for k, v in BLOCKED.items()},
            "forecast_path_auc": {k: round(v["forecast"]["auc"], 6)
                                  for k, v in RISK["leads"].items()},
            "vs_fwi_at_lead1": {"model": round(FWI["auc_model"], 6),
                                "fwi": round(FWI["auc_fwi"], 6), "rows": FWI["n"]},
            "anchor_auc": {str(y): round(a, 6) for y, a in ANCHORS.items()},
            "base_rate": RISK["leads"]["1"]["forecast"]["base_rate"],
            "rounds": RISK["n_rounds"], "neg_sample_rate": RISK["neg_sample_rate"],
        },
        "why_this_comparison_is_fair": [
            "the baseline trains on the same labels, folds and features",
            "the only addition is the spatial neighbourhood, which is the treatment",
            "the blocked baseline is the comparator, not the higher forecast-path one",
        ],
        "declared_limits": [
            "ERA5 at 0.25 deg is already spatially smooth, so neighbours are "
            "correlated and some spatial signal is in the per-cell features already",
            "three training folds only; the case's own note attributes this to the CDS "
            "queue rather than to the method, and it binds both models equally",
            "sea cells carry no target and are masked in the loss, but remain visible "
            "to the convolution because smoke crosses water",
        ],
        "gates": gates(),
    }
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps(doc, indent=1))
    print(f"  registered {len(doc['gates'])} gates for fire-haze, before any model\n")
    print(f"  grid {GRID['rows']}x{GRID['cols']} at {GRID['deg']} deg, "
          f"{GRID['land_cells']:,} land cells of {GRID['cells']:,}")
    print(f"  baseline (blocked): lead 1 {BLOCKED[1]:.4f}   lead 7 {BLOCKED[7]:.4f}")
    print(f"  FWI at lead 1: {FWI['auc_fwi']:.4f}")
    print(f"  anchors: " + ", ".join(f"{y} {a:.4f}" for y, a in sorted(ANCHORS.items())))
    print()
    for g in doc["gates"]:
        print(f"  {g['id']}  {'hard' if g['hard'] else 'soft'}  {g['name']}")
        print(f"        {json.dumps(g['threshold'])}")
    print(f"\n  wrote {DEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
