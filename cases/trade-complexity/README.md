# Flagship B — Indonesia in the Global Trade Network

BACI bilateral flows -> Indonesia's product space, complexity trajectory, and
the nickel-downstreaming paradox. Full spec (governing document):
https://claude.ai/code/artifact/5f595e20-bfa6-49e5-b400-bf36ff9ab1a7

## Non-negotiables from the spec

- **Goods only** — BACI has no services trade; stated verbatim on the
  methodology page.
- **Two data planes, never mixed**: BACI (through 2024) powers all complexity
  math; the Comtrade/BPS "pulse" panel is display-only for the latest year.
- **PCI sign normalization** is an explicit pipeline step with a unit test
  (py-ecomplexity issue #1).
- **G-B2 epistemics**: if our numbers don't reproduce the decade rank decline,
  the story is rewritten to match our numbers — never the reverse.
- **Layout is precomputed** (seeded); the browser never runs a force simulation.

## Run

```sh
uv sync
make rebuild RELEASE=202601   # full rebuild from raw BACI
make pulse                    # quarterly latest-year refresh
make validate                 # gates G-B1..G-B4
make partners                 # partner + import extension (G-B5..G-B7)
```

`web/` is the case's Astro app (dark-first, `data-case="trade"` tokens).

## Partners & imports extension

Chapters 07–10 close the half of the network the case originally ignored:
partner concentration, import dependence, bilateral balances, and a test of
the nickel story from the import side.

**Why the complexity chapters stay export-only.** ECI/PCI and the product
space are *defined* on exports through revealed comparative advantage — a
country's complexity is a statement about what it can sell, so imports cannot
enter that math. Chapters 01–06 are unchanged and export-based by
construction, and the page now says so where a reader would otherwise wonder.

**Data path.** `pipeline/fetch_baci.sh` re-downloads the 2.42 GB CEPII zip with
byte-exact resume (CEPII drops connections constantly). `pipeline/partners.py
slice` then streams it one year at a time, keeping only rows where Indonesia is
exporter or importer — the full 330M-row DuckDB is never rebuilt, and retained
data stays around 100 MB. Every year is skippable, so the stage resumes after a
kill.

### Pre-registered gates

Thresholds were fixed in `pipeline/config.py` **before any partner or import
number was computed**, and failures publish red on the page rather than being
quietly dropped.

| Gate | Test | Threshold |
|---|---|---|
| G-B5 | BACI IDN export total 2023 vs UN Comtrade US$259.5B (BPS US$258.82B reported alongside) | ±5% |
| G-B6 | BACI IDN import total 2023 vs BPS US$221.89B | ±10%, negative miss expected — BPS is CIF, BACI is FOB |
| G-B7 | Nickel capital-goods test, base 2013–15 vs peak 2021–24: **H1** HS84+85 imports +≥50%; **H2** that growth beats total import growth by ≥10 points; **H3** ≥1 smelter input (coke 2704, electrodes 8545, chrome ore 2610, scrap 7204, coal 2701, limestone 2521/2522) +≥100% | PASS = all three; PARTIAL = two; FAIL = one or none |

## Decisions pending user verification

*(logged autonomously — the owner was unavailable; each needs a yes/no)*

- **D-P1 — Chapters appended after the 3-D explorer, not before it.** The brief
  said "appended, do not disturb existing ones", so 07–10 sit between Explore
  (06) and the methodology footer rather than being interleaved with the
  export chapters they answer. Narratively they would land harder as 04–07,
  immediately after the nickel gambit, which would mean renumbering the
  existing eyebrows. Renumbering is a one-line-per-chapter change if wanted.
- **D-P2 — Import totals are reconciled against BPS, not Comtrade.** The
  project's verified export benchmark is UN Comtrade (US$259.5B); no equally
  verified Comtrade import figure existed in the repo, so G-B6 uses the BPS
  published total. BPS values imports CIF while BACI harmonizes everything to
  FOB, so a negative deviation is the expected, correct result and is reported
  as such rather than treated as an error.
- **D-P3 — "Partner" means BACI reporting territory.** Hong Kong, Macao and
  Taiwan ("Other Asia, nes") are separate partners, not folded into China.
  Folding them in would raise the measured China share materially; the page
  states the convention and shows the folded figure as a footnote so the
  reader can pick.
- **D-P4 — Concentration is reported as both HHI and top-5 share.** HHI is the
  standard measure but is unintuitive; the top-5 share is legible but coarse.
  The chapter leads on the top-5 share and carries HHI as the second axis.
- **D-P5 — The smelter-input basket for G-B7 was chosen on prior reasoning,
  not by searching for series that moved.** Indonesia already has domestic
  thermal coal and limestone, so the informative tells are coke, graphite
  electrodes, chrome ore and stainless scrap. Coal and limestone are still
  reported, as controls that should *not* move.
