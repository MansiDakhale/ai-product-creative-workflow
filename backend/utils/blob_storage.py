"""
utils/blob_storage.py
Helpers for uploading media assets to Vercel Blob (or compatible storage).
"""

from __future__ import annotations

import os
from typing import Optional

import structlog

logger = structlog.get_logger()


async def upload_bytes(file_bytes: bytes, blob_path: str, content_type: str) -> Optional[str]:
    """Upload bytes to blob storage and return a public URL when available.

    Expected env vars:
      - BLOB_READ_WRITE_TOKEN
      - BLOB_BASE_URL (e.g. https://blob.vercel-storage.com/<store_id>)
    """
    token = os.getenv("BLOB_READ_WRITE_TOKEN")
    base_url = os.getenv("BLOB_BASE_URL")

    if not token or not base_url:
        logger.warning("blob_not_configured")
        return None

    upload_url = f"{base_url.rstrip('/')}/{blob_path.lstrip('/')}"

    # Use the shared httpx client if available to keep settings consistent.
    try:
        from utils.http_client import make_async_client
        async with make_async_client(timeout=180) as client:
            resp = await client.put(
                upload_url,
                content=file_bytes,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": content_type,
                },
            )
    except Exception as e:
        logger.warning("blob_upload_failed", error=str(e))
        return None

    if not (200 <= resp.status_code < 300):
        logger.warning("blob_upload_failed", status=resp.status_code, body=resp.text)
        return None

    # Prefer a JSON response with a URL, otherwise fall back to the upload URL.
    try:
        data = resp.json()
        url = data.get("url") or data.get("downloadUrl")
        if url:
            return url
    except Exception:
        pass

    return upload_url
