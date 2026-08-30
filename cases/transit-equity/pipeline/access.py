"""Stage 5 · access — cumulative-opportunity access per kelurahan.

For each origin, scenario and cutoff t ∈ {30, 45, 60} min:
  jobs_access     share of the region's non-residential built-up surface (jobs proxy) reachable
  hosp_access     number of hospitals reachable; puskesmas likewise
  pop_reach       share of the region's population reachable (the "how connected" mirror)
  nearest_hosp    p50 travel time to the nearest hospital
Also a gravity-weighted variant (exp decay, β chosen so half-weight is at 45 min) so the
cutoff choice is shown not to drive the conclusions.

Outputs: data/access_adm4.parquet (kelurahan × scenario × cutoff × metric).
"""

from __future__ import annotations

import config


def main() -> None:
    print("TODO access →", config.ACCESS_ADM4)


if __name__ == "__main__":
    main()
