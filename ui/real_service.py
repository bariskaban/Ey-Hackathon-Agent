"""The real backend, presented in the shape the UI pages read.

`ui/services.py` picks this module when INVOICE_SERVICE=real. It has the same functions as
`ui/mock_service.py`, but every answer comes from `agent/service.py`. The pages were built against the
mock's flat, attribute-style objects, so this file converts the backend's Pydantic models into that shape.
It only reads and renames; no verification logic lives here, and the backend's contract stays untouched.

The conversions in one place:
    DocumentInfo   + last run      -> vendor, amount, status chip, main finding
    Invoice        (nested)        -> flat invoice (supplier_name, vat_lines, iban_last4 ...)
    Verdict        + last run      -> the verdict page's view (invoice, VAT and bank panels, evidence)
    check names                    -> the UI's names (CHECK_NAME_MAP)
    ReviewCase                     -> flat case with a decision
    AuditEntry / EvalReport / SystemStatus -> the rows the audit and eval pages draw
"""
from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent import service as backend
from agent.gateway import DEFAULT_MAX_CALLS
from agent.roles import ROLES
from agent.schemas import (
    CheckStatus,
    EvalProgress,
    EvalReport,
    Invoice,
    Verdict,
    VerdictStatus,
)
from servers.common.db import read_only

# The backend's check names on the left, the UI's on the right (ui/theme.py CHECK_LABELS).
CHECK_NAME_MAP = {
    "injection_scan": "prompt_injection", "vendor_lookup": "vendor_known", "duplicate_invoice": "not_duplicate",
    "already_paid": "not_already_paid", "po_amount_match": "po_match", "goods_receipt_match": "goods_received",
    "invoice_math": "vat_math", "bank_details": "bank_details_match", "amount_anomaly": "amount_typical",
    "date_flags": "date_ok",
}
# The checks the backend really runs (UI names, with their server). The checklist waits for exactly these.
EXPECTED_CHECKS = {
    "prompt_injection": "host", "vendor_known": "transactions", "not_duplicate": "transactions",
    "not_already_paid": "transactions", "po_match": "transactions", "goods_received": "transactions",
    "vat_number_valid": "tax_rules", "vat_treatment": "tax_rules", "vat_rate": "tax_rules",
    "mandatory_fields": "tax_rules", "vat_math": "tax_rules", "bank_details_match": "vendor_checks",
    "sanctions": "vendor_checks", "employee_overlap": "vendor_checks", "amount_typical": "anomaly_stats",
    "date_ok": "anomaly_stats",
}

_running: set[str] = set()


def _ns(x: Any) -> Any:
    """Nested dicts become objects with attribute access, like the mock's."""
    if isinstance(x, dict):
        # Tool-call `args` stay one level deep: the pages json.dumps each value, so values must stay plain JSON.
        return SimpleNamespace(**{k: SimpleNamespace(**v) if k == "args" and isinstance(v, dict) else _ns(v)
                                  for k, v in x.items()})
    if isinstance(x, list):
        return [_ns(v) for v in x]
    return x


def _check_name(name: str) -> str:
    return CHECK_NAME_MAP.get(name, name)


def _last4(iban: str | None) -> str | None:
    return iban[-4:] if iban else None


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except ArithmeticError:
        return None


# --------------------------------------------------------------------------- what the last run left behind


class _Run:
    """The facts of one recorded run that the pages need but the Verdict does not carry."""

    def __init__(self, events: list) -> None:
        self.invoice: Invoice | None = None
        self.low_confidence: list[str] = []
        self.method = None
        self.tool_calls = sum(e.type == "tool_call" for e in events)
        for e in events:
            if e.type == "invoice_extracted":
                self.invoice, self.low_confidence = e.invoice, list(e.low_confidence_fields)
            elif e.type == "markdown_ready":
                self.method = e.method
        self.seconds = 0
        if len(events) > 1:
            try:
                self.seconds = round((datetime.fromisoformat(events[-1].ts)
                                      - datetime.fromisoformat(events[0].ts)).total_seconds())
            except ValueError:
                pass


def _run(doc_id: str) -> _Run:
    return _Run(backend.read_run(doc_id))


