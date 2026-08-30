# Case F — Poverty Mapping from Space (Phase 3)

Village-level welfare estimation from satellite features — building footprints,
night lights, land cover, built-up surface, roads — trained on BPS's official
regency poverty rates (P0/P1/P2, 2016–2025 via WebAPI, verified with the lab
key), spatially cross-validated, then carried down to kecamatan by small-area
estimation benchmarked so the official number is never contradicted.

Status: **SCAFFOLDED — spec written, data path verified 2026-08-30, pipeline
stubs in place, no heavy runs yet.** Spec (governing document):
`docs/spec-poverty-map.html`.

## Non-negotiables from the spec

- **Target is the BPS poverty rate**, never "wealth" or "GDP"; kecamatan values
  are estimates benchmarked to the official regency rate (G-F4 makes this exact).
- **Spatial CV only** for the headline skill number (leave-one-province-out);
  random k-fold is computed solely to show how much it flatters.
- **Gates**: R² ≥ 0.50, Spearman ≥ 0.70, RMSE ≤ 4.0 pp (G-F1, hard); Java vs
  off-Java disclosure (G-F2); 2024/2025 temporal hold-out Spearman ≥ 0.65 (G-F3).
- **Reuse, don't re-download**: Black Marble annual composites come from
  `cases/nightlights-pulse/data/raw/bm/` on the dev server.
- **No CC BY-NC data**: Meta RWI and the SMERU map were scouted and rejected.

## Run (stubs for now)

```sh
uv sync
SCOPE=java make rebuild    # Java fast path (~11 GB raw); default SCOPE=idn (~20 GB)
make validate              # gates G-F1..G-F4
```

`web/` is the case's Astro app (port 4328, base `/poverty`,
`data-case="poverty"` tokens — ochre on soil).

## Decisions pending user verification

1. **Licence election + rejections.** Open Buildings v3 used under its CC BY 4.0
   option (it is dual CC BY 4.0 / ODbL). Meta Relative Wealth Index and the SMERU
   2015 poverty map are **CC BY-NC → excluded**, which removed the planned
   independent wealth cross-check; the temporal hold-out (G-F3) replaces it.
2. **ADM3 topology from HDX COD-AB** (BPS-derived, CC BY-IGO, P-coded), because
   geoBoundaries gbOpen has no ADM3/ADM4 for Indonesia. COD-AB has 522 ADM2 units
   vs the flagship's geoBoundaries 519 — reconciled via P-codes + the pemekaran
   crosswalk before training.
3. **Kemendesa IDM** village index as an optional check once its licence is
   clarified (portal TLS is broken; data.go.id mirror has no licence text).
4. **Annual re-run** (each March BPS release) vs D3's static-snapshot rule.
5. **Embeddings deferred**: Tessera (CC0, ~200 GB for Java) and Major TOM
   (CC BY-SA) are v2 candidates, not for the 16 GB server.
