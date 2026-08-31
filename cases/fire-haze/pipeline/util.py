"""Shared plumbing for Case J — logging, resource guards, resumable download, manifest.

Deliberately small.  The house pattern (cases/forest-watch, cases/transit-equity) is that
every long-running stage is *resumable* and *disk-guarded*: it checks free space before each
chunk, records what it finished in ``data/manifest.json``, and exits 0 rather than dying, so
a systemd transient unit can simply be re-run.  Nothing here is case-specific.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

import config


def log(*a) -> None:
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


# ── resource guards ───────────────────────────────────────────────────────────────────
def free_disk_gb(path: str | Path = "/") -> float:
    return shutil.disk_usage(str(path)).free / 2**30


def disk_ok(need_gb: float = 0.5) -> bool:
    """True when the run may continue.  Every download loop calls this before the next chunk."""
    return free_disk_gb() - need_gb >= config.MIN_FREE_DISK_GB


def guard_disk(need_gb: float = 0.5) -> bool:
    """Log and return False when free disk is under the floor.  Callers exit 0 (resumable)."""
    if disk_ok(need_gb):
        return True
    log(f"DISK GUARD: {free_disk_gb():.1f} GB free, floor {config.MIN_FREE_DISK_GB} GB "
        f"— exiting cleanly, rerun later")
    return False


# ── manifest ledger ───────────────────────────────────────────────────────────────────
def manifest_read() -> dict:
    if config.MANIFEST.exists():
        return json.loads(config.MANIFEST.read_text())
    return {}


def manifest_put(key: str, **kw) -> None:
    """Record one finished artefact.  Presence at the recorded byte count == skip on rerun."""
    m = manifest_read()
    m[key] = {**m.get(key, {}), **kw, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    config.MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    config.MANIFEST.write_text(json.dumps(m, indent=1, sort_keys=True))


def sha256(p: Path, limit: int = 1 << 30) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
            limit -= len(chunk)
            if limit <= 0:
                break
    return h.hexdigest()


# ── resumable fetch ───────────────────────────────────────────────────────────────────
def fetch(url: str, dest: Path, headers: dict | None = None, min_bytes: int = 2000,
          timeout: int = 300, tries: int = 3) -> Path | None:
    """Stream ``url`` to ``dest``, resuming a partial ``.part`` with a Range request.

    Returns the path on success, ``None`` when the source is genuinely absent (recorded as
    absent in the manifest so it is never retried forever) or when the disk guard trips.
    Never loads the body into memory — the ERA5 and land-cover pulls are gigabytes.
    """
    import requests

    if dest.exists() and dest.stat().st_size >= min_bytes:
        return dest
    if not guard_disk():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(tries):
        have = part.stat().st_size if part.exists() else 0
        hdr = dict(headers or {})
        if have:
            hdr["Range"] = f"bytes={have}-"
        try:
            with requests.get(url, headers=hdr, stream=True, timeout=timeout) as r:
                if r.status_code in (403, 404, 410):
                    log(f"absent ({r.status_code}): {url}")
                    return None
                if r.status_code == 416:
                    # the partial is already the whole file (or longer than it): a Range resume
                    # can never recover from this, so drop it and start clean
                    part.unlink(missing_ok=True)
                    raise OSError("416 range not satisfiable — partial discarded, retrying whole")
                r.raise_for_status()
                mode = "ab" if have and r.status_code == 206 else "wb"
                if mode == "wb":
                    have = 0
                with part.open(mode) as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
                        if not disk_ok():
                            log("DISK GUARD mid-download — partial kept, rerun to resume")
                            return None
            if part.stat().st_size < min_bytes:
                raise OSError(f"short file: {part.stat().st_size} bytes")
            part.replace(dest)
            return dest
        except Exception as exc:                      # noqa: BLE001 — resumable by design
            log(f"fetch {dest.name}: {type(exc).__name__} {exc} (attempt {attempt + 1}/{tries})")
            time.sleep(5 * (attempt + 1))
    return None


def require(cond: bool, msg: str) -> None:
    """Hard precondition.  Stages fail loudly rather than writing a plausible empty table."""
    if not cond:
        log("FATAL:", msg)
        sys.exit(1)


def browser_ua() -> dict:
    """Several Indonesian government portals block curl-style user agents (the BPS WAF does);
    any call to one carries a browser UA."""
    return {"User-Agent": os.environ.get("HTTP_UA", config.BROWSER_UA)}


# ── geometry ──────────────────────────────────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km.  Vectorised over any broadcastable mix of arrays/scalars."""
    import numpy as np
    r = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial compass bearing FROM point 1 TO point 2, degrees clockwise from north."""
    import numpy as np
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    y = np.sin(dl) * np.cos(p2)
    x = np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl)
    return (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0


def angdiff_deg(a, b):
    """Smallest absolute difference between two bearings, 0-180."""
    import numpy as np
    d = np.abs((np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0)
    return d


def snap_cell(lat, lon, deg: float = config.GRID_DEG):
    """Snap to the CENTRE of the model cell containing the point.  Returns (clat, clon).

    ERA5 is on a grid whose nodes sit at exact multiples of 0.25 deg, so a "cell" here is the
    box centred on a node.  Floor-based binning would offset the model grid from the wind grid
    by half a cell, which over 72 hours of advection is a real error, not a rounding one.
    """
    import numpy as np
    return (np.round(np.asarray(lat, dtype=float) / deg) * deg,
            np.round(np.asarray(lon, dtype=float) / deg) * deg)


def cell_key(clat, clon, deg: float = config.GRID_DEG):
    """Stable integer id for a model cell centre.  ``ilat * 100000 + ilon``, ilon offset +3600."""
    import numpy as np
    ilat = np.rint(np.asarray(clat, dtype=float) / deg).astype("int64")
    ilon = np.rint(np.asarray(clon, dtype=float) / deg).astype("int64")
    return ilat * 100_000 + (ilon + 3600)


def key_to_cell(key, deg: float = config.GRID_DEG):
    import numpy as np
    k = np.asarray(key, dtype="int64")
    ilat = np.floor_divide(k, 100_000)
    ilon = k - ilat * 100_000 - 3600
    return ilat * deg, ilon * deg


# ── Copernicus data stores (CDS / ADS / EWDS speak one dialect) ───────────────────────
class Cads:
    """Minimal OGC-API-Processes client for the Copernicus data stores.

    ``cdsapi`` blocks on ``retrieve`` and hides the job id, which makes a multi-hour, multi-year
    backfill on a shared box unresumable: kill the process and the queue position is lost.  The
    stores' own REST surface is small enough to speak directly — submit, poll, download — so the
    job id lands in ``data/cads_jobs.json`` and a restart picks up exactly where it left off.

    One ECMWF personal access token authenticates all three hosts.  A 403 whose body says
    "user didn't accept all required site policies" is a ONE-TIME BROWSER CLICK by the account
    owner, not a bad credential — ``policy_blocked`` recognises it so callers can degrade to
    PENDING and carry on rather than dying.  (Anonymous requests get 401, which is how we know
    the token is being read.)
    """

    def __init__(self, host: str = "cds") -> None:
        self.host = host
        self.base = config.CADS_HOSTS[host].rstrip("/")
        self.key = config.CDS_API_KEY

    # -- low level ------------------------------------------------------------------
    def _req(self, method: str, path: str, body: dict | None = None, timeout: int = 120):
        import requests
        url = path if path.startswith("http") else f"{self.base}{path}"
        r = requests.request(method, url, timeout=timeout,
                             headers={"PRIVATE-TOKEN": self.key,
                                      "Content-Type": "application/json"},
                             json=body)
        return r

    @staticmethod
    def policy_blocked(r) -> str | None:
        """Return the missing-policy message when ``r`` is a policy 403, else None."""
        if r.status_code != 403:
            return None
        try:
            j = r.json()
        except Exception:                                   # noqa: BLE001
            return "403 (unparseable body)"
        if "polic" in str(j.get("title", "")).lower():
            return str(j.get("detail") or j.get("title"))
        return None

    # -- jobs -----------------------------------------------------------------------
    def submit(self, dataset: str, request: dict) -> tuple[str | None, str | None]:
        """POST one request.  Returns ``(job_id, blocked_reason)``; exactly one is non-None."""
        r = self._req("POST", f"/retrieve/v1/processes/{dataset}/execution",
                      {"inputs": request})
        blocked = self.policy_blocked(r)
        if blocked:
            log(f"{self.host}/{dataset}: POLICY BLOCKED — {blocked}")
            return None, blocked
        if r.status_code >= 400:
            log(f"{self.host}/{dataset}: HTTP {r.status_code} {r.text[:400]}")
            return None, None
        j = r.json()
        return j.get("jobID") or j.get("jobId"), None

    def status(self, job_id: str) -> str:
        r = self._req("GET", f"/retrieve/v1/jobs/{job_id}")
        if r.status_code == 404:
            return "gone"
        if r.status_code >= 400:
            return "unknown"
        return r.json().get("status", "unknown")

    def result_href(self, job_id: str) -> str | None:
        r = self._req("GET", f"/retrieve/v1/jobs/{job_id}/results")
        if r.status_code >= 400:
            return None
        j = r.json()
        asset = (j.get("asset") or {}).get("value") or {}
        return asset.get("href")

    def download(self, job_id: str, dest: Path) -> Path | None:
        href = self.result_href(job_id)
        if not href:
            return None
        return fetch(href, dest, min_bytes=1000, timeout=1800)

    def delete(self, job_id: str) -> None:
        """Free the slot.  The stores cap concurrent jobs per user, so finished jobs are reaped."""
        try:
            self._req("DELETE", f"/retrieve/v1/jobs/{job_id}", timeout=60)
        except Exception:                                   # noqa: BLE001
            pass


# ── job ledger (survives process restarts; the queue position is the expensive thing) ─
def jobs_read() -> dict:
    if config.JOBS_JSON.exists():
        return json.loads(config.JOBS_JSON.read_text())
    return {}


def jobs_write(d: dict) -> None:
    config.JOBS_JSON.parent.mkdir(parents=True, exist_ok=True)
    config.JOBS_JSON.write_text(json.dumps(d, indent=1, sort_keys=True))


def jobs_put(key: str, **kw) -> None:
    d = jobs_read()
    d[key] = {**d.get(key, {}), **kw}
    jobs_write(d)


def run_store_jobs(host: str, specs: list[dict], reduce_fn, nc_dir: Path,
                   max_inflight: int = 3, max_minutes: float = 240.0,
                   poll_seconds: int = 45) -> dict:
    """Submit / poll / download / reduce a batch of Copernicus jobs on one store.

    ``specs`` is a list of ``{key, dataset, request, dest}``.  ``dest`` is the reduced artefact;
    a spec whose ``dest`` already exists is skipped, which is the whole resumability story: kill
    this process at any point and rerunning picks up from the parts on disk and the job ids in
    ``data/cads_jobs.json``.

    ``reduce_fn(spec, netcdf_paths) -> None`` writes ``dest``.  The payload is deleted only after
    a successful reduce, so a bug in the reducer never costs a re-queue.

    Returns a small status dict, including a ``policy_blocked`` entry naming the exact URL the
    account owner must visit — because "the pipeline stopped" and "someone has to click a
    checkbox" should never look the same in a log.
    """
    import zipfile
    cads = Cads(host)
    nc_dir.mkdir(parents=True, exist_ok=True)
    todo = [s for s in specs if not Path(s["dest"]).exists()]
    if not todo:
        return {"status": "complete", "done": len(specs)}
    log(f"{host}: {len(todo)}/{len(specs)} artefacts outstanding")
    deadline = time.time() + max_minutes * 60
    blocked_reason = None

    def members(p: Path) -> list[Path]:
        if not zipfile.is_zipfile(p):
            return [p]
        out = p.with_suffix("")
        out.mkdir(exist_ok=True)
        with zipfile.ZipFile(p) as z:
            names = [n for n in z.namelist() if n.endswith((".nc", ".grib", ".grb"))]
            for n in names:
                z.extract(n, out)
        return [out / n for n in names]

    while time.time() < deadline:
        jobs = jobs_read()
        inflight = sum(1 for s in todo
                       if jobs.get(s["key"], {}).get("status") in ("accepted", "running"))
        for s in todo:
            if Path(s["dest"]).exists():
                continue
            v = jobs.get(s["key"], {})
            if v.get("status") in ("accepted", "running", "downloaded") or inflight >= max_inflight:
                continue
            # a "failed" submit is usually a full queue or a cost refusal; retrying it every pass
            # produces a hundred identical log lines a minute and no progress
            if (v.get("status") in ("rejected", "failed")
                    and time.time() - v.get("ts", 0) < 180):
                continue
            jid, blocked = cads.submit(s["dataset"], s["request"])
            if blocked:
                blocked_reason = blocked
                break
            if not jid:
                # a submit that returns 2xx without a job id is silent otherwise, and a silent
                # failure in a queue driver is indistinguishable from a slow queue
                log(f"  {s['key']}: submit returned no job id — will retry")
                jobs_put(s["key"], status="failed", job_id=None, ts=time.time())
                continue
            jobs_put(s["key"], job_id=jid, dataset=s["dataset"], status="accepted",
                     ts=time.time())
            log(f"  submitted {s['key']} -> {jid}")
            inflight += 1
            jobs = jobs_read()
        if blocked_reason:
            break

        progressed = False
        for s in todo:
            if Path(s["dest"]).exists():
                continue
            v = jobs_read().get(s["key"], {})
            if not v.get("job_id"):
                continue
            raw = nc_dir / f"{s['key'].replace(':', '_')}.bin"
            if not (raw.exists() and raw.stat().st_size > 5000):
                st = cads.status(v["job_id"])
                if st in ("accepted", "running"):
                    continue
                if st != "successful":
                    # DROP THE JOB ID.  A terminally rejected job never becomes successful, so
                    # re-polling it is pointless — and worse, each poll re-stamped `ts`, which
                    # kept the submit loop's 180 s cooling-off window permanently open and made
                    # the request unresubmittable for the life of the ledger.  That is why the
                    # CEMS backfill sat on three missing years across ~40 passes while the log
                    # said "cooling off".  Clearing the id sends it back to the submit loop.
                    if v.get("status") != "rejected":
                        log(f"  {s['key']}: job {st} — dropping the id and requeueing")
                    jobs_put(s["key"], status="rejected", job_id=None, ts=time.time())
                    continue
                if not guard_disk(2.0):
                    return {"status": "disk_guard"}
                if cads.download(v["job_id"], raw) is None:
                    continue
            try:
                reduce_fn(s, members(raw))
                jobs_put(s["key"], status="reduced")
                raw.unlink(missing_ok=True)
                shutil.rmtree(raw.with_suffix(""), ignore_errors=True)
                cads.delete(v["job_id"])
                progressed = True
                log(f"  {s['key']}: reduced -> {Path(s['dest']).name}")
            except Exception as exc:                        # noqa: BLE001 — resumable by design
                log(f"  {s['key']}: reduce failed {type(exc).__name__} {exc} — payload kept")
                jobs_put(s["key"], status="downloaded")
        left = [s for s in todo if not Path(s["dest"]).exists()]
        if not left:
            return {"status": "complete", "done": len(specs)}
        if not progressed:
            time.sleep(poll_seconds)

    if blocked_reason:
        return {"status": "PENDING_POLICY", "reason": blocked_reason,
                "urls": config.POLICY_URLS.get(host, [])}
    left = [s["key"] for s in specs if not Path(s["dest"]).exists()]
    return {"status": "partial", "outstanding": left[:20], "n_outstanding": len(left)}
