"""Shared helpers: resource guards (disk/RAM), resumable fetch, manifest, raster zonal maths.

Resource rules (hard, see README): keep >10 GB free disk, never start heavy work under
4 GB free RAM, at most 4 concurrent downloads. Every stage exits cleanly (code 3) rather
than filling the box; every stage is resumable.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

import config


def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def free_disk_gb(path: str = "/") -> float:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1e9


def free_ram_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 1 << 30


def guard_disk(need_gb: float = config.MIN_FREE_DISK_GB) -> None:
    g = free_disk_gb()
    if g < need_gb:
        log(f"STOP: {g:.1f} GB free disk < {need_gb} GB floor. Exiting cleanly — rerun to resume.")
        sys.exit(3)


def guard_ram(need_mb: int = config.MIN_FREE_RAM_MB) -> None:
    m = free_ram_mb()
    if m < need_mb:
        log(f"STOP: {m} MB RAM available < {need_mb} MB floor. Exiting cleanly — rerun to resume.")
        sys.exit(3)


def sha256(p: Path, limit: int = 1 << 30) -> str:
    h = hashlib.sha256()
    n = 0
    with open(p, "rb") as f:
        while (b := f.read(1 << 20)) and n < limit:
            h.update(b)
            n += len(b)
    return h.hexdigest()


MANIFEST = config.RAW / "manifest.json"


def manifest_put(key: str, **kw) -> None:
    m = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    m[key] = kw
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2, sort_keys=True))


def fetch(url: str, dest: Path, key: str | None = None, min_bytes: int = 2000,
          headers: dict | None = None, timeout: int = 180) -> Path:
    """Resumable, cached download. Skips if the file is already there and plausible."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size >= min_bytes:
        log("cached", dest.name, f"{dest.stat().st_size/1e6:.1f} MB")
        return dest
    guard_disk()
    h = dict(config.BROWSER_UA)
    h.update(headers or {})
    part = dest.with_name(dest.name + ".part")
    mode, pos = "wb", 0
    if part.exists():                          # a previous run (or the curl pre-fetch) left bytes
        try:
            total = int(requests.head(url, headers=h, timeout=30,
                                      allow_redirects=True).headers.get("Content-Length", 0))
            if total and part.stat().st_size >= total:
                part.rename(dest)
                log("completed from .part", dest.name, f"{total/1e6:.1f} MB")
                manifest_put(key or dest.name, url=url,
                             path=str(dest.relative_to(config.CASE_DIR)), bytes=total,
                             sha256=sha256(dest), fetched=time.strftime("%Y-%m-%d"))
                return dest
        except Exception:
            pass
    if part.exists():
        pos = part.stat().st_size
        h["Range"] = f"bytes={pos}-"
        mode = "ab"
    log("GET", url, f"(resume @ {pos/1e6:.1f} MB)" if pos else "")
    with requests.get(url, headers=h, stream=True, timeout=timeout, allow_redirects=True) as r:
        if pos and r.status_code == 200:      # server ignored Range — start over
            mode, pos = "wb", 0
        r.raise_for_status()
        last = time.time()
        with open(part, mode) as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                pos += len(chunk)
                if time.time() - last > 30:
                    last = time.time()
                    log(f"  … {pos/1e6:.0f} MB")
                    guard_disk()
    part.rename(dest)
    lm = None
    try:
        lm = requests.head(url, headers=config.BROWSER_UA, timeout=30).headers.get("Last-Modified")
    except Exception:
        pass
    manifest_put(key or dest.name, url=url, path=str(dest.relative_to(config.CASE_DIR)),
                 bytes=dest.stat().st_size, sha256=sha256(dest), fetched=time.strftime("%Y-%m-%d"),
                 last_modified=lm)
    log("OK", dest.name, f"{dest.stat().st_size/1e6:.1f} MB")
    return dest


def run(cmd: list[str], **kw) -> None:
    log("$", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


# --- raster helpers (GHSL tiles are 100 m Mollweide, ESRI:54009) ----------------------------

def read_window(tif: Path, gdf):
    """Read the raster window covering gdf (reprojected to the raster CRS). Returns arr, transform, gdf_proj."""
    import numpy as np
    import rasterio
    from rasterio.windows import from_bounds

    with rasterio.open(tif) as src:
        g = gdf.to_crs(src.crs)
        b = g.total_bounds
        win = from_bounds(b[0] - 2000, b[1] - 2000, b[2] + 2000, b[3] + 2000,
                          transform=src.transform)
        arr = src.read(1, window=win, boundless=True, fill_value=0).astype("float64")
        nod = src.nodata
        tr = src.window_transform(win)
    if nod is not None:
        arr[arr == nod] = 0.0
    arr[~np.isfinite(arr)] = 0.0
    arr[arr < 0] = 0.0
    return arr, tr, g


def zonal(tif: Path, gdf, want_centroid: bool = False):
    """Sum a raster per polygon; optionally the value-weighted centroid (raster CRS coords)."""
    import numpy as np
    from rasterio.features import rasterize

    arr, tr, g = read_window(tif, gdf)
    n = len(g)
    zid = rasterize(((geom, i + 1) for i, geom in enumerate(g.geometry)),
                    out_shape=arr.shape, transform=tr, fill=0, dtype="int32", all_touched=False)
    flat_z, flat_v = zid.ravel(), arr.ravel()
    tot = np.bincount(flat_z, weights=flat_v, minlength=n + 1)[1:]
    if not want_centroid:
        return tot, None, None
    rows, cols = np.indices(arr.shape)
    xs = (tr.c + (cols + 0.5) * tr.a).ravel()
    ys = (tr.f + (rows + 0.5) * tr.e).ravel()
    wx = np.bincount(flat_z, weights=(flat_v * xs), minlength=n + 1)[1:]
    wy = np.bincount(flat_z, weights=(flat_v * ys), minlength=n + 1)[1:]
    safe = np.where(tot > 0, tot, 1.0)
    return tot, wx / safe, wy / safe


def grid_sums(tif: Path, gdf_extent, cell_m: int):
    """Aggregate a 100 m raster onto a cell_m lattice in the raster CRS.

    Returns (x, y, value, wx, wy): the cell centre, the cell's total, and the value-weighted
    centre of mass inside the cell. The weighted centre is what the routing uses as the
    destination point — a rural cell's geometric centre often lands in a field with no street
    within snapping distance, while its centre of population lands on the village road.
    """
    import numpy as np

    arr, tr, _ = read_window(tif, gdf_extent)
    rows, cols = np.indices(arr.shape)
    xs = tr.c + (cols + 0.5) * tr.a
    ys = tr.f + (rows + 0.5) * tr.e
    ix = np.floor(xs / cell_m).astype("int64")
    iy = np.floor(ys / cell_m).astype("int64")
    ix0, iy0 = ix.min(), iy.min()
    nx = int(ix.max() - ix0 + 1)
    n = nx * int(iy.max() - iy0 + 1)
    key = ((iy - iy0) * nx + (ix - ix0)).ravel()
    v = arr.ravel()
    vals = np.bincount(key, weights=v, minlength=n)
    sx = np.bincount(key, weights=v * xs.ravel(), minlength=n)
    sy = np.bincount(key, weights=v * ys.ravel(), minlength=n)
    k = np.arange(vals.size)
    gx = ((k % nx) + ix0 + 0.5) * cell_m
    gy = ((k // nx) + iy0 + 0.5) * cell_m
    safe = np.where(vals > 0, vals, 1.0)
    return gx, gy, vals, sx / safe, sy / safe
