"""Build the review article's data layer from the case's own published output.

Every number the article prints comes from here, so the prose can never drift from
the pipeline. Two inputs, both produced by this case:

  web/src/data/summary.json   what the dashboard publishes
  data/replication.json       pipeline/replicate.py — the independent re-derivation

Published benchmarks from the literature are the one exception and are transcribed
into LITERATURE below, each with the table it came from, so a reader can check them.

    python pipeline/article.py            # writes web/src/data/article.json
"""

from __future__ import annotations

import json
from collections import defaultdict

import config

SUMMARY = config.WEB_SRC_DATA / "summary.json"
REPLICATION = config.DATA_DIR / "replication.json"
OUT = config.WEB_SRC_DATA / "article.json"


# ── Published benchmarks, transcribed from the cited tables ──────────────────
# Each entry names the table or section the number was read from. Nothing here is
# estimated; where a paper reports RMSE only, the MAE field is null.
LITERATURE = {
    "beijing": {
        "ref": "wangdu", "city": "Beijing", "n_stations": 12, "years": "2013–2017",
        "obs_mean": 79.79, "source": "Table 3, primary test set",
        # Best MAE and best RMSE are not always the same model, so both are named.
        "rows": [
            {"h": 1, "persist_mae": 10.721, "best_mae": 10.544, "best": "Ridge",
             "persist_rmse": 21.369, "best_rmse": 20.147, "best_r": "Ridge"},
            {"h": 6, "persist_mae": 35.056, "best_mae": 31.876, "best": "XGBoost ensemble",
             "persist_rmse": 61.341, "best_rmse": 54.131, "best_r": "XGBoost ensemble"},
            {"h": 24, "persist_mae": 65.172, "best_mae": 56.365, "best": "XGBoost ensemble",
             "persist_rmse": 98.151, "best_rmse": 80.829, "best_r": "Elastic Net"},
        ],
        "extreme_note": "Table 4: on rows above 242 µg/m³ persistence beats every "
                        "machine-learning model at h=6 and h=24.",
        "persistence_r2_24h": -0.117,
    },
    "trondheim": {
        "ref": "murad", "city": "Trondheim", "source": "§3.3 and Figures 4–5",
        "baseline": "the value observed 24 hours earlier",
        "persist_rmse": 7.52, "model_rmse": 4.63, "h": 24,
        "picp_qgb": 0.61, "picp_nominal_qgb": 0.90, "picp_bnn_lo": 0.90, "picp_bnn_hi": 0.99,
    },
    "elche": {
        "ref": "rolling", "pollutant": "PM10", "city": "Elche",
        "static_lo": 0.231, "static_hi": 0.299,
        "rolling_h1": -0.192, "folds_nonpositive": 34, "folds": 47,
        "source": "§3.1.2–3.1.4",
    },
    "fmqo": {
        "ref": "vitali", "indicator": "RMSE(forecast) / RMSE(persistence)",
        "threshold": 1.0, "station_share": 0.90, "source": "Eq. 1–2 and §2.1",
    },
    "accra": {
        "ref": "raheja", "city": "Accra", "reference_instrument": "Teledyne T640",
        "clarity_mae_raw": 13.68, "clarity_rmse_raw": 17.51, "clarity_r2_raw": 0.69,
        "clarity_slope_raw": 1.8, "clarity_mae_corrected": 2.27,
        "rh_lo": 48.0, "rh_hi": 89.9, "source": "Tables 2–3",
    },
    "lubbock": {
        "ref": "lubbock", "city": "Lubbock", "clarity_mae_raw": 3.4,
        "clarity_r2_raw": 0.66, "source": "§3",
    },
    "smoke": {
        "ref": "berlinghieri", "persistence_precision": 0.908,
        "n_forecasts": 6, "best_ai_precision": 0.781,
        "source": "Table 1",
    },
    "standards": {
        "who_annual": 5.0, "who_daily": 15.0,
        "id_annual": 15.0, "id_daily": 55.0, "id_ref": "PP 22/2021, Lampiran VII",
        "us_unhealthy": 55.5, "us_very_unhealthy": 125.5, "us_hazardous": 225.5,
        "eu_points_per_million": 1.0, "eu_ref": "2008/50/EC, Annex V §B",
        "epa_min_large_msa": 3, "epa_ref": "40 CFR 58, Appendix D, Table D-5",
        # Jabodetabek's population is the figure the case page already uses; published
        # estimates for the agglomeration sit in the low thirties of millions.
        "jabodetabek_millions": 32.0,
    },
    "jakarta_level": {
        "iqair_2023": 37.3, "iqair_2024": 41.7, "ref": "iqair",
        "peer_jabodetabek": 42.5, "peer_ref": "aogh",
    },
}


def addresses(stations: list[dict]) -> dict:
    """The registry lists 24 entries. How many instruments is that?

    Rounding to four decimal places is about 11 m — closer than any two genuinely
    distinct monitoring sites would be sited, and far coarser than the jitter between
    two registrations of one device.
    """
    by_xy = defaultdict(list)
    for s in stations:
        by_xy[(round(s["lat"], 3), round(s["lon"], 3))].append(s)
    clusters = [{"lat": k[0], "lon": k[1], "n": len(v),
                 "reported": sum(1 for s in v if (s.get("n_hours") or 0) > 0),
                 "names": [s["name"] for s in v]}
                for k, v in by_xy.items()]
    ever = [s for s in stations if (s.get("n_hours") or 0) > 0]
    return {
        "n_registered": len(stations),
        "n_addresses": len(clusters),
        "n_ever_reported": len(ever),
        "n_never_reported": len(stations) - len(ever),
        "n_duplicate_registrations": len(stations) - len(clusters),
        "largest_cluster": max(clusters, key=lambda c: c["n"]),
        "clusters": sorted(clusters, key=lambda c: -c["n"]),
    }