def flat_invoice(invoice: Invoice) -> dict:
    """Backend invoice (nested parties, `vat_breakdown`) to the flat one the pages read."""
    data = invoice.model_dump(mode="json")
    supplier, buyer = data["supplier"], data["buyer"]
    return {
        "supplier_name": supplier["name"], "supplier_country": supplier["country"],
        "supplier_vat_number": supplier["vat_number"], "supplier_kvk_number": supplier["kvk_number"],
        "supplier_address": supplier["address"], "buyer_name": buyer["name"],
        "buyer_vat_number": buyer["vat_number"], "buyer_address": buyer["address"],
        "invoice_number": data["invoice_number"], "invoice_date": data["invoice_date"],
        "supply_date": data["supply_date"], "po_number": data["po_number"], "currency": data["currency"] or "EUR",
        "lines": [{"description": ln["description"], "qty": ln["quantity"], "unit_price": ln["unit_price"],
                   "vat_rate": ln["vat_rate"], "amount": ln["line_total"]} for ln in data["lines"]],
        "vat_lines": [{"rate": v["rate"], "net": v["base"], "vat": v["vat"]} for v in data["vat_breakdown"]],
        "total_excl_vat": data["total_excl_vat"], "total_vat": data["total_vat"],
        "total_incl_vat": data["total_incl_vat"], "iban_last4": _last4(supplier["iban"]),
        "iban": supplier["iban"], "notes": data["notes"],  # not shown as fields; used to box the value on the page
        "reverse_charge_text": "Reverse charge" if data["reverse_charge"] else None,
    }


# --------------------------------------------------------------------------- verdict view


def _main_finding(verdict: Verdict) -> str:
    if verdict.discrepancies:
        return verdict.discrepancies[0].type.value
    if verdict.verdict == VerdictStatus.NEEDS_REVIEW:
        return verdict.needs_review_reasons[0] if verdict.needs_review_reasons else "A person must look"
    return "All checks pass"


def _find_vendor(invoice: Invoice | None) -> sqlite3.Row | None:
    vat = (invoice.supplier.vat_number or "").replace(" ", "").upper() if invoice else ""
    if not vat:
        return None
    try:
        with read_only() as con:
            return con.execute("SELECT * FROM vendors WHERE REPLACE(UPPER(vat_number), ' ', '') = ?", (vat,)).fetchone()
    except sqlite3.Error:
        return None


def _evidence(ids: list[str]) -> list[dict]:
    """The company records a verdict relied on, masked the way the agent saw them."""
    records: list[dict] = []
    try:
        with read_only() as con:
            for record_id in ids:
                if txn := con.execute("SELECT * FROM transactions WHERE txn_id = ?", (record_id,)).fetchone():
                    records.append({"record_id": record_id, "kind": "Earlier payment", "date": txn["date"],
                                    "amount": f"{txn['amount_eur']:.2f}", "currency": "EUR",
                                    "iban_last4": _last4(txn["iban"]), "status": str(txn["status"]).capitalize()})
                elif po := con.execute("SELECT * FROM purchase_orders WHERE po_number = ?", (record_id,)).fetchone():
                    records.append({"record_id": record_id, "kind": "Purchase order", "date": po["date"],
                                    "amount": f"{po['total_excl_vat']:.2f}", "currency": "EUR", "iban_last4": None,
                                    "status": str(po["status"]).capitalize()})
                elif grn := con.execute("SELECT * FROM goods_receipts WHERE grn_id = ?", (record_id,)).fetchone():
                    records.append({"record_id": record_id, "kind": "Goods receipt", "date": grn["date"],
                                    "amount": None, "currency": None, "iban_last4": None, "status": "Received"})
                else:
                    records.append({"record_id": record_id, "kind": "Record", "date": None, "amount": None,
                                    "currency": None, "iban_last4": None, "status": ""})
    except sqlite3.Error:
        pass
    return records


