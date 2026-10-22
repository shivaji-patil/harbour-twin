"""Publish an AOI's products to S3-compatible object storage (AWS S3, Cloudflare R2, SeaweedFS, MinIO).

Keys mirror the repository layout (data/<aoi>/..., web/...), so the STAC
catalog's relative links and the viewer's data paths keep working when read
straight from the bucket. Credentials and the endpoint come from the standard
AWS environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
AWS_ENDPOINT_URL for anything that is not AWS), so nothing is configured here.
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from .config import AOI, ROOT

log = logging.getLogger(__name__)

# Types mimetypes does not know, or gets wrong for geospatial files.
TYPES = {
    ".parquet": "application/vnd.apache.parquet",
    ".gpkg": "application/geopackage+sqlite3",
    ".glb": "model/gltf-binary",
    ".tif": "image/tiff; application=geotiff; profile=cloud-optimized",
    ".czml": "application/json",
    ".bin": "application/octet-stream",
    ".dt2": "application/octet-stream",
    ".slpk": "application/octet-stream",
    ".md": "text/markdown",
}
SKIP = {".aux.xml"}  # GDAL side-cars


def content_type(path: Path) -> str:
    if path.name.endswith(".city.json"):
        return "application/city+json"
    return TYPES.get(path.suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def files(aoi: AOI) -> list[Path]:
    """What a customer needs: the AOI's products and the viewer that reads them."""
    out = [p for p in aoi.data_dir.rglob("*") if p.is_file()]
    web = ROOT / "web"
    out += [p for p in web.iterdir() if p.is_file()]  # index.html, app.js, style.css
    out += [p for p in aoi.web_dir.rglob("*") if p.is_file()]
    return sorted(p for p in out if not any(p.name.endswith(s) for s in SKIP))


def run(aoi: AOI, dest: str) -> dict:
    """Upload to `dest` (s3://bucket[/prefix]), creating the bucket if it does not exist."""
    import boto3
    from botocore.exceptions import ClientError

    u = urlparse(dest)
    if u.scheme != "s3" or not u.netloc:
        raise ValueError(f"expected s3://bucket[/prefix], got {dest!r}")
    bucket, prefix = u.netloc, u.path.strip("/")
    s3 = boto3.client("s3")
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)
    n = size = 0
    for p in files(aoi):
        key = "/".join(x for x in (prefix, p.relative_to(ROOT).as_posix()) if x)
        s3.upload_file(str(p), bucket, key, ExtraArgs={"ContentType": content_type(p)})
        n, size = n + 1, size + p.stat().st_size
    base = f"{s3.meta.endpoint_url.rstrip('/')}/{bucket}" + (f"/{prefix}" if prefix else "")
    out = {"objects": n, "size_mb": round(size / 1e6, 1),
           "stac": f"{base}/data/{aoi.slug}/stac/catalog.json", "viewer": f"{base}/web/index.html?aoi={aoi.slug}"}
    log.info("publish %s: %s", aoi.slug, out)
    return out
