"""The one place the pages get their backend from.

Pages import `svc` from here and never import agent/ directly. `svc` is ui/real_service.py, which wraps
agent/service.py (the real agent) and renames its answers into the flat shape the pages read.
"""
from __future__ import annotations

from ui import real_service as svc

MODE = "real"


def expected_checks() -> dict[str, str]:
    """check name -> server, for the live checklist."""
    return dict(svc.EXPECTED_CHECKS)


async def open_case_count() -> int:
    """Number for the review-queue badge in the left rail."""
    try:
        return len([c for c in await svc.list_cases() if str(getattr(c, "status", "")).endswith("OPEN")])
    except Exception:  # noqa: BLE001 - the badge must never break a page
        return 0
