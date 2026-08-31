# Phase 2 — Jakarta Is Sinking (static snapshot case, decision D3)
# Spec: docs/spec-jakarta-sinking.html (C3 pipeline, C4 gates)
# Runs on the server (~/demo-lab/cases/jakarta-sinking); `uv sync` first.

PY := uv run python pipeline

.PHONY: rebuild ingest grid fuse export review article validate insar

ingest:            ## ~300 MB of open inputs; idempotent (each source independent)
	$(PY)/ingest.py

grid:              ## deposited field → 0.001° raster + gates G-C1/G-C2 → data/derived/stats.json
	$(PY)/grid.py

fuse:              ## kelurahan exposure + the clock + gates G-C4/G-C5
	$(PY)/fuse.py

export:            ## web view-models → web/public/data, web/src/data/summary.json, hero.jpg
	$(PY)/export_web.py

review:            ## the eight adversarial-review tests → data/derived/review.json
	$(PY)/review.py

article:           ## review.json + published outputs → web/src/data/article.json
	$(PY)/article.py

validate: grid fuse   ## re-evaluate every gate (G-C3 deferred, see `insar`)

rebuild: ingest grid fuse export review article

insar:             ## gate G-C3 — own LiCSBAS run on LiCSAR 098A (13 GB); DEFERRED, not started
	@echo "G-C3 (LiCSBAS 098A_09673_121312, 2017–2024) is deferred — see README 'Deferred'."
