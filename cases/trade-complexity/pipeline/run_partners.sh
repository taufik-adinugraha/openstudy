#!/usr/bin/env bash
# Partner/import extension, end to end and resumable at every stage.
# Waits for any in-flight download unit, tops the archive up to its exact byte
# count, slices Indonesia's rows year by year, then builds the view-models and
# evaluates gates G-B5..G-B7.
set -u
cd /home/ubuntu/demo-lab/cases/trade-complexity || exit 1
export PATH=$HOME/.local/bin:$PATH

while systemctl is-active --quiet tc-fetch; do
  echo "[run] waiting for tc-fetch…"; sleep 20
done

echo "[run] === fetch ==="
./pipeline/fetch_baci.sh || { echo "[run] FETCH FAILED"; exit 1; }

echo "[run] === slice ==="
uv run python pipeline/partners.py slice --release 202601 || { echo "[run] SLICE FAILED"; exit 1; }

echo "[run] === facts ==="
uv run python pipeline/partners.py facts || { echo "[run] FACTS FAILED"; exit 1; }

echo "[run] PARTNERS PIPELINE DONE"
