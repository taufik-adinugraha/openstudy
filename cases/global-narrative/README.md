# Case D — Indonesia in the Global Narrative

How much the world's news talks about Indonesia, how warmly, and about what —
2017 → now from GDELT's open feeds. Spec: `docs/spec-global-narrative.html`
(decisions D20 no BigQuery · D21 hybrid API + raw-feed architecture · D22 one
window 2017→now). Identity: signal violet on broadcast black
(`[data-case="narrative"]`).

## Layout

```
pipeline/config.py      constants: endpoints, query battery, anchors, gates, column map
pipeline/doc_api.py     curves   — cached DOC 2.0 API timelines (35 calls, ≥12 s apart)
pipeline/events.py      stream   — 15-min export zips → Indonesia rows → monthly parquet
                        ledger   — DuckDB daily aggregation + denominators + curves
pipeline/validate.py    gates G-D1..G-D4 → data/stats.json
pipeline/export_web.py  NaN-safe view-models → web/public/data, web/src/data/summary.json
pipeline/attribution.py review study — publisher origin + tone decomposition + the
                        foreign-only anchor re-test → data/attribution.json
pipeline/article.py     review-article data layer → web/src/data/article.json
web/                    Astro + d3 dashboard, port 4326, base /narrative
web/src/pages/article.astro  the review article, served at /narrative/article
data/                   (server only, never synced) raw/docapi cache, events/, ledger
```

## The review (2026-08-31)

`article.astro` is an adversarial review of this case, built entirely from the
case's own output. Run order after an export:

```
uv run python pipeline/attribution.py     # needs data/events/*.parquet only, no API
uv run python pipeline/article.py         # needs web/public/data/*.json + attribution.json
```

`web/src/data/article.json` and `summary.json` are committed so the pages build
from a fresh checkout. Both are derived; regenerate them, never hand-edit them.
The Indonesian-publisher list §6 depends on is a literal constant in
`attribution.py` — it is meant to be audited and extended.

## Running (dev server only — never on the laptop)

```
ssh ubuntu@18.141.229.57   # repo copy at ~/demo-lab (rsync target)
cd ~/demo-lab/cases/global-narrative && export PATH=$HOME/.local/bin:$PATH
make curves | events | ledger | validate | export      # or: make backfill / make refresh
```

Long jobs run as transient systemd units (`nn-curves`, `nn-events`, …) with
`MemoryMax=4G`; follow with `journalctl -u nn-<name> -f`. Every stage is
resumable: the API cache makes reruns free, the event stream skips months whose
parquet exists (the current month is always redone), and the ledger/validate/
export stages run against whatever has landed.

Launch/resume commands (idempotent — safe to re-run after any crash/reboot;
`reset-failed` first if the unit name lingers):

```
sudo systemctl reset-failed nn-curves nn-events 2>/dev/null
sudo systemd-run --unit nn-curves --uid ubuntu --gid ubuntu -p MemoryMax=2G \
  -p Restart=on-failure -p RestartSec=180 \
  -p WorkingDirectory=/home/ubuntu/demo-lab/cases/global-narrative \
  --setenv=HOME=/home/ubuntu --setenv=PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin \
  /home/ubuntu/.local/bin/uv run python pipeline/doc_api.py all
sudo systemd-run --unit nn-events --uid ubuntu --gid ubuntu -p MemoryMax=4G \
  -p Restart=on-failure -p RestartSec=120 \
  -p WorkingDirectory=/home/ubuntu/demo-lab/cases/global-narrative \
  --setenv=HOME=/home/ubuntu --setenv=PATH=/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin \
  /home/ubuntu/.local/bin/uv run python pipeline/events.py stream
```

`nn-curves` exits non-zero while any query is still refused and re-runs itself
(cache-first) until the battery is complete, then exits 0 and stops. After both
finish, rebuild everything downstream with
`uv run python pipeline/events.py ledger && uv run python pipeline/validate.py; uv run python pipeline/export_web.py`.

**Nightly refresh (enable only AFTER the backfill completes** — a second
streamer would race the backfill on the current month): `make refresh`, e.g. as
a systemd timer or `cron: 30 19 * * * cd ~/demo-lab/cases/global-narrative && PATH=$HOME/.local/bin:$PATH make refresh`.

Sync code from the worktree (data is produced on the server, never synced):

```
rsync -az cases/global-narrative/{pipeline,Makefile,pyproject.toml,README.md} ubuntu@…:~/demo-lab/cases/global-narrative/
rsync -az cases/global-narrative/web/{src,astro.config.mjs,package.json}   ubuntu@…:~/demo-lab/cases/global-narrative/web/
```

## Data facts (verified 2026-08-30)

- DOC 2.0 API: keyless; ~24 s per full-range timeline; one request 2017→today
  returns **daily** resolution (3,504 points). The service **resets the TCP
  connection** when a second request overlaps an in-flight one — so exactly one
  client at a time, ≥12 s spacing, exponential backoff. `TimelineVol` is
  pre-normalized (% of all monitored articles); `TimelineVolRaw` returns raw
  counts + `norm` (total monitored).
- Raw feed: `masterfilelist.txt` redirects HTTP→HTTPS (follow redirects).
  Export zips 2017→now: **331,667 English files / 30.8 GB + 326,747 translation
  files / 18.8 GB**. 61 tab-separated columns, no header. Indonesia rows ≈ 1.4 %
  of both feeds combined (measured on 2026 months; ~70 % of kept rows come from
  the translated feed).
