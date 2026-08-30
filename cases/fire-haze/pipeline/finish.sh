#!/usr/bin/env bash
# Case J — run the rest of the pipeline once the ERA5 shards have drained.
#
# CDS queues requests server-side and a multi-year backfill takes hours to days, so the
# downstream stages cannot simply be chained behind them in a Makefile.  This waits for the
# sharded ingest to go quiet, makes one single-threaded pass to pick up any month a shard
# skipped on a transient error, then runs features -> risk -> transport -> validate -> export.
#
# Every stage is idempotent, so re-running this is always safe.
#   sudo -n systemd-run --unit fh-finish --uid ubuntu --gid ubuntu \
#     -p MemoryMax=3G -p WorkingDirectory=/home/ubuntu/demo-lab/cases/fire-haze \
#     --setenv=HOME=/home/ubuntu --setenv=PATH=/home/ubuntu/.local/bin:/usr/bin:/bin \
#     bash pipeline/finish.sh
set -uo pipefail
cd "$(dirname "$0")/.."
UV="${UV:-$HOME/.local/bin/uv}"
SHARDS="${SHARDS:-4}"
say() { echo "[finish] $*"; }

say "waiting for fh-era5-* to drain (${SHARDS} shards)"
while :; do
  busy=0
  for i in $(seq 0 $((SHARDS - 1))); do
    systemctl is-active --quiet "fh-era5-$i" && busy=1
  done
  [ "$busy" -eq 0 ] && break
  sleep 120
done
say "shards done: $(ls data/era5_parts 2>/dev/null | wc -l) monthly parts on disk"

# One unsharded sweep: any month a shard dropped on a transient CDS error is still missing,
# and this is the pass that notices.
say "gap-filling pass"
"$UV" run python pipeline/ingest_era5.py || say "gap-fill returned $? — continuing with what exists"

for stage in ingest_cams features risk transport validate export_web; do
  say "stage: $stage"
  if ! "$UV" run python "pipeline/${stage}.py"; then
    say "stage $stage FAILED — stopping; rerun this script after fixing"
    exit 1
  fi
done
say "complete — dashboard view-models refreshed"
