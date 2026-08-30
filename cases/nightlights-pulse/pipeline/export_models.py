"""Export the model stage's view-models for the dashboard (sibling of export_web.py;
existing files/keys untouched).

Writes into web/public/data/:
  index.json        seasonally adjusted lights index — national + per-regency
                    arrays aligned to `months` (sa = coverage-normalised,
                    deseasonalised SOL; tr = trend; null where no composite)
  calibration.json  gate G-A1 table + scatter points, panel/growth elasticities,
                    out-of-sample test, nowcast series with bands, BPS reference
                    growth, movers, weak-fit list, flare summary

Rerun after `models.py calibrate`. The page fetches both at runtime and shows a
"pending" note when they are absent, so the build never depends on them.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

import config
from models import CALIB, INDEX, NOWCAST

WEB = config.CASE_DIR / "web" / "public" / "data"


def sig(v, nd=4):
    if v is None or not np.isfinite(v):
        return None
    return float(f"{v:.{nd}g}")


def main() -> int:
    if not (INDEX.exists() and config.STATS_JSON.exists() and CALIB.exists()):
        sys.exit("[export-models] run `models.py deseason` and `models.py calibrate` first")
    WEB.mkdir(parents=True, exist_ok=True)
    idx = pd.read_parquet(INDEX)
    stats = json.loads(config.STATS_JSON.read_text())
    cal = pd.read_parquet(CALIB)

    nat = idx[idx["level"] == "national"].sort_values("month")
    months = list(nat["month"])
    reg = idx[idx["level"] == "regency"]
    regions = {}
    for rid, d in reg.groupby("region_id"):
        d = d.set_index("month").reindex(months)
        regions[rid] = {
            "sa": [sig(v) for v in d["sol_sa"]],
            "tr": [sig(v) for v in d["trend"]],
        }
    index = {
        "months": months,
        "national": [{"m": r.month, "raw": sig(r.sol_raw), "sa": sig(r.sol_sa), "tr": sig(r.trend),
                      "cov": sig(r.coverage, 3), "low": bool(r.flag_low_coverage), "ram": sig(r.ramadan_share, 3),
                      "flare": sig(r.flare_share, 3)} for r in nat.itertuples()],
        "regions": regions,
        "ramadanBetaNational": stats["deseason"]["ramadan_beta_national"],
        "notes": stats["deseason"]["weights"],
    }
    (WEB / "index.json").write_text(json.dumps(index, allow_nan=False, separators=(",", ":")))

    # calibration scatter: per regency, x/y arrays aligned to years
    years = sorted(int(y) for y in cal["year"].unique())
    pts = []
    for rid, d in cal.groupby("region_id"):
        d = d.set_index("year").reindex(years)
        pts.append({"id": rid, "name": d["region_name"].dropna().iat[0], "code": str(d["xw_code"].dropna().iat[0]),
                    "x": [sig(v) for v in d["lL"]], "y": [sig(v) for v in d["lP"]],
                    "sr": [sig(v, 3) for v in d["std_resid"]], "flare": bool((d["flare_share"].fillna(0) >= 0.05).any())})
    nowcast_reg = None
    if NOWCAST.exists():
        nc = pd.read_parquet(NOWCAST)
        nowcast_reg = [{"id": r.region_id, "g": sig(r.g), "lo": sig(r.lo), "hi": sig(r.hi), "gl": sig(r.lights_growth),
                        "obs": bool(r.observed), "flare": bool(r.flare_flag)} for r in nc.itertuples()]
    calibration = {
        "generated": stats["generated"], "latestMonth": stats["latest_month"], "bps": stats["bps"], "gates": stats["gates"],
        "levels": stats["levels"], "scatter": {"years": years, "points": pts},
        "panel": stats["panel"], "growth": stats["growth"], "oos": stats["oos"],
        "nowcast": {k: v for k, v in stats["nowcast"].items()}, "nowcastRegency": nowcast_reg,
        "weakFit": stats["weak_fit"], "deseason": stats["deseason"], "flares": stats["flares"],
    }
    (WEB / "calibration.json").write_text(json.dumps(calibration, allow_nan=False, separators=(",", ":")))
    print(f"[export-models] index.json ({len(regions)} regions × {len(months)} months, "
          f"{(WEB / 'index.json').stat().st_size / 1e6:.2f} MB) · calibration.json ({len(pts)} scatter regencies × {len(years)} years, "
          f"{(WEB / 'calibration.json').stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