def _vat_panel(verdict: Verdict, invoice: Invoice) -> dict:
    by_name = {c.name: c for c in verdict.checks}

    def failed(name: str) -> bool:
        return name in by_name and by_name[name].status == CheckStatus.FAIL

    country = (invoice.supplier.country or "").upper()
    applies = bool(country) and country != "NL" and "vat_treatment" in by_name \
        and by_name["vat_treatment"].status != CheckStatus.SKIPPED
    mandatory = [d.field for d in verdict.discrepancies if d.type.value == "MISSING_MANDATORY_FIELDS" and d.field]
    lines = []
    for vat in invoice.vat_breakdown:
        net, found = _money(vat.base) or Decimal(0), _money(vat.vat) or Decimal(0)
        rate = _money(vat.rate) or Decimal(0)
        expected = Decimal(0) if applies else (net * rate / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
        lines.append({"rate_label": "Reverse charge (0%)" if applies else f"{rate.normalize():f}%",
                      "net": f"{net:.2f}", "vat_expected": f"{expected:.2f}", "vat_found": f"{found:.2f}",
                      "ok": not (failed("vat_treatment") or failed("vat_rate") or failed("invoice_math"))})
    return {"lines": lines, "reverse_charge_applies": applies,
            "shown_correctly": not failed("vat_treatment") and (
                not applies or (bool(invoice.reverse_charge) and (_money(invoice.total_vat) or Decimal(0)) == 0)),
            "vat_number_valid": by_name["vat_number_valid"].status == CheckStatus.PASS
            if "vat_number_valid" in by_name and by_name["vat_number_valid"].status != CheckStatus.SKIPPED else None,
            "missing_fields": mandatory}


def _bank_panel(verdict: Verdict, invoice: Invoice) -> dict | None:
    by_name = {c.name: c for c in verdict.checks}
    bank = by_name.get("bank_details")
    if bank is None or bank.status == CheckStatus.SKIPPED:
        return None
    vendor = _find_vendor(invoice)
    return {"iban_matches": bank.status == CheckStatus.PASS, "invoice_iban_last4": _last4(invoice.supplier.iban),
            "vendor_iban_last4": _last4(vendor["iban"]) if vendor else None,
            "last_changed": vendor["iban_changed_at"] if vendor else None,
            "sanctions_hit": "sanctions" in by_name and by_name["sanctions"].status == CheckStatus.FAIL,
            "employee_overlap": "employee_overlap" in by_name
            and by_name["employee_overlap"].status == CheckStatus.FAIL}


def verdict_view(doc_id: str, verdict: Verdict, run: _Run) -> dict:
    """Everything the verdict page draws, from the backend verdict and the run that produced it."""
    check_of_error = {c.error_type: _check_name(c.name) for c in verdict.checks if c.error_type}
    invoice = run.invoice
    return {
        "doc_id": doc_id, "invoice_id": verdict.invoice_id, "verdict": verdict.verdict.value,
        "reason": "; ".join(verdict.needs_review_reasons) or None, "confidence": verdict.confidence,
        "auditor_note": verdict.reasoning, "reasoning": verdict.reasoning,
        "discrepancies": [{**d.model_dump(mode="json"), "check": check_of_error.get(d.type)}
                          for d in verdict.discrepancies],
        "risk_flags": list(verdict.risk_flags),
        "checks": [{"name": _check_name(c.name), "server": c.server, "status": c.status.value, "detail": c.detail,
                    "severity": c.severity.value if c.severity else None} for c in verdict.checks],
        "evidence_transaction_ids": list(verdict.evidence_transaction_ids),
        "evidence": _evidence(verdict.evidence_transaction_ids),
        "vat": _vat_panel(verdict, invoice) if invoice else None,
        "bank": _bank_panel(verdict, invoice) if invoice else None,
        "low_confidence_fields": run.low_confidence,
        "invoice": flat_invoice(invoice) if invoice else flat_invoice(Invoice()),
        "review_case_id": verdict.review_case_id, "model": backend_model(), "seconds": run.seconds,
        "tool_calls": run.tool_calls, "created_at": verdict.created_at,
    }


def backend_model() -> str:
    llm = backend._runtime.llm
    return getattr(llm, "model", None) or backend.LLM().model


# --------------------------------------------------------------------------- documents


def _document(info) -> dict:
    run, verdict = _run(info.doc_id), None
    if info.verdict is not None:
        verdict = backend._verdict_path(info.doc_id)
        verdict = Verdict.model_validate_json(verdict.read_text(encoding="utf-8")) if verdict.exists() else None
    invoice = run.invoice
    status = "VERIFYING" if info.doc_id in _running else "VERIFIED" if verdict else "NOT_VERIFIED"
    return {
        "doc_id": info.doc_id, "filename": info.filename, "mime_type": info.type, "size_bytes": info.size_bytes,
        "pages": info.pages, "read_method": run.method,
        "vendor_name": invoice.supplier.name if invoice else None,
        "vendor_country": invoice.supplier.country if invoice else None,
        "invoice_number": invoice.invoice_number if invoice else None,
        "invoice_date": invoice.invoice_date if invoice else None,
        "amount_total": str(invoice.total_incl_vat) if invoice and invoice.total_incl_vat is not None else None,
        "currency": (invoice.currency if invoice else None) or "EUR", "status": status,
        "verdict": verdict.verdict.value if verdict else None,
        "main_finding": _main_finding(verdict) if verdict else None,
        "risk_flags": [d.type.value for d in verdict.discrepancies] if verdict else [], "uploaded_at": None,
    }


async def list_documents() -> list[Any]:
    return [_ns(_document(d)) for d in sorted(await backend.list_documents(), key=lambda d: d.doc_id)]


async def upload_document(filename: str, data: bytes) -> Any:
    return _ns(_document(await backend.upload_document(filename, data)))


def get_document_file(doc_id: str) -> Path:
    return backend.get_document_file(doc_id)


# --------------------------------------------------------------------------- verification


def _event_view(event, run_events: list) -> dict:
    data = event.model_dump(mode="json")
    kind = data["type"]
    if kind == "check_result":
        data["name"] = _check_name(data["name"])
    elif kind == "tool_call":
        data["result_preview"] = data.pop("error", None) or (f"{data['rows_returned']} rows"
                                                            if data.get("rows_returned") is not None else "")
        data["refusal_reason"] = "Not on this role's list" if data["refused"] else None
    elif kind == "invoice_extracted":
        data["invoice"] = flat_invoice(event.invoice)
    elif kind == "verdict":
        data["verdict"] = verdict_view(event.doc_id, event.verdict, _Run(run_events))
    return data


async def verify(doc_id: str) -> AsyncIterator[Any]:
    """Stream the backend's events, renamed for the pages. The last one is the verdict (or an error)."""
    _running.add(doc_id)
    seen: list = []
    try:
        async for event in backend.verify(doc_id):
            seen.append(event)
            yield _ns(_event_view(event, seen))
    finally:
        _running.discard(doc_id)


async def get_verdict(doc_id: str) -> Any | None:
    verdict = await backend.get_verdict(doc_id)
    return _ns(verdict_view(doc_id, verdict, _run(doc_id))) if verdict else None


# --------------------------------------------------------------------------- review cases


def _case(case) -> dict:
    evidence = case.evidence or {}
    verdict, invoice = evidence.get("verdict") or {}, evidence.get("invoice") or {}
    flat = flat_invoice(Invoice.model_validate(invoice)) if invoice else flat_invoice(Invoice())
    checks = verdict.get("checks") or []
    status = case.status.value.upper()
    decision = None
    if status != "OPEN":
        decision = {"status": status, "reviewer": case.status_changed_by, "reason": case.status_reason,
                    "ts": case.notes[-1].ts if case.notes else case.created_at}
    return {
        "case_id": case.case_id, "doc_id": case.invoice_id, "vendor_name": flat["supplier_name"],
        "error_type": case.error_types[0].value if case.error_types else None,
        "severity": case.top_severity.value if case.top_severity else "high", "verdict": case.verdict.value,
        "status": status, "created_at": case.created_at, "summary": verdict.get("reasoning") or "",
        "amount": flat["total_incl_vat"], "currency": flat["currency"],
        "checks_passed": sum(c.get("status") == "pass" for c in checks), "checks_total": len(checks),
        "notes": [n.model_dump(mode="json") for n in case.notes], "decision": decision,
    }


async def list_cases(status: str | None = None) -> list[Any]:
    cases = await backend.list_cases(status.lower() if status and status != "ALL" else None)
    return [_ns(_case(c)) for c in cases]


async def get_case(case_id: str) -> Any:
    return _ns(_case(await backend.get_case(case_id)))


async def set_case_status(case_id: str, status: str, reviewer: str, reason: str) -> Any:
    """Humans only. This is the one place a case gets a final status."""
    return _ns(_case(await backend.set_case_status(case_id, status.lower(), reviewer, reason)))


async def add_case_note(case_id: str, text: str, author: str) -> Any:
    return _ns(_case(await backend.add_case_note(case_id, text, author)))


async def export_evidence_pack(case_id: str, fmt: str = "pdf") -> Path:
    return await backend.export_evidence_pack(case_id, fmt)


# --------------------------------------------------------------------------- audit, eval, status


def _run_window(doc_id: str) -> tuple[datetime, datetime] | None:
    """When the recorded run of a document started and ended (None if it was never verified here)."""
    events = backend.read_run(doc_id)
    if not events:
        return None
    return datetime.fromisoformat(events[0].ts), datetime.fromisoformat(events[-1].ts)


async def get_audit_log(doc_id: str | None = None, role: str | None = None, server: str | None = None) -> list[Any]:
    """The tool calls behind the verdicts the UI shows: those made during each document's recorded run.

    Evaluation runs and earlier attempts stay in data/audit.jsonl but would make one invoice look like it
    used many times its call budget, so they are left out of this view.
    """
    entries = await backend.get_audit_log(doc_id, role, server)
    windows: dict[str, tuple[datetime, datetime] | None] = {}
    shown = []
    for e in entries:
        if e.invoice_id not in windows:
            windows[e.invoice_id] = _run_window(e.invoice_id) if e.invoice_id else None
        window = windows[e.invoice_id]
        if window and window[0] <= datetime.fromisoformat(e.ts) <= window[1]:
            shown.append(e)
    return [_ns({"doc_id": e.invoice_id, "ts": e.ts, "role": e.role, "server": e.server, "tool": e.tool,
                 "args": e.args, "rows_returned": e.rows_returned, "latency_ms": e.latency_ms,
                 "refused": e.refused, "result_preview": "" if e.refused else f"{e.result_size} characters returned",
                 "refusal_reason": e.reason, "seq": i}) for i, e in enumerate(shown, 1)]


async def run_eval(model: str, runs_per_invoice: int = 3) -> AsyncIterator[Any]:
    async for p in backend.run_eval(model, runs_per_invoice):
        p: EvalProgress
        yield _ns({"done": p.completed, "total": p.total, "doc_id": p.invoice_id, "run": p.run_index,
                   "model": p.model, "finished": p.finished, "report_id": p.run_id if p.finished else None,
                   "message": "Finished" if p.finished else f"Run {p.run_index} of {runs_per_invoice}: {p.invoice_id}"})


def _report(report: EvalReport) -> dict:
    rows = [{"doc_id": r.invoice_id, "planted_error": r.expected_error, "expected_verdict": r.expected_verdict.value,
             "runs": [v.value for v in r.verdicts], "caught_by": ", ".join(_check_name(c) for c in r.caught_by) or None,
             "seconds": r.seconds, "tool_calls": r.tool_calls, "outcome": r.outcome, "note": r.note,
             "consistent": r.consistent, "scanned": r.scanned} for r in report.invoices]
    return {"report_id": report.run_id, "model": report.model, "runs_per_invoice": report.runs_per_invoice,
            "started_at": report.created_at, "finished_at": report.created_at,
            "duration_s": round(sum(r["seconds"] * report.runs_per_invoice for r in rows)), "sample": False,
            "rows": rows, "passed": report.passed, "safe_failed": report.safe_failed, "failed": report.failed}


async def list_eval_runs() -> list[Any]:
    return [_ns(_report(r)) for r in await backend.list_eval_runs()]


async def system_status() -> Any:
    s = await backend.system_status()
    roles = [{"role": name, "tools": list(role.llm_tools), "budget": DEFAULT_MAX_CALLS} for name, role in ROLES.items()]
    return _ns({"ollama_up": s.ollama_up, "model": s.model, "model_available": s.model_available,
                "servers_up": sum(s.servers.values()), "servers_total": len(s.servers), "service_mode": "real",
                "roles": roles, "database_ok": s.database_ok, "tesseract_ok": s.tesseract_ok})
