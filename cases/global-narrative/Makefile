# Case D — Indonesia in the Global Narrative (GDELT open feeds, D20: no BigQuery)
# Spec: docs/spec-global-narrative.html (pending scout report)

PY := uv run python pipeline

.PHONY: backfill refresh validate export deploy

backfill:        ## build the daily ledger 2017→now from GDELT open feeds
	$(PY)/backfill.py

refresh:         ## append the latest days (cron target)
	$(PY)/refresh.py

validate:        ## gates G-D1..G-Dn
	$(PY)/validate.py

export:          ## web view-models
	$(PY)/export_web.py
