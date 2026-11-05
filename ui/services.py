"""The one place that picks the backend.

    INVOICE_SERVICE=mock  (default) -> ui/mock_service.py, fixture data
    INVOICE_SERVICE=real            -> agent/service.py, the real agent

Pages import `svc` from here and never import agent/ or mock_service directly.
"""
from __future__ import annotations

import os

MODE = os.getenv("INVOICE_SERVICE", "mock").strip().lower()

if MODE == "real":
    from agent import service as svc  # noqa: F401
elif MODE == "mock":
    from ui import mock_service as svc  # noqa: F401
else:
    raise SystemExit(f"INVOICE_SERVICE must be 'mock' or 'real', not {MODE!r}")


async def open_case_count() -> int:
    """Number for the review-queue badge in the left rail."""
    try:
        return len([c for c in await svc.list_cases() if str(getattr(c, "status", "")).endswith("OPEN")])
    except Exception:  # the badge must never break a page
        return 0
