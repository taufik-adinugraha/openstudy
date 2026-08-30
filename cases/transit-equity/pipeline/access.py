"""Stage 5 · access — cumulative-opportunity access per kelurahan.

For every origin × scenario × cutoff t ∈ {30, 45, 60} min (p50 travel time over the
07:00–09:00 departure window, scheduled — not congestion-adjusted):

  jobs_share    share of the region's non-residential built-up surface (the jobs proxy —
                floorspace, NOT employment) reachable within t
  pop_share     share of the region's population reachable within t
  hospitals     hospitals reachable within t; clinics likewise
  nearest_hosp  travel time to the nearest hospital (cutoff-independent)
  gravity_share exp-decay weighted jobs access, half-weight at 45 min, so the reader can see
                the cutoff choice is not driving the conclusion

Outputs: data/access_adm4.parquet (long: id × scenario × cutoff) and the per-origin extras.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config
import matrix
import points
import util
from util import log


def compute(scenario: str, dests: pd.DataFrame, origins: pd.DataFrame) -> pd.DataFrame:
    m = matrix.load(scenario)
    d = dests.set_index("id")[["nres_m2", "pop", "hospital", "clinic"]]
    m = m.join(d, on="to_id")
    tot = {"nres_m2": float(dests["nres_m2"].sum()), "pop": float(dests["pop"].sum()),
           "hospital": float(dests["hospital"].sum()), "clinic": float(dests["clinic"].sum())}
    rows = []
    for c in config.CUTOFFS_MIN:
        g = m[m.tt <= c].groupby("from_id")[["nres_m2", "pop", "hospital", "clinic"]].sum()
        g = g.reindex(origins["id"]).fillna(0.0)
        rows.append(pd.DataFrame({
            "id": g.index, "scenario": scenario, "cutoff": c,
            "jobs_m2": g["nres_m2"].values, "jobs_share": g["nres_m2"].values / tot["nres_m2"],
            "pop_reached": g["pop"].values, "pop_share": g["pop"].values / tot["pop"],
            "hospitals": g["hospital"].values, "clinics": g["clinic"].values,
        }))
    out = pd.concat(rows, ignore_index=True)

    beta = math.log(2) / config.GRAVITY_HALF_WEIGHT_MIN
    m["w"] = np.exp(-beta * m.tt) * m["nres_m2"]
    grav = m.groupby("from_id")["w"].sum().reindex(origins["id"]).fillna(0.0) / tot["nres_m2"]
    hosp = m[m["hospital"] > 0].groupby("from_id")["tt"].min().reindex(origins["id"])
    extra = pd.DataFrame({"id": origins["id"].values, "scenario": scenario,
                          "gravity_share": grav.values, "nearest_hosp_min": hosp.values})
    out = out.merge(extra, on=["id", "scenario"], how="left")
    reach = out[out.cutoff == 60]
    log(f"{scenario}: median 60-min jobs access {reach.jobs_share.median():.1%}, "
        f"unreachable origins {(reach.jobs_share == 0).sum()}, "
        f"median nearest hospital {np.nanmedian(extra.nearest_hosp_min):.0f} min")
    return out


def main() -> None:
    origins = points.build_origins()
    dests = points.build_destinations()
    have = sorted({p.name.rsplit("_", 1)[0] for p in matrix.PARTS.glob("*.parquet")})
    log("scenarios with matrix parts:", have)
    frames = [compute(s, pd.DataFrame(dests.drop(columns="geometry")),
                      pd.DataFrame(origins.drop(columns="geometry"))) for s in have]
    out = pd.concat(frames, ignore_index=True)
    meta = pd.DataFrame(origins.drop(columns="geometry"))[
        ["id", "adm4_name", "adm3_name", "adm2_name", "adm1_name", "pop", "lat", "lon"]]
    out = out.merge(meta, on="id", how="left")
    out.to_parquet(config.ACCESS_ADM4, index=False)
    log("access →", config.ACCESS_ADM4, len(out), "rows")


if __name__ == "__main__":
    main()
