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

| Gate | Test | Threshold | Result |
|---|---|---|---|
| G-B5 | BACI IDN export total 2023 vs UN Comtrade US$259.5B (BPS US$258.82B reported alongside) | ±5% | **FAIL** — BACI US$305.04B, **+17.55%** vs Comtrade, +17.86% vs BPS |
| G-B6 | BACI IDN import total 2023 vs BPS US$221.89B | ±10%, negative miss expected — BPS is CIF, BACI is FOB | **PASS** — BACI US$219.96B, −0.87% |
| G-B7 | Nickel capital-goods test, base 2013–15 vs peak 2021–24: **H1** HS84+85 imports +≥50%; **H2** that growth beats total import growth by ≥10 points; **H3** ≥1 smelter input (coke 2704, electrodes 8545, chrome ore 2610, scrap 7204, coal 2701, limestone 2521/2522) +≥100% | PASS = all three; PARTIAL = two; FAIL = one or none | **FAIL** — H1 ✗ (+29.4%), H2 ✗ (+2.3 pts over a +27.1% total), H3 ✓ |

### What the gates found

**G-B5 fails and the failure is the finding.** BACI puts Indonesia's goods exports
about a sixth above Indonesia's own published figure, in every year BPS
publishes: +19.0% (2022), +17.9% (2023), +14.3% (2024). It is not an artefact of
this extension — our 2024 total (302,633,802 kUSD) reproduces the case's existing
`extracts.py` figure (302,633,799) to nine significant figures, it is spread
across chapters rather than one product, and no aggregate/"nes" partner code is
inflating it. The leading explanation is that BACI is a mirror-reconciled
reconstruction and Indonesia's partners consistently report receiving more than
Indonesia reports shipping. **This discrepancy already existed in the case** —
the hero quoted Comtrade's US$259.5B while the footer quoted BACI's US$303B — it
was simply never stated. It is now, on the page, in red.

Complexity chapters 01–06 are largely insulated because RCA is a ratio of
*shares*, and a roughly proportional level difference cancels.

**G-B7 fails, and the reason it fails is the most interesting result here.**
Capital-goods imports (HS 84+85) grew +29.4% base→peak against +27.1% for all
imports — the smelter build-out is essentially invisible in the machinery bill.
But the *feedstock* exploded: chromium ore +12,348%, coal +1,325%, coke +994%,
quicklime +335%, limestone +236%. Indonesia — the world's largest coal exporter —
raised coal imports fourteen-fold to ~US$3.2B/yr. So downstreaming shows up not
as one-off equipment purchases but as a **permanent new import dependency** of
roughly US$3.7B/yr against ~US$28.5B/yr of extra processed-nickel exports: about
an eighth of the headline gain is spent on imported inputs.

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
  **This prior was falsified and the page says so.** Coal and limestone — the
  two controls — moved the most (+1,325% and +236%), while ferrous scrap, which
  we expected to rise, fell 23%. The probable reason is geographic and
  metallurgical (smelters in Sulawesi, far from the Kalimantan/Sumatra basins;
  Indonesian coal is largely thermal rather than metallurgical grade), but that
  is an inference, not a measurement, and is labelled as one on the page.
  Nothing was re-specified after the fact: the gate still reports FAIL under its
  original thresholds.

- **D-P6 — The chapter 07 thesis was rewritten after the data contradicted it.**
  The brief anticipated a story about deepening dependence on China. The data
  says the export customer base *diversified*: HHI fell from 1,097 (1995) to 588
  (2015) before climbing back to 844 (2024), and Japan's 26.0% share in 1995 was
  larger than China's 22.5% today. The chapter now argues the true shape — a U:
  two decades of diversification, one decade of re-concentration driven entirely
  by China — rather than the shape we expected. The *import* side is where
  concentration genuinely ran one way: HHI 846 → 1,307, with China at 32.4% of
  imports against 22.5% of exports.

- **D-P7 — G-B5's failure was published rather than accommodated.** The
  tolerance was not widened and the benchmark was not swapped. The only thing
  added after seeing the result was the BPS series for 2022 and 2024, so the
  deviation could be shown as a systematic pattern rather than a single miss;
  the ±5% threshold is untouched. **This needs an owner decision**: either accept
  that the case reports BACI levels with a stated ~17% offset from national
  figures, or rebase all level statements onto Comtrade/BPS and keep BACI for
  shares and structure only.

- **D-P8 — Pre-existing issue, deliberately not fixed.** At 390px the fixed
  `.stamp-bar` wraps to two lines (~82px tall) and overlays body copy — on the
  original chapters 03 and 04 exactly as on the new ones. It is page chrome
  shared with the existing design, so it was left alone rather than restyled
  unilaterally. A one-line fix (hide the secondary stamp below ~560px, or give
  the bar a solid background) is available if wanted.
