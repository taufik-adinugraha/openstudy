#!/usr/bin/env bash
# Case E — run the rest of the pipeline once the ERA5 shards have drained.
#
# ERA5 requests queue server-side at Copernicus and a full backfill takes hours,
# so the downstream stages cannot simply be chained behind them in a Makefile.
# This waits for the sharded ingest to go quiet, makes one single-threaded pass
# to pick up any month a shard skipped on a transient error, then runs
# features -> model -> validate -> export.
#
# Every stage is idempotent, so re-running this is always safe.
#   sudo -n systemd-run --unit aq-finish --uid ubuntu --gid ubuntu \
#     -p MemoryMax=3G -p WorkingDirectory=/home/ubuntu/demo-lab/cases/air-quality \
#     --setenv=HOME=/home/ubuntu --setenv=PATH=/home/ubuntu/.local/bin:/usr/bin:/bin \
#     bash pipeline/finish.sh
set -uo pipefail
cd "$(dirname "$0")/.."
UV="${UV:-$HOME/.local/bin/uv}"
say() { echo "[finish] $*"; }

say "waiting for aq-era5-* to drain"
while systemctl is-active --quiet aq-era5-0 || \
      systemctl is-active --quiet aq-era5-1 || \
      systemctl is-active --quiet aq-era5-2; do
  sleep 120
done
say "shards done: $(ls data/era5_parts 2>/dev/null | wc -l) monthly parts on disk"

# One unsharded sweep: any month a shard dropped on a transient CDS error is
# still missing, and this is the pass that notices.
say "gap-filling pass"
"$UV" run python pipeline/ingest_era5.py || say "gap-fill pass returned $? — continuing with what exists"

for stage in features model validate export_web; do
  say "stage: $stage"
  if ! "$UV" run python "pipeline/${stage}.py"; then
    say "stage $stage FAILED — stopping; rerun this script after fixing"
    exit 1
  fi
done
say "complete — dashboard view-models refreshed"
