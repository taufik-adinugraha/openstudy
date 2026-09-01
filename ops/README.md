# ops — running pipelines here, publishing data there

The server serves static files. Pipelines run on your laptop. Nothing on the server
fetches or computes any more; it rebuilds pages only when new data arrives.

Why the split: pipelines need 13 GB of raw parquet and up to ~3.1 GB of memory each,
while the site needs 43 MB of JSON and none of the raw data. Keeping the two apart
means the server can be small, and a pipeline that misbehaves cannot take the demo
down with it.

## The loop

```sh
ops/pipeline run --plan air-quality fire-haze     # what would run, in what order
ops/pipeline run air-quality fire-haze            # one after another, never together
ops/data status air-quality fire-haze             # what the server would receive
ops/data push  air-quality fire-haze --yes        # send it, and rebuild what reads it
```

That last step matters more than it looks. Every page imports its JSON **at build
time**, so landing a file next to the source changes nothing a visitor can see. A push
therefore rebuilds and republishes the affected apps on the server, and reports it. A
push that said "success" while the site kept showing yesterday would be the worst
possible outcome, so `--no-rebuild` exists but says plainly what it is skipping.

`run` ends by printing the two `ops/data` commands for the cases that succeeded — a
case that failed is never offered for publishing, because half-written output is
worse than yesterday's.

## ops/pipeline

One pipeline at a time, enforced by a lock directory rather than a convention. A
second `start` is refused and names what is running; `--wait` queues behind it.

| command | |
|---|---|
| `start <name> [--wait] [--fg] [--mem 3G] -- <cmd...>` | run one thing |
| `run [--plan] [--keep-going] <case...>` | each case's own `make` target, in order |
| `status` · `history [n]` · `logs <name> [-f]` · `stop [name]` | |

Targets are detected per case, not assumed: `refresh` where the Makefile has one,
then `rebuild`, `rebuild-all`, `all`. All ten cases resolve today. Every target is
resolved before the first one runs, so a missing target is found in a second rather
than an hour into the batch.

Two backends. On Linux with systemd it uses a transient unit with a real cgroup
memory cap and systemd's own accounting. On macOS, which has neither cgroups nor
`flock`, it runs under `/usr/bin/time -l` and cannot cap memory — so it measures the
peak instead, which is the number a cap should come from anyway.

Every run appends to `ops/state/runs.tsv`: start, end, exit code, peak MB, cap MB.
That ledger is the point. Three pipelines were OOM-killed on the old server-side
setup, and every kill in the syslog read `constraint=CONSTRAINT_MEMCG` with
`oom_memcg` pointing at the unit itself — ~10 GB free while the unit died at a
`MemoryMax` typed in by hand. Two of them died at 3,129 MB and 3,128 MB, one MB
apart, which is a ceiling and not a leak. So a cap is now proposed from a measured
peak plus 25%, and a run whose known peak exceeds what the machine can spare says so
before starting rather than hours later.

The server carries `server` in `ops/state/ROLE` and refuses `start` outright.

## ops/data

Only two directories per case ever cross, and no source code:

- `web/src/data/` — imported at build time (`article.json` and friends)
- `web/public/data/` — served as files and fetched by the page

| command | |
|---|---|
| `status [case...]` | per-file sha256 comparison both ways |
| `push [case...] [--yes]` | plan unless `--yes`; backs up, transfers, verifies |
| `pull [case...]` | the server's JSON (43 MB) |
| `pull --raw [case...]` | the raw pipeline inputs (13 GB) |
| `restore <timestamp>` | put a backed-up set back |

`push` copies what it is about to overwrite into `ops/backups/<timestamp>` on the
server first, then verifies every file by sha256 after transfer — rsync exiting 0 is
not proof, and a partial write would otherwise be found by a visitor. `pull` backs up
what it replaces here, because this tree is not under version control and there is no
other undo.

`rsync --inplace` is not optional anywhere in here. Without it rsync writes a temp
file and renames, Astro's dev watcher fires on a path that no longer exists, throws
ENOENT, and serves an error overlay where the page should be.

## ops/serve-static

Twelve Astro dev servers cost 4,579 MB between them — a file watcher, a Vite graph and
a module cache each, for pages that are already fully static (`output: "static"`, no
adapter, every route prerendered). One nginx serving the built output costs **17 MB**.
That is the difference between needing a 16 GB box and a 4 GB one.

| command | |
|---|---|
| `manifest` | derive port, base path and directory for every app |
| `build [app...]` | `astro build`, one app at a time |
| `publish [app...]` | copy built output into the served tree |
| `install` | nginx, the generated config, and the first publish |
| `cutover` | stop the dev servers, start nginx |
| `verify` | fetch every port and base, and check the titles |
| `rollback` | dev servers back, nginx off |

Ports and base paths are preserved exactly — one nginx `server` block per original
port. A cheaper server that breaks every link people have already been sent is not
cheaper.

The built output is **copied** to `/srv/demo/<port>/<base>`, not symlinked. `/home/ubuntu`
is `drwxr-x---`, so nginx cannot traverse into a dist that lives there, and opening the
home directory to every local user is a worse trade than copying 54 MB. The copy also
means a rebuild cannot half-serve: the live site changes when the new output is
complete, not while Vite is still writing it.

`verify` checks the page title, not just the status code — Astro serves its own compile
errors as a 200 with an error page, so "it responded" is not "it works".

Rebuilding needs `node_modules` on the server, which is why they are still there. If
the box gets smaller than that is comfortable, build on the laptop and extend
`ops/data push` to ship `dist` instead of rebuilding remotely.

## ops/tls — domain, https, and a record that follows the IP

Run on the server. No Elastic IP is held, so the address changes on every stop and
start; a dynamic-DNS record is what makes that a non-event rather than a re-share.

```sh
ops/tls ddns fmv-demo <duckdns-token>   # free subdomain + a timer that keeps it current
ops/tls cert                            # certificate, then https on
ops/tls status                           # domain, record, expiry, both timers
```

`ddns` writes the token to `/etc/duckdns.env` at 0600, root-owned, and never passes it
in an argument list where `ps` would show it. A systemd timer updates the record 45s
after boot — the moment the address has just changed — and every five minutes after.

`cert` uses `certbot certonly --webroot`, **never the nginx plugin**: `ops/serve-static`
rewrites the site config and would erase anything the plugin added. The generator emits
the TLS block itself from the standard certificate paths, so regenerating the config is
safe and repeatable. Before asking Let's Encrypt for anything, `cert` writes a probe
file and fetches it over the public name — a failure there is a DNS or firewall problem,
and finding that out in one second beats finding it out in a rate-limited failure.

Ports: 80 and 443 only. 80 stays open because the ACME challenge needs it and because
everything on it redirects to https once a certificate exists.

## Slugs and one origin

Everything is served from a single origin, one slug per case — `/nightlights`,
`/forest`, `/haze` — which is what each app's Astro `base` already was. That is what
makes https possible: a certificate cannot be issued for a bare IP, and a page served
over https cannot call an API over http on another port.

So Provenance's API is proxied at `/knowledge/api`, same origin, and the pages address it
relatively. Nothing reaches for port 4330 any more, which is what made closing every
port except 22, 80 and 443 safe.

## Environment

`DEMO_HOST`, `DEMO_KEY`, `DEMO_REMOTE_ROOT` override the server coordinates.
`PIPELINE_CPU` (default `200%`) and `--mem` bound a run on the systemd backend.
