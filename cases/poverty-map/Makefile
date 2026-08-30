# Case F — Poverty Mapping from Space (static snapshot, D3)
# Spec: docs/spec-poverty-map.html — DAG stages below map 1:1 to pipeline/*.py

PY := uv run python pipeline

.PHONY: rebuild ingest features train downscale validate export brief web help

help:
	@echo "rebuild    full DAG: ingest → features → train → downscale → validate → export"
	@echo "ingest     boundaries (geoBoundaries ADM2 / COD-AB ADM3) + BPS P0/P1/P2 2016-2025 + rasters + Open Buildings cells"
	@echo "features   zonal features per regency and kecamatan (buildings, lights, pop, land cover, roads, built-up)"
	@echo "train      gradient boosting vs BPS regency P0 with spatial (province-block) cross-validation"
	@echo "downscale  small-area estimation: regency model applied to kecamatan features, benchmarked to regency totals"
	@echo "validate   gates G-F1..G-F4 → data/stats.json"
	@echo "export     web view-models (GeoJSON/JSON ≤ 3 MB each) into web/public/data"
	@echo "brief      insight brief from stats.json (D11; PR = human review)"

rebuild: ingest features train downscale validate export

ingest:          ## raw pulls, all clipped to Indonesia (or Java when SCOPE=java)
	$(PY)/ingest.py

features:        ## zonal statistics → data/features_{adm2,adm3}.parquet
	$(PY)/features.py

train:           ## LightGBM + spatial CV → data/model/, data/cv_predictions.parquet
	$(PY)/model.py

downscale:       ## kecamatan estimates → data/estimates_adm3.parquet
	$(PY)/downscale.py

validate:        ## gates G-F1..G-F4
	$(PY)/validate.py

export:          ## view-models for web/
	$(PY)/export_web.py

brief:           ## same mechanism as Flagship A (shared/pipeline/brief.py; PR merge = review)
	uv run python -c "from pathlib import Path; import sys; sys.path.insert(0, '../../'); \
	from shared.pipeline.brief import generate; \
	generate(Path('data/stats.json'), Path('web/src/content/brief.md'))"

web:
	cd web && npm install && npm run dev
