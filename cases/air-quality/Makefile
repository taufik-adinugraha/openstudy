# Case E — Jabodetabek Air Quality Nowcast
# Spec: ../air-quality/README.md (§Spec). Every stage is resumable; rerun to continue.

PY := uv run python pipeline

.PHONY: ingest ground era5 fire features model validate export all refresh

## --- Stage 1 · acquisition (each resumable, safe to rerun) -------------
ground:          ## OpenAQ v3 PM2.5 ground sensors -> data/ground_hourly.parquet + stations.json
	$(PY)/ingest_ground.py

inventory:       ## station inventory only (live / stale / silent) — cheap, no measurement pulls
	$(PY)/ingest_ground.py --inventory-only

era5:            ## ERA5 hourly meteorology, newest month first -> data/era5_hourly.parquet
	$(PY)/ingest_era5.py

fire:            ## FIRMS VIIRS hotspots -> upwind sector-day aggregates, raw never stored
	$(PY)/ingest_firms.py

ingest: ground fire era5

finish:          ## wait for the sharded ERA5 units to drain, gap-fill, then run everything downstream
	bash pipeline/finish.sh

## --- Stage 2-4 · modelling ---------------------------------------------
features:        ## join target + meteorology + upwind fire -> data/features.parquet
	$(PY)/features.py

model:           ## gradient boosting per horizon vs persistence -> data/model_eval.parquet
	$(PY)/model.py

validate:        ## gates G-E1..G-E5 -> data/stats.json
	$(PY)/validate.py

export:          ## NaN-safe view-models -> web/src/data/*.json
	$(PY)/export_web.py

all: ingest features model validate export

# Operational refresh: what a cron would run. Ingest stages no-op when nothing
# new has been published, so this is safe to run on a schedule.
refresh: ground fire era5 features model validate export
