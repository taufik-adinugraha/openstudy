# Case H — Forest & Commodity Watch (static snapshot with an optional weekly alert refresh)
# Spec: docs/spec-forest-watch.html — DAG stages below map 1:1 to pipeline/*.py

PY := uv run python pipeline

.PHONY: rebuild ingest alerts link chips validate export refresh brief web help

help:
	@echo "rebuild    full DAG: ingest → alerts → link → chips → validate → export"
	@echo "ingest     Hansen GFC v1.12 tiles (IDN), RADD + GLAD-L tiles for focus provinces (GFW Data API key), concessions, palm map, mills, boundaries"
	@echo "alerts     decode date_conf → weekly alert rasters → connected-component clusters (≥ 0.5 ha) per province"
	@echo "link       clusters × concessions × palm extent × mill catchments → linked/unlinked scoring"
	@echo "chips      Sentinel-2 before/after chips for the top clusters (Earth Search STAC)"
	@echo "validate   gates G-H1..G-H4 → data/stats.json"
	@echo "export     web view-models (clusters GeoJSON, province ledgers, mill scores) into web/public/data"
	@echo "refresh    weekly: pull the latest RADD version for the focus provinces and re-run alerts → link → export"

rebuild: ingest alerts link chips validate export

ingest:
	$(PY)/ingest.py

alerts:          ## data/alerts/{province}/clusters.parquet
	$(PY)/alerts.py

link:            ## data/linked.parquet, data/mills_scored.parquet
	$(PY)/link.py

chips:           ## web/public/chips/*.png (small, pre-rendered)
	$(PY)/chips.py

validate:        ## gates G-H1..G-H4
	$(PY)/validate.py

export:
	$(PY)/export_web.py

refresh:         ## cron target (weekly)
	$(PY)/ingest.py --alerts-only && $(PY)/alerts.py && $(PY)/link.py && $(PY)/export_web.py

brief:           ## same mechanism as Flagship A (shared/pipeline/brief.py; PR merge = review)
	uv run python -c "from pathlib import Path; import sys; sys.path.insert(0, '../../'); \
	from shared.pipeline.brief import generate; \
	generate(Path('data/stats.json'), Path('web/src/content/brief.md'))"

web:
	cd web && npm install && npm run dev