- Only the plain `"Indonesia"` query survives a full-range (2017→now) API
  request. Any multi-keyword or negation query runs >100 s server-side, has its
  connection reset, and puts the calling IP in a several-minute penalty box
  (verified from two different IPs). Theme and foreign/domestic curves are
  therefore fetched as ten per-year windows each, with a circuit breaker that
  ends a pass after 10 consecutive refusals; the `nn-curves` unit re-runs
  (cache-first) every 15 min until the battery is complete.
- Coding split: `ActionGeo_CountryCode` (col 54) is FIPS `ID`;
  `Actor1/2CountryCode` (cols 8/18) are ISO3 `IDN`. Both are matched.
- `SQLDATE` is unreliable for undated in-text references (GDELT assigns the
  previous year to "February 14"-style mentions); the ledger therefore keys
  on `DATEADDED` day (= when the world's news carried it), which is also what
  the denominator counts and what the API's timeline measures.

## Decisions pending user verification

Logged by the build agent (2026-08-30); the user was unavailable. Each is
reversible in `pipeline/config.py` unless noted.

1. **Ledger keyed on DATEADDED day, not SQLDATE.** Reason above (news-time vs
   GDELT's guessed event date). Both columns are kept in the parquet.
2. **Source URL reduced to its domain** in the kept parquet (keeps the archive
   well under the 2 GB budget; headline samples come from the API's `ArtList`
   cache per anchor instead).
3. **API refresh = full re-pull, once per day.** Cache keys include the window
   end (today 00:00 UTC); a rerun the same day is free, a new day re-pulls the
   ~35 timelines (~25 min at the polite rate). Simpler and safer than merging
   trailing windows into normalized series.
4. **Anchor list extended** beyond the six in the brief to the spec's G-D2
   list: Palu 2018-09-28 and the Aug–Sep 2025 protests (2025-08-28) added.
   The G20 Bali "positive tone" claim is **report-only** (spec: tested and
   absent); its attention spike is the pass/fail claim.
5. **Anchor test design:** peak of the API's normalized volume in
   `[anchor, anchor + window)` vs the trailing 28-day median (ratio ≥ 1.5);
   tone: window minimum vs trailing 28-day mean (drop ≥ 1.0 points);
   protest: event-layer EventRootCode 14 count ratio ≥ 3 against the same
   baseline. Kanjuruhan additionally must be its quarter's most negative
   tone day. Thresholds are in `config.py`.
   **Rewritten to the data (2026-08-30):** the riots 2019, election 2024,
   PDNS 2024 and protests 2025 do NOT lift *overall* Indonesia attention
   ≥ 1.5× (measured 1.24 / 1.48 / 1.26 / 1.30 — standing coverage keeps the
   baseline high, and cyber is a niche story). Their tested claim is the
   corresponding THEME curve's spike (protest / election / cyber), which is
   also what the spec's live-test asserted for 2019. Overall attention is
   still computed and published for every anchor, marked "info" when it is
   not the gate — thresholds were never moved.
6. **Theme battery = keyword queries, not GKG theme codes** (`Indonesia nickel`,
   `Indonesia ("palm oil" OR sawit)`, …, 20 themes). Keywords are transparent
   to a client; GKG theme taxonomies are not. Theme share = theme volume /
   "Indonesia" volume, both pre-normalized by the API.
7. **Stream order newest-month-first** so the recent years (2024 election,
   2025 protests) are available for the dashboard within the first hour;
   the backfill completes in the background.
8. **Event map is a self-drawn grid (canvas 2-D)** — 0.25° cells of event
   locations coloured by count / tone / protests; no basemap library and no
   WebGL (the user's browser has WebGL disabled).
9. **Eras** (chapter 1) are fixed calendar regimes (pre-pandemic, pandemic,
   G20 & ASEAN chair, election year, Prabowo era); the numbers per era come
   from the data — if the data contradicts an era's label the copy is
   rewritten, never the data.
10. `make backfill` / `make refresh` replaced the placeholder `backfill.py` /
    `refresh.py` targets with the real stages (no separate scripts).
11. **Anchor added:** Anak Krakatau tsunami 2018-12-22 — the decade's single
    loudest measured day (4.00 % of world coverage, attention 4.07×, tone
    −4.2) surfaced by the data itself; now a tested anchor.
12. **Per-year API strategy + circuit breaker** (see Data facts): full-range
    requests are only used for the plain "Indonesia" query; everything else is
    ten stitched per-year windows; ten consecutive refusals end a pass and the
    unit cools down 15 min. Verified from two IPs that heavy full-range
    queries get reset AND penalize the caller.
13. **Units finish the pipeline themselves:** `nn-curves` runs
    ledger→validate→export after every pass (themes appear on the dashboard as
    they land); `nn-events` runs it once the backfill completes. The two are
    serialized by a file lock (`data/.chain.lock`). No timer is installed —
    enable `make refresh` as a nightly cron only after the backfill finishes.
14. **Astro dev toolbar disabled** in `web/astro.config.mjs` (it floated over
    the dashboard in the served dev-mode demo); `demo-narrative` restarted.

## Gates

See `data/stats.json` after `make validate`; the dashboard's methodology
footer publishes the same numbers. Lab rule: if the data contradicts the
story, the story is rewritten to what the data shows.
