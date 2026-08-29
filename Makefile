# Root orchestrator — each case remains independently runnable from its own dir.

.PHONY: setup nightlights trade trade-pulse site validate help

help:
	@echo "setup                  create uv envs for both case pipelines"
	@echo "nightlights M=YYYY-MM  run flagship A pipeline for one month"
	@echo "trade RELEASE=202601   full flagship B rebuild from BACI"
	@echo "trade-pulse            quarterly Comtrade/BPS latest-year refresh"
	@echo "site                   landing page dev server"
	@echo "validate               run all validation gates (G-A*, G-B*)"

setup:
	cd cases/nightlights-pulse && uv sync
	cd cases/trade-complexity && uv sync

nightlights:
	$(MAKE) -C cases/nightlights-pulse month M=$(M)

trade:
	$(MAKE) -C cases/trade-complexity rebuild RELEASE=$(RELEASE)

trade-pulse:
	$(MAKE) -C cases/trade-complexity pulse

site:
	cd site && npm install && npm run dev

validate:
	$(MAKE) -C cases/nightlights-pulse validate
	$(MAKE) -C cases/trade-complexity validate
