#!/usr/bin/env bash
# Case J — run the rest of the pipeline once the Copernicus queues have drained enough.
#
# CDS refuses multi-year ERA5 requests (measured ceiling ~16,368 fields, and the cost is computed
# BEFORE the area subset), and it caps queued requests per dataset at about two.  So the ERA5
# backfill is a 45-job serial queue at roughly ten minutes a job, not a parallel pull, and the
# downstream stages cannot simply be chained behind it in a Makefile.
#
# This waits for the long-running ingest units to go quiet, makes one unsharded sweep to pick up
# anything a transient error dropped, then runs features -> risk -> transport -> validate ->
# export.  Every stage is idempotent and every stage builds from WHATEVER YEARS HAVE LANDED, so
# running this against a partial drain produces a shorter record rather than a failure — and
# running it again later simply lengthens the record.
#
#   sudo -n systemd-run --unit hz-finish --uid ubuntu --gid ubuntu \
#     -p MemoryMax=3G -p WorkingDirectory=/home/ubuntu/demo-lab/cases/fire-haze \
#     --setenv=HOME=/home/ubuntu --setenv=PATH=/home/ubuntu/.local/bin:/usr/bin:/bin \
#     bash pipeline/finish.sh
set -uo pipefail
cd "$(dirname "$0")/.."
UV="${UV:-$HOME/.local/bin/uv}"
WAIT_UNITS="${WAIT_UNITS:-hz-era5 hz-cams hz-fwi}"
MAX_WAIT_MIN="${MAX_WAIT_MIN:-240}"
say() { echo "[finish] $*"; }

say "waiting for [${WAIT_UNITS}] to go quiet (cap ${MAX_WAIT_MIN} min)"
deadline=$(( $(date +%s) + MAX_WAIT_MIN * 60 ))
while :; do
  busy=0
  for u in $WAIT_UNITS; do
    systemctl is-active --quiet "$u" && busy=1
  done
  [ "$busy" -eq 0 ] && break
  [ "$(date +%s)" -ge "$deadline" ] && { say "wait cap reached — proceeding on what has landed"; break; }
  sleep 120
done
say "era5 parts on disk: $(ls data/era5_parts 2>/dev/null | wc -l)"
say "cams parts on disk: $(ls data/cams_parts 2>/dev/null | wc -l)"
say "fwi  parts on disk: $(ls data/fwi_parts  2>/dev/null | wc -l)"

# One unsharded sweep: anything a transient error dropped is still missing, and this notices.
say "gap-filling pass (era5)"
"$UV" run python pipeline/ingest_era5.py --max-minutes 20 \
  || say "gap-fill returned $? — continuing with what exists"

# Fold whatever CAMS parts have landed into the tables without waiting for the rest of the queue.
say "consolidating cams parts"
"$UV" run python pipeline/ingest_cams.py --consolidate-only || say "cams consolidate: $?"

for stage in features risk transport validate export_web; do
  say "stage: $stage"
  if ! "$UV" run python "pipeline/${stage}.py"; then
    say "stage $stage FAILED — stopping; rerun this script after fixing"
    exit 1
  fi
done
say "complete — dashboard view-models refreshed"
