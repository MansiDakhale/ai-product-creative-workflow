"""
utils/http_client.py
HTTPS clients with reliable CA bundle (fixes SSL CERTIFICATE_VERIFY_FAILED on Windows).
"""

from __future__ import annotations

import os
from typing import Any

import certifi
import httpx


def ssl_verify() -> str | bool:
    """
    CA bundle for TLS verification.
    Set SSL_VERIFY=false in .env only for local debugging (not recommended).
    Or set SSL_CERT_FILE to a custom .pem path.
    """
    if os.getenv("SSL_VERIFY", "true").lower() in ("0", "false", "no"):
        return False
    custom = os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE")
    if custom and os.path.isfile(custom):
        return custom
    return certifi.where()


def make_sync_client(**kwargs: Any) -> httpx.Client:
    return httpx.Client(verify=ssl_verify(), **kwargs)


def make_async_client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=ssl_verify(), **kwargs)
