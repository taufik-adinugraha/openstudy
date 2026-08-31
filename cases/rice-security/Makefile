# Case I — Rice & Food Security (static snapshot; D3 rule, no cron)
# Spec: docs/spec-rice-security.html — DAG stages below map 1:1 to pipeline/*.py
#
# Order matters and is not cosmetic: ingest_stats is cheap and gates the whole
# case (if BPS KSA is not retrievable there is nothing to benchmark against), so
# it runs FIRST and fails loudly.  ingest_sar is the expensive stage and is
# resumable per (orbit, date) — never restart it from zero.

PY := uv run python pipeline

.PHONY: rebuild aux stats climate sar backscatter phenology area model validate audit export article brief web help

help:
	@echo "rebuild      full DAG: stats -> aux -> climate -> sar -> backscatter -> phenology"
	@echo "             -> area -> model -> validate -> audit -> export -> article"
	@echo "stats        BPS KSA harvested area + production + productivity (WebAPI, domain 0000)"
	@echo "aux          admin boundaries (geoBoundaries gbOpen / COD-AB) + rice reference mask"
	@echo "climate      CHIRPS rainfall, ERA5 met, ENSO/IOD indices for the AOI"
	@echo "sar          Sentinel-1 GRD/RTC time series for the AOI  (EXPENSIVE — resumable)"
	@echo "backscatter  speckle handling + per-cell VV/VH/ratio series on a common date grid"
	@echo "phenology    flooding minima / heading maxima -> planting + harvest dates per cell"
	@echo "area         planted and harvested area per kabupaten per month and per season"
	@echo "model        calibration to KSA + harvest-timing predictor"
	@echo "validate     gates G-I1..G-I5 -> data/stats.json"
	@echo "audit        the review's independent tests -> data/audit.json (feeds both pages)"
	@echo "export       NaN-safe web view-models into web/public/data"
	@echo "article      review-article data layer -> web/src/data/article.json"

rebuild: stats aux climate sar backscatter phenology area model validate audit export article

stats:           ## data/bps_ksa.parquet, data/bps_production.parquet
	$(PY)/ingest_stats.py

aux:             ## data/adm.parquet, data/rice_mask.tif
	$(PY)/ingest_aux.py

climate:         ## data/chirps.parquet, data/era5.parquet, data/enso.parquet
	$(PY)/ingest_climate.py

sar:             ## data/raw/s1/**, data/manifest.json  (resumable ledger; disk-guarded)
	$(PY)/ingest_sar.py

backscatter:     ## data/backscatter/<tile>.parquet
	$(PY)/backscatter.py

phenology:       ## data/phenology.parquet
	$(PY)/phenology.py

area:            ## data/area_month.parquet, data/area_season.parquet
	$(PY)/area.py

model:           ## data/model.parquet, data/model_meta.json
	$(PY)/model.py

validate:        ## gates G-I1..G-I5
	$(PY)/validate.py

audit:           ## data/audit.json — must run BEFORE export (summary.json embeds it)
	$(PY)/audit.py

export:
	$(PY)/export_web.py

article:         ## web/src/data/article.json — the review article's only data source
	$(PY)/article.py web/public/data/summary.json data/audit.json web/src/data/article.json

brief:           ## same mechanism as Flagship A (shared/pipeline/brief.py; PR merge = review)
	uv run python -c "from pathlib import Path; import sys; sys.path.insert(0, '../../'); \
	from shared.pipeline.brief import generate; \
	generate(Path('data/stats.json'), Path('web/src/content/brief.md'))"

web:
	cd web && npm install && npm run dev
