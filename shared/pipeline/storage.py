"""R2 (S3-compatible) storage helpers.

Layout convention (per case):
    raw/<source>/<vintage>/...      immutable source extracts
    derived/<dataset>/<vintage>/... pipeline outputs (parquet, PMTiles, JSON)

Everything is idempotent by vintage key: re-running a month/release overwrites
its own prefix and touches nothing else.
"""

from __future__ import annotations

import os
from pathlib import Path


def r2_client():
    """S3 client against Cloudflare R2, configured from environment."""
    import boto3  # deferred: keeps stub importable without deps installed

    account = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def upload_dir(local: Path, prefix: str) -> int:
    """Upload a directory tree under `prefix`. Returns file count."""
    client = r2_client()
    bucket = os.environ.get("R2_BUCKET", "demo-lab-data")
    n = 0
    for path in sorted(local.rglob("*")):
        if path.is_file():
            client.upload_file(str(path), bucket, f"{prefix}/{path.relative_to(local)}")
            n += 1
    return n


def download_prefix(prefix: str, dest: Path) -> int:
    """Mirror a prefix into `dest`. Returns file count."""
    client = r2_client()
    bucket = os.environ.get("R2_BUCKET", "demo-lab-data")
    n = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            target = dest / os.path.relpath(obj["Key"], prefix)
            target.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, obj["Key"], str(target))
            n += 1
    return n
