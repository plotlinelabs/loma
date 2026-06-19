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


def _get_s3_client():
    """Create an S3 client configured for Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _get_bucket():
    return os.environ.get("R2_PUBLIC_BUCKET_NAME", os.environ.get("R2_BUCKET_NAME", ""))


def _get_cdn_base():
    return os.environ["R2_MEDIA_BASE_URL"].rstrip("/")


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
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    return {"error": f"Failed to download URL (HTTP {resp.status}): {url}"}
                data = await resp.read()

                # Determine extension from URL or content-type
                from urllib.parse import urlparse
                parsed = urlparse(url)
                ext = os.path.splitext(parsed.path)[1].lower()
                if not ext:
                    ct = resp.headers.get("Content-Type", "")
                    ext_guess = mimetypes.guess_extension(ct.split(";")[0].strip())
                    ext = ext_guess or ""

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

    except aiohttp.ClientError as e:
        return {"error": f"Failed to download URL: {e}"}


# -- CLI entry point -----------------------------------------------------------

def _parse_flag(args, flag, default=""):
    """Extract a --flag value from args list."""
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return default


if __name__ == "__main__":
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
