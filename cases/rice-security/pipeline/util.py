"""Shared plumbing for Case I — logging, resource guards, resumable download, manifest.

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
    Never loads the body into memory — the SAR scenes are gigabytes.
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
    """BPS's WAF blocks curl-style user agents; every BPS call carries a browser UA."""
    return {"User-Agent": os.environ.get("HTTP_UA", config.BROWSER_UA)}


# ── Earthdata Login ───────────────────────────────────────────────────────────────────
def edl_session():
    """A ``requests.Session`` that keeps ``Authorization`` across a cross-host redirect.

    ``requests`` strips the header when the host changes, which is correct in general and wrong
    for Earthdata: every DAAC 30x-redirects the object request to its own egress host.  Without
    this the real failure (403 EULA, or 200 data) is masked as ``401 HTTP Basic: Access denied``,
    which reads like a bad token and sends you off to regenerate a token that was fine.
    """
    import requests

    class _Keep(requests.Session):
        def rebuild_auth(self, prepared_request, response):   # noqa: D102 — intentional no-op
            return

    s = _Keep()
    if config.EARTHDATA_TOKEN:
        s.headers["Authorization"] = f"Bearer {config.EARTHDATA_TOKEN}"
    return s


def is_geotiff(path: Path) -> bool:
    """Magic-byte check.  A 302-to-login writes an HTML page into a ``.tif`` with HTTP 200."""
    with path.open("rb") as fh:
        head = fh.read(4)
    return head in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")   # classic + BigTIFF


# ── Microsoft Planetary Computer ──────────────────────────────────────────────────────
_SAS: dict[str, tuple[str, float]] = {}


def mpc_sas(collection: str = None, force: bool = False) -> str:
    """A read SAS token for an MPC blob container, cached until shortly before it expires.

    Anonymous — the endpoint issues a working read token without a subscription key.  Tokens
    live about an hour, so anything long-running has to refresh mid-flight rather than opening
    one dataset per token and hoping.
    """
    import requests

    collection = collection or config.MPC_RTC_COLLECTION
    hit = _SAS.get(collection)
    if hit and not force and time.time() < hit[1]:
        return hit[0]
    url = config.MPC_SAS.format(collection=collection)
    for attempt in range(5):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            tok = r.json()["token"]
            _SAS[collection] = (tok, time.time() + config.MPC_SAS_TTL_S)
            return tok
        except Exception as exc:                            # noqa: BLE001
            log(f"mpc_sas: {type(exc).__name__} {exc} (attempt {attempt + 1}/5)")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("could not obtain an MPC SAS token")


def gdal_env():
    """GDAL settings for reading remote COGs: no directory listing, big VSI cache, HTTP/2."""
    import rasterio

    return rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_USE_HEAD="NO",
        VSI_CACHE="TRUE",
        VSI_CACHE_SIZE="67108864",
        GDAL_HTTP_MULTIPLEX="YES",
        GDAL_HTTP_VERSION="2",
        GDAL_HTTP_MAX_RETRY="4",
        GDAL_HTTP_RETRY_DELAY="3",
        GDAL_NUM_THREADS="2",
    )


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone containing a point.  Java is 48S and 49S, split at 108 degE."""
    zone = int((lon + 180) // 6) + 1
    return (32700 if lat < 0 else 32600) + zone


def season_of(ts) -> str:
    """Crop year label for a timestamp — 1 July to 30 June, named for the year it starts."""
    import pandas as pd

    ts = pd.Timestamp(ts)
    y = ts.year if ts.month >= config.SEASON_START_MONTH else ts.year - 1
    return f"{y}/{str(y + 1)[-2:]}"
