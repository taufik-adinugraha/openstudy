# Demo Lab (working name — final brand pending, decision D8)

Public-data analytics demo site: a landing page plus independently deployable
case demos, each proving end-to-end capability from data acquisition to an
award-level interactive dashboard.

## Governing documents

Source HTML lives in `docs/` (the durable copy); published views:

- Build plan (decisions D1–D16, portfolio, quality gates, validation results): https://claude.ai/code/artifact/db923b4e-94e8-4b0b-a1e2-504a8bd309e8
- Flagship A spec — Nighttime-Lights Pulse: https://claude.ai/code/artifact/becd92dc-5dfe-4ba8-9627-56a00a470ce1
- Flagship B spec — Trade Complexity: https://claude.ai/code/artifact/6f1db914-d986-48a3-8708-b15535c927a0
- Platform spec — Provenance knowledge assistant: https://claude.ai/code/artifact/6b8a2784-27c9-48cf-ac8b-d9faa14ecd4c
- Case C spec — Jakarta Is Sinking: https://claude.ai/code/artifact/648a23f4-8bfe-4e47-a8dc-25859e9963a5

## Architecture rule

One spec, one package, one deployment per case. The only shared code is the
landing page, design tokens/components, and common pipeline helpers.
A broken case never blocks another.

```
shared/            design tokens + common pipeline helpers (storage, briefs)
site/              landing page (Astro, static)
cases/
  nightlights-pulse/   Flagship A — monthly cron
    pipeline/  data/  web/  spec/
  trade-complexity/    Flagship B — annual rebuild + quarterly pulse
    pipeline/  data/  web/  spec/
.github/workflows/ scheduled pipeline runs
```

## Conventions

- Pipelines: Python 3.12, `uv` for env management, DuckDB + parquet as the
  data layer, one `Makefile` per case with `rebuild-all | validate | deploy`.
- Frontends: Astro static builds, MapLibre GL + deck.gl for maps, custom d3
  for signature charts. Dark-first tokens from `shared/design/tokens.css`.
- Every derived dataset carries a data-vintage stamp; every generated insight
  brief ships via pull request (merge = human review).
- Secrets only via environment (`.env` locally, repo secrets in CI) — see
  `.env.example`. Never commit data or credentials; `data/` is git-ignored.

## Quality gates

No case ships unless all nine gates in the build plan §7 pass, including the
validation gates in its own spec (G-A1…4 / G-B1…4).

## Quickstart

```sh
make setup            # create uv envs for both case pipelines
make nightlights M=2026-07   # run flagship A for one month (stubs for now)
make trade RELEASE=202601    # rebuild flagship B (stubs for now)
make site             # run the landing page dev server
```
