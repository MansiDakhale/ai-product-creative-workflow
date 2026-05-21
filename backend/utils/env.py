"""
utils/env.py
Load environment variables from .env files for local development.
"""

from __future__ import annotations
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent

# Project root .env (used by docker compose) then backend/.env (local overrides)
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv(_BACKEND_DIR / ".env", override=True)


def _configure_ssl() -> None:
    """
    Windows/Python often lacks trusted CAs for httpx → Groq fails with
    CERTIFICATE_VERIFY_FAILED. Prefer OS cert store via truststore; allow
    SSL_VERIFY=false in .env as a local fallback.
    """
    if os.getenv("SSL_VERIFY", "").lower() in ("0", "false", "no"):
        # Also affects replicate, requests, urllib3
        import ssl
        ssl._create_default_https_context = ssl._create_unverified_context
        return
    if sys.platform != "win32":
        return
    try:
        import truststore  # noqa: F401
        truststore.inject_into_ssl()
    except ImportError:
        pass


_configure_ssl()
