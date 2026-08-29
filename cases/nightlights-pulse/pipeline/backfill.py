"""Historical backfill — streaming and resumable.

For each month: ingest → zonal → delete that month's rasters (raw HDF5 + COGs),
keeping only the ledger rows, so disk use stays ~1 GB regardless of span.
Months already in the ledger are skipped, so the job can be killed and rerun.
Unpublished months (LAADS lag) are skipped cleanly.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import config

PIPELINE = Path(__file__).resolve().parent


def month_range(start: str, end: str):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1


def done_months(product: str) -> set[str]:
    if not config.LEDGER.exists():
        return set()
    import pandas as pd

    df = pd.read_parquet(config.LEDGER, columns=["month", "product"])
    return set(df.loc[df["product"] == product, "month"].unique())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2018-01")
    parser.add_argument("--end", required=True, help="YYYY-MM inclusive")
    parser.add_argument("--product", default="VJ146A3")
    parser.add_argument("--keep-rasters", action="store_true")
    args = parser.parse_args()

    done = done_months(args.product)
    todo = [m for m in month_range(args.start, args.end) if m not in done]
    print(f"[backfill] {len(todo)} months to do ({args.product}); {len(done)} already in ledger",
          flush=True)

    failures: list[str] = []
    for i, month in enumerate(todo, 1):
        print(f"[backfill] ({i}/{len(todo)}) {month}", flush=True)
        raw = config.DATA_DIR / "raw" / "bm" / month
        try:
            subprocess.run([sys.executable, str(PIPELINE / "ingest.py"),
                            "--month", month, "--products", args.product], check=True)
            if not (raw / f"{args.product}_radiance.tif").exists():
                print(f"[backfill] {month}: not published — skipped", flush=True)
                continue
            subprocess.run([sys.executable, str(PIPELINE / "zonal.py"),
                            "--month", month, "--product", args.product], check=True)
        except subprocess.CalledProcessError as err:
            failures.append(month)
            print(f"[backfill] {month} FAILED: {err}", flush=True)
        finally:
            if not args.keep_rasters:
                shutil.rmtree(raw, ignore_errors=True)
                shutil.rmtree(config.DATA_DIR / "derived" / "bm" / month, ignore_errors=True)

    print(f"[backfill] complete — {len(failures)} failures: {failures or 'none'}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
