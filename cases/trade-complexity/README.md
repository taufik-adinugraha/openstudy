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
```

`web/` is the case's Astro app (dark-first, `data-case="trade"` tokens).
