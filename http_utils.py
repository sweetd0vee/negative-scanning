from __future__ import annotations

import ssl
from typing import Optional


def ssl_context() -> ssl.SSLContext:
    """
    Build an SSL context that works reliably on macOS Python installs.
    Prefers certifi bundle when available; falls back to default trust store.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi  # type: ignore

        ctx.load_verify_locations(certifi.where())
    except Exception:
        # certifi not installed or failed to load: use system defaults
        pass
    return ctx

