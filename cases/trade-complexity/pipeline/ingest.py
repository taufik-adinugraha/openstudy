"""Stages: ingest + filter (spec §B3).

Streams the BACI zip into DuckDB one year at a time (extract → load → delete
CSV) so disk stays bounded. Product codes are loaded as VARCHAR — HS6 codes
carry leading zeros. Builds the Atlas-style sample view (country total trade
>= $1B/yr; population filter approximated by the trade floor, noted in the
methodology page). Goods only.
"""

from __future__ import annotations

import argparse
import sys
import zipfile

import duckdb

import config

RAW = config.DATA_DIR / "raw"


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

    # Atlas-style sample: countries with total trade >= $1B (v is kUSD)
    con.execute("""
        CREATE OR REPLACE TABLE sample_countries AS
        SELECT t, c AS country FROM (
            SELECT t, i AS c, sum(v) AS s FROM flows GROUP BY 1, 2
            UNION ALL
            SELECT t, j AS c, sum(v) AS s FROM flows GROUP BY 1, 2
        ) GROUP BY 1, 2 HAVING sum(s) >= 1e6""")
    # HS4 export matrix over the sample
    con.execute("""
        CREATE OR REPLACE TABLE exports_hs4 AS
        SELECT t, i AS country, substr(k, 1, 4) AS hs4, sum(v) AS v
        FROM flows
        WHERE i IN (SELECT country FROM sample_countries sc WHERE sc.t = flows.t)
        GROUP BY 1, 2, 3""")
    n = con.execute("SELECT count(*) FROM exports_hs4").fetchone()[0]
    ncty = con.execute("SELECT count(DISTINCT country) FROM sample_countries WHERE t=2023").fetchone()[0]
    print(f"[ingest] exports_hs4: {n:,} rows; sample countries in 2023: {ncty}")
    con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default=config.BACI_RELEASE)
    args = parser.parse_args()
    load(args.release)
    return 0


if __name__ == "__main__":
    sys.exit(main())