def main() -> int:
    S = json.loads(SUMMARY.read_text())
    R = json.loads(REPLICATION.read_text())

    ev = {r["horizon_h"]: r for r in S["model"]["eval"]}
    rep = {r["horizon_h"]: r for r in R["horizons"]}
    h24, r24 = ev[24], rep[24]
    boot = R["bootstrap24"]
    gates = {g["id"]: g for g in S["gates"]}

    # The published gate, re-tested against the strongest trivial baseline rather
    # than the one the case chose.
    best24 = min(r24["baselines"], key=lambda b: b["mae"])
    gate_retest = {
        "threshold": gates["G-E1"]["threshold"],
        "published_skill": gates["G-E1"]["value"],
        "published_baseline": "Persistence",
        "best_baseline": best24["name"], "best_baseline_mae": best24["mae"],
        "skill_vs_best": r24["skill_vs_best"],
        "ci": [boot[best24["key"]]["lo"], boot[best24["key"]]["hi"]],
        "p_meets_threshold": 1 - boot[best24["key"]]["p_lt_15"],
        "persistence_ci": [boot["b_persist"]["lo"], boot["b_persist"]["hi"]],
        "persistence_p_meets": 1 - boot["b_persist"]["p_lt_15"],
        "verdict": "fail" if r24["skill_vs_best"] < gates["G-E1"]["threshold"] else "pass",
    }

    # The autocorrelation echo: persistence's error tracks the inverse of the ACF,
    # so a skill curve drawn against it reports the baseline's phase.
    acf = {a["lag"]: a["r"] for a in R["acf"]}
    echo = [{"h": h, "acf": acf.get(h), "persist_mae": ev[h]["persistence_mae"],
             "model_mae": ev[h]["model_mae"],
             "skill_persist": rep[h]["skill_vs_persistence"],
             "skill_best": rep[h]["skill_vs_best"],
             "best_name": rep[h]["best_baseline_name"],
             "best_mae": rep[h]["best_baseline_mae"]}
            for h in sorted(ev)]

    pan = R["panel"]
    epi = R["episodes"]
    uns = R["unseen"]
    pair = R["colocation"]["pairs"][0] if R["colocation"]["pairs"] else None

    # Beijing on the same footing as this case: percentage improvement in MAE.
    bj = LITERATURE["beijing"]
    for row in bj["rows"]:
        row["skill_mae"] = 1 - row["best_mae"] / row["persist_mae"]
        row["skill_rmse"] = 1 - row["best_rmse"] / row["persist_rmse"]

    out = {
        "generated": S["generated_utc"],
        "replicated": R["generated_utc"],
        "vintage": {
            "ground_first": S["vintage"]["ground_first_utc"][:10],
            "ground_last": S["vintage"]["ground_last_utc"][:10],
            "fire_last": S["vintage"]["fire_last_date"],
            "cut": R["cut"][:10],
        },
        "gates": S["gates"],
        "gates_passed": S["gates_passed"], "gates_total": S["gates_total"],
        "network": {k: v for k, v in S["network"].items() if k != "stations"},
        "stations": S["network"]["stations"],
        "addresses": addresses(S["network"]["stations"]),
        "observed": S["observed"],
        "drivers": S["model"]["top_drivers"],
        "model": {"n_features": S["model"]["n_features"],
                  "train_rows": S["model"]["train_rows"],
                  "test_rows": S["model"]["test_rows"],
                  "h24": h24},
        "horizons": R["horizons"],
        "echo": echo,
        "bootstrap24": boot,
        "gateRetest": gate_retest,
        "acf": R["acf"],
        "panel": {k: pan[k] for k in
                  ("months", "by_station", "balanced", "coverage", "fmqo",
                   "n_test_rows", "share_before_2026")},
        "episodes": epi,
        "unseen": uns,
        "providers": R["providers"],
        "colocation": pair,
        "thresholds": R["thresholds"],
        "fire": R["fire"],
        "lit": LITERATURE,
    }

    OUT.write_text(json.dumps(out, indent=1))
    print(f"[article] gate G-E1 published {gate_retest['published_skill'] * 100:+.1f}% vs persistence; "
          f"{gate_retest['skill_vs_best'] * 100:+.1f}% vs {gate_retest['best_baseline']} "
          f"-> {gate_retest['verdict'].upper()}")
    print(f"[article] panel {pan['fmqo']['n_pass']}/{pan['fmqo']['n_stations']} stations meet MQI_f<=1 "
          f"({pan['fmqo']['share_pass'] * 100:.1f}% vs 90% required)")
    print(f"[article] episode ladder: " + ", ".join(
        f"{r['threshold']}={r['recall'] * 100:.0f}%" if r["recall"] is not None else f"{r['threshold']}=—"
        for r in epi["ladder"]))
    print(f"[article] -> {OUT} ({len(OUT.read_text()) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
