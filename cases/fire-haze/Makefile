# Case J — Fire & Haze Early Warning (static snapshot + optional daily refresh)
# Spec: docs/spec-fire-haze.html — DAG stages below map 1:1 to pipeline/*.py
#
# ingest_era5 is the long pole, and the reason is measured: CDS refuses multi-year
# requests above ~16,368 fields (the cost is computed BEFORE the area subset, so a
# small box buys nothing) and caps queued requests per dataset at about two.  The
# backfill is therefore a 45-job serial queue, not a parallel pull.  Job ids live in
# data/cads_jobs.json so a restart never loses a queue position, and every downstream
# stage builds from WHATEVER YEARS HAVE LANDED — so a partial drain produces a shorter
# record rather than a failure.  pipeline/finish.sh drives the rest once it goes quiet.

PY := uv run python pipeline

.PHONY: rebuild fires era5 era5-poll cams fwi static indices ground features risk transport \
        validate export refresh brief web help

help:
	@echo "rebuild     full DAG: fires -> static -> indices -> era5 -> cams -> ground"
	@echo "            -> features -> risk -> transport -> validate -> export"
	@echo "fires       FIRMS bulk country CSVs (history) + area API (tail), static-mask filtered"
	@echo "static      peat depth, land cover, primary forest, boundaries (reuses Case H layers)"
	@echo "indices     ONI/DMI/SOI, CHIRPS + CHIRPS-GEFS forecasts, SPI, and the CEMS FWI+KBDI"
	@echo "            set from EWDS — the external baseline the risk model has to beat"
	@echo "era5        ERA5 single + pressure levels + ERA5-Land soil moisture (SHARDED)"
	@echo "cams        CAMS forecasts (CTM benchmark), EAC4 (surrogate truth), GFAS (plume height)"
	@echo "ground      PM2.5 receptors, three tiers — see pipeline/ingest_ground.py"
	@echo "features    daily 0.25 deg cell panel: weather x drought x fuel x ENSO, strictly lagged"
	@echo "risk        ignition-probability model, blocked-by-season CV, scored vs FWI"
	@echo "transport   forward/back trajectories on ERA5 winds, GFAS injection heights"
	@echo "validate    gates G-J1..G-J5 -> data/stats.json"
	@echo "export      NaN-safe web view-models into web/public/data"
	@echo "refresh     daily: NRT hotspots -> risk -> transport -> export"
	@echo ""
	@echo "era5-poll   drain the CDS queue without resubmitting (safe to run repeatedly)"
	@echo "fwi         drain the EWDS queue only — the CEMS baseline for gate G-J2"
	@echo ""
	@echo "NOTE: CDS/ADS/EWDS all authenticate with the one ECMWF token in .env.  ADS and EWDS"
	@echo "      each needed a ONE-TIME browser policy acceptance; both were accepted 2026-08-30"
	@echo "      and verified by live submission.  A 403 whose body mentions site policies is"
	@echo "      that click, not a dead key — every stage degrades to a named PENDING."

rebuild: fires static indices era5 cams ground features risk transport validate export

fires:           ## data/fires.parquet, data/fires_removed.parquet (the G-J1 audit trail)
	$(PY)/ingest_fires.py

static:          ## data/peat.tif, data/landcover.tif, data/adm.parquet
	$(PY)/ingest_static.py

indices:         ## data/enso.parquet, data/chirps.parquet, data/drought.parquet
	$(PY)/ingest_indices.py

era5:            ## data/era5_parts/{sl,tp}_YYYY.parquet + pl_YYYY.npz — submits and drains
	$(PY)/ingest_era5.py

era5-poll:       ## drain the CDS queue without submitting anything new
	$(PY)/ingest_era5.py --poll

fwi:             ## data/fwi_parts/*.parquet — the CEMS baseline gate G-J2 scores against
	$(PY)/ingest_indices.py --fwi-only

cams:            ## data/cams_forecast.parquet, data/cams_eac4.parquet, data/gfas.parquet
	$(PY)/ingest_cams.py

ground:          ## data/ground.parquet, data/ground_meta.json
	$(PY)/ingest_ground.py

features:        ## data/panel.parquet
	$(PY)/features.py

risk:            ## data/risk.parquet, data/risk_meta.json
	$(PY)/risk.py

transport:       ## data/trajectories.parquet, data/receptor_exposure.parquet
	$(PY)/transport.py

validate:        ## gates G-J1..G-J5
	$(PY)/validate.py

export:
	$(PY)/export_web.py

refresh:         ## cron target (daily); idempotent by acquisition date
	$(PY)/ingest_fires.py --nrt-only && $(PY)/features.py && $(PY)/risk.py --predict-only \
	  && $(PY)/transport.py && $(PY)/export_web.py

brief:           ## same mechanism as Flagship A (shared/pipeline/brief.py; PR merge = review)
	uv run python -c "from pathlib import Path; import sys; sys.path.insert(0, '../../'); \
	from shared.pipeline.brief import generate; \
	generate(Path('data/stats.json'), Path('web/src/content/brief.md'))"

web:
	cd web && npm install && npm run dev
