"""The one place that picks the backend.

    INVOICE_SERVICE=mock  (default) -> ui/mock_service.py, fixture data
    INVOICE_SERVICE=real            -> ui/real_service.py, which wraps agent/service.py (the real agent)
                                       and renames its answers into the shape the pages read

Pages import `svc` from here and never import agent/ or mock_service directly.
"""
from __future__ import annotations

import os

MODE = os.getenv("INVOICE_SERVICE", "mock").strip().lower()

if MODE == "real":
    from ui import real_service as svc
elif MODE == "mock":
    from ui import mock_service as svc
else:
    raise SystemExit(f"INVOICE_SERVICE must be 'mock' or 'real', not {MODE!r}")


# Every check the checklist waits for, with its server. The mock runs 18 checks, the real agent 16.
_MOCK_CHECKS = {
    "prompt_injection": "host", "vendor_known": "transactions", "not_duplicate": "transactions",
    "not_already_paid": "transactions", "po_match": "transactions", "goods_received": "transactions",
    "vat_number_valid": "tax_rules", "vat_treatment": "tax_rules", "vat_rate": "tax_rules",
    "mandatory_fields": "tax_rules", "vat_math": "tax_rules", "iban_valid": "vendor_checks",
    "bank_details_match": "vendor_checks", "sanctions": "vendor_checks", "employee_overlap": "vendor_checks",
    "amount_typical": "anomaly_stats", "no_split": "anomaly_stats", "date_ok": "anomaly_stats",
}


def expected_checks() -> dict[str, str]:
    """check name -> server, for the live checklist."""
    return dict(svc.EXPECTED_CHECKS) if MODE == "real" else dict(_MOCK_CHECKS)


async def open_case_count() -> int:
    """Number for the review-queue badge in the left rail."""
    try:
        return len([c for c in await svc.list_cases() if str(getattr(c, "status", "")).endswith("OPEN")])
    except Exception:  # noqa: BLE001 - the badge must never break a page
        return 0
