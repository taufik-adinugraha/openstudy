# Case G — Transit Access & Urban Equity (static snapshot, D3; rebuilt when GTFS changes)
# Spec: docs/spec-transit-equity.html — DAG stages below map 1:1 to pipeline/*.py

PY := uv run python pipeline

.PHONY: rebuild ingest rail network matrix access equity validate export review article brief web help

help:
	@echo "rebuild    full DAG: ingest → rail → network → matrix → access → equity → validate → export"
	@echo "ingest     TransJakarta GTFS, OSM Java extract (clipped to Jabodetabek), population, jobs proxy, facilities, kelurahan polygons, RWI"
	@echo "rail       build the minimal hand-encoded GTFS for KRL / MRT / LRT from published headways + OSM route geometry"
	@echo "network    r5py TransportNetwork from OSM PBF + GTFS zips (needs JDK 21; ~4-6 GB RAM)"
	@echo "matrix     kelurahan-centroid → destination travel-time matrix, 07:00-09:00 departures, 60/45/30 min cutoffs"
	@echo "access     cumulative-opportunity access per kelurahan (jobs proxy, hospitals, puskesmas)"
	@echo "equity     Lorenz/Gini/Palma of access, access vs wealth (RWI) and vs BPS poverty"
	@echo "validate   gates G-G1..G-G4 → data/stats.json"
	@echo "export     web view-models (kelurahan GeoJSON, matrix slices, curves) into web/public/data"
	@echo "review     eleven adversarial-review tests (thresholds, naive baseline, MAUP, volume, poverty) → web/src/data/review.json"
	@echo "article    the review article's data layer → web/src/data/article.json"

rebuild: ingest rail network matrix access equity validate export review article

ingest:
	$(PY)/ingest.py

rail:            ## data/gtfs/rail_*.zip
	$(PY)/gtfs_rail.py

network:         ## data/network/ (r5 network.dat)
	$(PY)/network.py

matrix:          ## data/matrix.parquet
	$(PY)/matrix.py

access:          ## data/access_adm4.parquet
	$(PY)/access.py

equity:          ## data/equity.json
	$(PY)/equity.py

validate:        ## gates G-G1..G-G4
	$(PY)/validate.py

export:
	$(PY)/export_web.py

review:          ## the eleven review tests → web/src/data/review.json + web/public/data/review_summary.json
	$(PY)/review.py

article:         ## the review article's data layer → web/src/data/article.json
	$(PY)/article.py

brief:           ## same mechanism as Flagship A (shared/pipeline/brief.py; PR merge = review)
	uv run python -c "from pathlib import Path; import sys; sys.path.insert(0, '../../'); \
	from shared.pipeline.brief import generate; \
	generate(Path('data/stats.json'), Path('web/src/content/brief.md'))"

web:
	cd web && npm install && npm run dev
