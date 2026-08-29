# Demo Lab (working name — final brand pending, decision D8)

Public-data analytics demo site: a landing page plus independently deployable
case demos, each proving end-to-end capability from data acquisition to an
award-level interactive dashboard.

## Governing documents

- Build plan (decisions D1–D11, portfolio, quality gates): https://claude.ai/code/artifact/5363019d-6af0-4643-8cc7-e98f15cd61a7
- Flagship A spec — Nighttime-Lights Pulse: https://claude.ai/code/artifact/06da6f68-9ed2-4a61-b8ce-bac9086856d3
- Flagship B spec — Trade Complexity: https://claude.ai/code/artifact/5f595e20-bfa6-49e5-b400-bf36ff9ab1a7

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
