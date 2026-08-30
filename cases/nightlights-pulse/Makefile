# Flagship A — Nighttime-Lights Pulse
# Spec: https://claude.ai/code/artifact/06da6f68-9ed2-4a61-b8ce-bac9086856d3

PY := uv run python pipeline

.PHONY: month rebuild-all validate brief deploy

# One monthly run: the full DAG for month M (idempotent by month key).
month:
	$(PY)/ingest.py --month $(M)
	$(PY)/zonal.py --month $(M)
	$(PY)/models.py deseason
	$(PY)/models.py nowcast
	$(PY)/publish.py --month $(M)

# Gate 1 (reproducibility): rebuild the entire ledger from raw sources.
# Streaming + resumable; END=YYYY-MM required, START defaults to 2018-01.
rebuild-all:
	$(PY)/backfill.py --start $(or $(START),2018-01) --end $(END)

crosswalk:
	$(PY)/crosswalk.py

frames:          ## annual hero timelapse frames from data/raw/bm/YYYY-01/*A4_radiance.tif
	$(PY)/frames.py

export:          ## web view-models (ledger.json, summary.json, hero.jpg)
	$(PY)/export_web.py

validate:
	$(PY)/models.py validate

brief:
	uv run python -c "from pathlib import Path; import sys; sys.path.insert(0, '../../'); \
	from shared.pipeline.brief import generate; \
	generate(Path('data/stats.json'), Path('web/src/content/brief.md'))"

deploy:
	$(PY)/publish.py --month $(M)
