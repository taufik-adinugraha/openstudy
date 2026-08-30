# Case H — Forest & Commodity Watch (static snapshot with an optional weekly alert refresh)
# Spec: docs/spec-forest-watch.html — DAG stages below map 1:1 to pipeline/*.py

PY := uv run python pipeline

.PHONY: rebuild ingest alerts loss link chips validate export refresh brief web help

help:
	@echo "rebuild    full DAG: ingest → alerts → loss → link → validate → export"
	@echo "ingest     RADD, Hansen tcd30-masked loss, SDPT palm, peat, primary, GLAD-L tiles"
	@echo "           (GFW Data API, 13 IDN 10-degree tiles ≈ 1.5 GB) + UML mills + COD-AB ADM1"
	@echo "alerts     decode date_conf → 8-connected clusters ≥ 0.5 ha with seam stitching,"
	@echo "           sampling palm/peat/primary/GLAD in the same pass"
	@echo "loss       Hansen tree-cover loss hectares per province per year (2001–)"
	@echo "link       clusters × palm extent × mill catchments × peat/primary → linkage classes"
	@echo "chips      Sentinel-2 before/after chips for the top clusters (NOT BUILT — see README)"
	@echo "validate   gates G-H1..G-H4 → data/stats.json"
	@echo "export     NaN-safe web view-models into web/public/data"
	@echo "refresh    weekly: latest RADD version → alerts → link → validate → export"

rebuild: ingest alerts loss link validate export

ingest:          ## data/raw/<layer>/<tile>.tif + data/manifest.json (resumable ledger)
	$(PY)/ingest.py

alerts:          ## data/alerts/<tile>.parquet
	$(PY)/alerts.py

loss:            ## data/loss_province_year.parquet
	$(PY)/loss.py

link:            ## data/linked.parquet, data/mills_scored.parquet
	$(PY)/link.py

chips:           ## web/public/chips/*.png (small, pre-rendered)
	$(PY)/chips.py

validate:        ## gates G-H1..G-H4
	$(PY)/validate.py

export:
	$(PY)/export_web.py

refresh:         ## cron target (weekly); idempotent by RADD version
	$(PY)/ingest.py --alerts-only && $(PY)/alerts.py && $(PY)/link.py \
	  && $(PY)/validate.py && $(PY)/export_web.py

brief:           ## same mechanism as Flagship A (shared/pipeline/brief.py; PR merge = review)
	uv run python -c "from pathlib import Path; import sys; sys.path.insert(0, '../../'); \
	from shared.pipeline.brief import generate; \
	generate(Path('data/stats.json'), Path('web/src/content/brief.md'))"

web:
	cd web && npm install && npm run dev
