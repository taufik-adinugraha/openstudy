# Flagship B — Trade & Complexity
# Spec: https://claude.ai/code/artifact/5f595e20-bfa6-49e5-b400-bf36ff9ab1a7

PY := uv run python pipeline

.PHONY: rebuild pulse validate brief deploy

# Gate 1 (reproducibility): full rebuild from raw BACI on a clean machine.
rebuild:
	$(PY)/ingest.py --release $(RELEASE)
	$(PY)/complexity.py compute
	$(PY)/complexity.py layout
	$(PY)/extracts.py extracts
	$(PY)/extracts.py publish

# Quarterly: refresh only the Comtrade/BPS latest-year pulse panel.
pulse:
	$(PY)/extracts.py pulse

validate:
	$(PY)/complexity.py validate

brief:
	uv run python -c "from pathlib import Path; import sys; sys.path.insert(0, '../../'); \
	from shared.pipeline.brief import generate; \
	generate(Path('data/stats.json'), Path('web/src/content/brief.md'))"

deploy:
	$(PY)/extracts.py publish
