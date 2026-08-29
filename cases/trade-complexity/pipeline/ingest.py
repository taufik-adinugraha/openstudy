"""Stages: ingest + filter (spec §B3).

Streams the BACI zip into DuckDB one year at a time (extract → load → delete
CSV) so disk stays bounded. Product codes are loaded as VARCHAR — HS6 codes
carry leading zeros. Builds the Atlas-style sample view (country total trade
>= $1B/yr; population filter approximated by the trade floor, noted in the
methodology page). Goods only.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile

import duckdb
import pandas as pd
import requests

import config

RAW = config.DATA_DIR / "raw"
POP_CACHE = config.DATA_DIR / "population_2023.json"
WB_POP = ("https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL"
          "?date=2023&format=json&per_page=400")
TAIWAN_BACI = 490   # BACI "Other Asia, nes" — the Atlas includes Taiwan; World Bank has no row


def population_ok(con) -> set[int]:
    """BACI numeric codes of economies with population >= MIN_POPULATION (Atlas rule)."""
    if not POP_CACHE.exists():
        resp = requests.get(WB_POP, timeout=60)
        resp.raise_for_status()
        POP_CACHE.write_text(resp.text)
    rows = json.loads(POP_CACHE.read_text())[1]
    pop = {r["countryiso3code"]: r["value"] for r in rows if r.get("value")}
    countries = con.execute("SELECT * FROM countries").df()
    code_col = next(c for c in countries.columns if "code" in c.lower())
    iso3_col = next(c for c in countries.columns if "3" in c or c.lower().endswith("iso3"))
    keep = {int(c) for c, iso in zip(countries[code_col], countries[iso3_col])
            if pop.get(iso, 0) >= config.MIN_POPULATION}
    keep.add(TAIWAN_BACI)
    return keep


def build_sample(con) -> None:
    """Atlas-style sample: population >= 1M AND total trade >= $1B (v is kUSD)."""
    con.register("pop_ok_df", pd.DataFrame({"country": sorted(population_ok(con))}))
    con.execute("CREATE OR REPLACE TABLE pop_ok AS SELECT * FROM pop_ok_df")
    con.execute("""
        CREATE OR REPLACE TABLE sample_countries AS
        SELECT t, c AS country FROM (
            SELECT t, i AS c, sum(v) AS s FROM flows GROUP BY 1, 2
            UNION ALL
            SELECT t, j AS c, sum(v) AS s FROM flows GROUP BY 1, 2
        ) WHERE c IN (SELECT country FROM pop_ok)
        GROUP BY 1, 2 HAVING sum(s) >= 1e6""")
    con.execute("""
        CREATE OR REPLACE TABLE exports_hs4 AS
        SELECT t, i AS country, substr(k, 1, 4) AS hs4, sum(v) AS v
        FROM flows
        WHERE i IN (SELECT country FROM sample_countries sc WHERE sc.t = flows.t)
        GROUP BY 1, 2, 3""")
    n = con.execute("SELECT count(*) FROM exports_hs4").fetchone()[0]
    ncty = con.execute("SELECT count(DISTINCT country) FROM sample_countries WHERE t=2023").fetchone()[0]
    print(f"[ingest] exports_hs4: {n:,} rows; sample countries in 2023: {ncty} (Atlas ≈ 133)")


def load(release: str) -> None:
    zpath = RAW / f"BACI_HS92_V{release}.zip"
    if not zpath.exists():
        sys.exit(f"[ingest] missing {zpath} — download first")
    con = duckdb.connect(str(config.DB))
    con.execute("CREATE OR REPLACE TABLE flows (t SMALLINT, i INTEGER, j INTEGER, "
                "k VARCHAR, v DOUBLE, q DOUBLE)")
    with zipfile.ZipFile(zpath) as z:
        members = sorted(n for n in z.namelist() if n.startswith("BACI_HS92_Y"))
        print(f"[ingest] {len(members)} year files in {zpath.name}")
        for name in members:
            z.extract(name, RAW)
            csv = RAW / name
            con.execute(f"""
                INSERT INTO flows
                SELECT t, i, j, k, v, q FROM read_csv('{csv}', header=true,
                    types={{'t':'SMALLINT','i':'INTEGER','j':'INTEGER',
                            'k':'VARCHAR','v':'DOUBLE','q':'DOUBLE'}})""")
            csv.unlink()
            print(f"[ingest]   {name} loaded")
        for aux, table in (("country_codes", "countries"), (f"product_codes_HS92", "products")):
            member = next(n for n in z.namelist() if n.startswith(aux))
            z.extract(member, RAW)
            con.execute(f"CREATE OR REPLACE TABLE {table} AS "
                        f"SELECT * FROM read_csv('{RAW / member}', header=true, all_varchar=true)")
            (RAW / member).unlink()

    # schema checks (spec §B3): year coverage + plausible row counts
    years = con.execute("SELECT min(t), max(t), count(DISTINCT t), count(*) FROM flows").fetchone()
    print(f"[ingest] years {years[0]}–{years[1]} ({years[2]} distinct), {years[3]:,} rows")
    assert years[2] >= 29, "missing years"
    assert years[3] > 150_000_000, "row count implausibly low"

    build_sample(con)
    con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default=config.BACI_RELEASE)
    parser.add_argument("--sample-only", action="store_true",
                        help="rebuild the sample + HS4 matrix from the already-loaded flows")
    args = parser.parse_args()
    if args.sample_only:
        con = duckdb.connect(str(config.DB))
        build_sample(con)
        con.close()
    else:
        load(args.release)
    return 0


if __name__ == "__main__":
    sys.exit(main())
