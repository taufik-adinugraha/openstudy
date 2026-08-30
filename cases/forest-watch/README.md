# Case H — Forest & Commodity Watch (Phase 3)

Where Global Forest Watch stops: weekly RADD radar alerts (national, ~1 GB via
the GFW Data API) clustered into disturbance events and linked to what the land
becomes — Descals oil-palm extent + planting year, Universal Mill List
catchments, peat and primary-forest flags — with deep dives in Riau,
Kalimantan Tengah and Papua. All stored linkage layers are CC BY 4.0.

Status: **SCAFFOLDED — spec written, data path verified 2026-08-30 (GFW API
queried live, per-tile sizes HEAD-checked), pipeline stubs in place, no heavy
runs yet.** Spec (governing document): `docs/spec-forest-watch.html`.

## Non-negotiables from the spec

- **Concessions are overlay-only.** No commercially-licensed Indonesian
  concession vector exists (GFW's are "CC BY 4.0 excluding Indonesia,
  view-only"; ministry services carry no licence text). Government ArcGIS/WMS
  services render as a live, labelled reference overlay — never stored, joined
  or redistributed. The stored linkage is palm + mills + peat/primary.
- **Gates**: Hansen reconciliation vs GFW query API ±5 % per province × year
  (G-H1, hard; national anchors 2023 = 1,395,285 ha / 2024 = 1,120,264 ha);
  RADD reconciliation ±10 % (G-H2, hard); ≥ 60 % GLAD-L agreement on big
  high-confidence clusters (G-H3); Riau linked-share ≥ 25 % literature floor
  (G-H4 — diagnosed, never tuned to pass).
- **The KLHK divergence is shown, not hidden** — 2024: GFW 1.12 Mha tree-cover
  loss vs KLHK 175.4k ha net deforestation; different definitions, both true,
  explained in plain words.
- **Every number carries its RADD version string** (e.g. v20260823).

## Run (stubs for now; needs a free GFW API key in `.env` as `GFW_API_KEY`)

```sh
uv sync
make rebuild               # ingest → alerts → link → chips → validate → export
make refresh               # weekly: latest RADD version → alerts → link → export
```

`web/` is the case's Astro app (port 4331 — 4330 is the Pustaka API — base
`/forest`, `data-case="forest"` tokens — canopy green against burnt umber).

## Decisions pending user verification

1. **The concession-overlay compromise** (view-time government services,
   nothing stored) — the biggest editorial call in this case; alternative is
   dropping concession context entirely.
2. **Naming mills/groups** from the Universal Mill List (CC BY 4.0, fields are
   published) on a public page — or aggregating to group level for a Jakarta
   consultancy's comfort.
3. **New registration + `.env` key**: free GFW API key (expires yearly, like
   the Earthdata token — a calendar item). `GFW_API_KEY` should be added to
   the root `.env.example` (not touched from this worktree).
4. **Weekly alert refresh cron** vs D3's static-snapshot rule for non-flagships.
5. **Papua** = pre-2022 province boundary (COD-AB 2020 vintage, 34 provinces);
   the 2022 split into four provinces is noted on the page.
6. **Rejected sources on licence**: Nusantara Atlas (non-commercial), GeoRSPO
   (no open licence, Indonesia excluded), Planetary Computer S1-RTC (account
   required).
