# Case D — Indonesia in the Global Narrative (GDELT open feeds, D20: no BigQuery)
# Spec: docs/spec-global-narrative.html · all stages run on the dev server (see README)

PY := uv run python pipeline

.PHONY: backfill curves events ledger validate export refresh deploy

backfill:        ## clean-server rebuild: API curves + event stream + ledger + gates + views
	$(PY)/doc_api.py all
	$(PY)/events.py all
	$(PY)/validate.py
	$(PY)/export_web.py

curves:          ## DOC-API battery -> data/docapi_curves.parquet (cache-first, ~35 calls)
	$(PY)/doc_api.py all

events:          ## stream the 15-min export zips 2017->now (resumable, newest month first)
	$(PY)/events.py stream

ledger:          ## monthly parquet + denominators + curves -> data/narrative_daily.parquet
	$(PY)/events.py ledger

validate:        ## gates G-D1..G-D4 -> data/stats.json
	$(PY)/validate.py

export:          ## web view-models -> web/public/data + web/src/data/summary.json
	$(PY)/export_web.py

refresh:         ## nightly: re-pull curves, append the current month of events, rebuild
	$(PY)/doc_api.py all
	$(PY)/events.py stream --months $(shell date -u +%Y%m)
	$(PY)/events.py ledger
	$(PY)/validate.py
	$(PY)/export_web.py
