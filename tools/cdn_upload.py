"""Upload files to Cloudflare R2 and return CDN URLs.

Uses boto3 S3 client pointed at the R2 endpoint. Anything uploaded to the
configured R2 bucket is automatically served via CDN.

Commands:
  1. cdn_upload.py upload --file PATH [--key KEY]
     Upload a local file to R2. Returns the CDN URL.

  2. cdn_upload.py upload-url --url URL [--key KEY]
     Download a file from a URL, then upload it to R2. Returns the CDN URL.

Usage (called by the agent via Bash):
  python3 tools/cdn_upload.py upload --file /tmp/hero.png
  python3 tools/cdn_upload.py upload --file /tmp/logo.svg --key "website/logo.svg"
  python3 tools/cdn_upload.py upload-url --url "https://example.com/image.png"
"""

import asyncio
import json
import mimetypes
import os
import sys
import tempfile
import uuid

import aiohttp
import boto3


def _r2_env(name: str, field: str | None = None) -> str:
    val = os.environ.get(name, "")
    if not val and field:
        from tools._integration_key import get_integration_extra, get_integration_key
        val = get_integration_key("cdn_r2") if field == "__key__" else get_integration_extra("cdn_r2", field)
    return val


def _get_s3_client():
    """Create an S3 client configured for Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=_r2_env("R2_ENDPOINT", "endpoint"),
        aws_access_key_id=_r2_env("R2_ACCESS_KEY_ID", "access_key_id"),
        aws_secret_access_key=_r2_env("R2_SECRET_ACCESS_KEY", "__key__"),
        region_name="auto",
    )


def _get_bucket():
    return os.environ.get("R2_PUBLIC_BUCKET_NAME", os.environ.get("R2_BUCKET_NAME", "")) or _r2_env("", "bucket_name")


def _get_cdn_base():
    val = os.environ.get("R2_MEDIA_BASE_URL", "") or _r2_env("", "media_base_url")
    return val.rstrip("/")


UPLOAD_PREFIX = "loma-images"


def _generate_key(file_path: str, custom_key: str | None = None) -> str:
    """Generate an object key under the loma-images/ prefix."""
    if custom_key:
        # Ensure custom keys are also under the prefix
        if not custom_key.startswith(f"{UPLOAD_PREFIX}/"):
            return f"{UPLOAD_PREFIX}/{custom_key}"
        return custom_key
    ext = os.path.splitext(file_path)[1].lower()
    return f"{UPLOAD_PREFIX}/{uuid.uuid4().hex}{ext}"


def _guess_content_type(file_path: str) -> str:
    """Guess the MIME type from the file extension."""
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or "application/octet-stream"


def upload_file(file_path: str, custom_key: str | None = None) -> dict:
    """Upload a local file to R2 and return the CDN URL."""
    if not os.path.isfile(file_path):
        return {"error": f"File not found: {file_path}"}

    key = _generate_key(file_path, custom_key)
    content_type = _guess_content_type(file_path)
    size_bytes = os.path.getsize(file_path)

    try:
        client = _get_s3_client()
        client.upload_file(
            file_path,
            _get_bucket(),
            key,
            ExtraArgs={"ContentType": content_type},
        )
        cdn_url = f"{_get_cdn_base()}/{key}"
        return {
            "success": True,
            "cdn_url": cdn_url,
            "key": key,
            "content_type": content_type,
            "size_bytes": size_bytes,
        }
    except KeyError as e:
        return {"error": f"Missing environment variable: {e}"}
    except Exception as e:
        return {"error": f"Upload failed: {e}"}


async def upload_from_url(url: str, custom_key: str | None = None) -> dict:
    """Download a file from a URL and upload it to R2."""
    try:
        from tools._public_download import download_public
        from urllib.parse import urlparse
        data, content_type = await download_public(url)
        ext = os.path.splitext(urlparse(url).path)[1].lower()
        if not ext or not ext[1:].isalnum() or len(ext) > 12:
            ext = mimetypes.guess_extension(content_type.split(';')[0].strip()) or ''

        # Write to temp file
        suffix = ext if ext else ""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            result = upload_file(tmp_path, custom_key)
            return result
        finally:
            os.unlink(tmp_path)

    except (aiohttp.ClientError, ValueError, asyncio.TimeoutError):
        return {"error": "Public HTTPS download failed or exceeded its limits"}


# -- CLI entry point -----------------------------------------------------------

def _parse_flag(args, flag, default=""):
    """Extract a --flag value from args list."""
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return default


if __name__ == "__main__":
    from _integration_access import authorize_cli
    authorize_cli('cdn_r2')
    from dotenv import load_dotenv
    load_dotenv()

    args = sys.argv[1:]
    if not args:
        print(json.dumps({
            "error": "Usage: python3 tools/cdn_upload.py <command> [flags]\nCommands: upload, upload-url"
        }))
        sys.exit(1)

    command = args[0]

    if command == "upload":
        file_path = _parse_flag(args, "--file")
        key = _parse_flag(args, "--key") or None

        if not file_path:
            result = {"error": "Missing required flag: --file"}
        else:
            result = upload_file(file_path, key)

    elif command == "upload-url":
        url = _parse_flag(args, "--url")
        key = _parse_flag(args, "--key") or None

        if not url:
            result = {"error": "Missing required flag: --url"}
        else:
            result = asyncio.run(upload_from_url(url, key))

    else:
        result = {"error": f"Unknown command: {command}. Use: upload, upload-url"}

    print(json.dumps(result, indent=2))
