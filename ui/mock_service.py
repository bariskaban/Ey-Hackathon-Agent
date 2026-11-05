"""Mock implementation of the agent/service.py contract (todo.md section 1).

The UI imports this through ui/services.py when INVOICE_SERVICE=mock (the
default). Every function has the same name and arguments as the real service.

Return values are attribute-access objects built from the JSON fixtures, so
pages can write `doc.vendor_name` and work the same way against the real
service's Pydantic models. Field names are listed in docs/ui_fields.md.

Environment variables:
    MOCK_SPEED   playback speed for verify() and run_eval(), default 1.0
                 (2.0 = twice as fast; useful while building pages)
"""
from __future__ import annotations

import asyncio
import copy
import csv
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
WORK_DIR = Path(tempfile.gettempdir()) / "tickmark_mock"
SPEED = float(os.getenv("MOCK_SPEED", "1.0"))

# --------------------------------------------------------------------------
# Check catalogue. Shared with fixtures/_generate.py so every fixture agrees.
# --------------------------------------------------------------------------
CHECKS: list[dict[str, Any]] = [
    {"name": "prompt_injection", "server": "host", "tool": None, "ok": "No text addressed to an AI found in the invoice."},
    {"name": "vendor_known", "server": "transactions", "tool": "get_vendor", "ok": "Matched to a vendor in the vendor master."},
    {"name": "not_duplicate", "server": "transactions", "tool": "lookup_invoice_number", "ok": "No earlier invoice with this number or a close variant."},
    {"name": "not_already_paid", "server": "transactions", "tool": "find_transactions", "ok": "No payment found with this reference or amount."},
    {"name": "po_match", "server": "transactions", "tool": "get_purchase_order", "ok": "Amount excluding VAT is within the purchase order."},
    {"name": "goods_received", "server": "transactions", "tool": "get_purchase_order", "ok": "The goods receipt covers every invoiced line."},
    {"name": "vat_number_valid", "server": "tax_rules", "tool": "validate_vat_number", "ok": "Supplier VAT number passes the format and checksum check."},
    {"name": "vat_treatment", "server": "tax_rules", "tool": "expected_vat_treatment", "ok": "VAT treatment matches the supplier and buyer countries."},
    {"name": "vat_rate", "server": "tax_rules", "tool": "get_vat_rate", "ok": "Each line uses the right VAT rate for what was sold."},
    {"name": "mandatory_fields", "server": "tax_rules", "tool": "check_invoice_requirements", "ok": "All fields Dutch invoices must show are present."},
    {"name": "vat_math", "server": "tax_rules", "tool": "validate_invoice_math", "ok": "Line totals, VAT per rate and the total add up."},
    {"name": "iban_valid", "server": "vendor_checks", "tool": "validate_iban", "ok": "IBAN passes the checksum check."},
    {"name": "bank_details_match", "server": "vendor_checks", "tool": "compare_bank_details", "ok": "IBAN matches the vendor record."},
    {"name": "sanctions", "server": "vendor_checks", "tool": "screen_sanctions", "ok": "No close match on the EU sanctions list."},
    {"name": "employee_overlap", "server": "vendor_checks", "tool": "vendor_employee_overlap", "ok": "No overlap with employee addresses or bank accounts."},
    {"name": "amount_typical", "server": "anomaly_stats", "tool": "vendor_amount_profile", "ok": "Amount is within this vendor's usual range."},
    {"name": "no_split", "server": "anomaly_stats", "tool": "split_invoice_check", "ok": "No invoices just under the approval limit this week."},
    {"name": "date_ok", "server": "anomaly_stats", "tool": "date_flags", "ok": "Dated on a working day, not in the future."},
]

ERROR_TO_CHECK = {
    "WRONG_AMOUNT": "po_match", "QUANTITY_NOT_RECEIVED": "goods_received",
    "DUPLICATE_INVOICE": "not_duplicate", "ALREADY_PAID": "not_already_paid",
    "UNKNOWN_VENDOR": "vendor_known", "VAT_ARITHMETIC_ERROR": "vat_math",
    "WRONG_VAT_RATE": "vat_rate", "REVERSE_CHARGE_ERROR": "vat_treatment",
    "INVALID_VAT_NUMBER": "vat_number_valid", "MISSING_MANDATORY_FIELDS": "mandatory_fields",
    "CHANGED_BANK_DETAILS": "bank_details_match", "SANCTIONED_VENDOR": "sanctions",
    "UNUSUAL_AMOUNT": "amount_typical", "PROMPT_INJECTION": "prompt_injection",
}

SEVERITY = {
    "WRONG_AMOUNT": "medium", "QUANTITY_NOT_RECEIVED": "medium", "DUPLICATE_INVOICE": "high",
    "ALREADY_PAID": "high", "UNKNOWN_VENDOR": "high", "VAT_ARITHMETIC_ERROR": "low",
    "WRONG_VAT_RATE": "medium", "REVERSE_CHARGE_ERROR": "medium", "INVALID_VAT_NUMBER": "medium",
    "MISSING_MANDATORY_FIELDS": "medium", "CHANGED_BANK_DETAILS": "high", "SANCTIONED_VENDOR": "high",
    "UNUSUAL_AMOUNT": "medium", "PROMPT_INJECTION": "high",
}

ROLES = [
    {"role": "extractor", "tools": ["convert_document", "get_document_metadata"], "budget": 6},
    {"role": "verifier", "tools": ["get_vendor", "lookup_invoice_number", "find_transactions",
                                    "get_purchase_order", "vendor_payment_summary", "convert_document"], "budget": 24},
    {"role": "reporter", "tools": ["create_review_case", "add_case_note", "export_evidence_pack"], "budget": 4},
]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _obj(x: Any) -> Any:
    """Turn nested dicts into objects with attribute access (like Pydantic models)."""
    if isinstance(x, dict):
        return SimpleNamespace(**{k: _obj(v) for k, v in x.items()})
    if isinstance(x, list):
        return [_obj(v) for v in x]
    return x


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def synth_events(doc: dict, verdict: dict, start: datetime | None = None,
                 inject_refusal: bool = False) -> list[dict]:
    """Build a realistic event stream for one invoice from its verdict.

    Used by fixtures/_generate.py to write fixtures/events/*.jsonl, and by
    verify() for invoices that have no recorded stream.
    """
    start = start or datetime.now(timezone.utc)
    t = 0.0
    seq = 0
    out: list[dict] = []
    doc_id = doc["doc_id"]

    def emit(etype: str, dt: float, **payload: Any) -> None:
        nonlocal t, seq
        t += dt
        seq += 1
        out.append({"type": etype, "doc_id": doc_id, "seq": seq,
                    "ts": (start + timedelta(seconds=t)).isoformat(), **payload})

    def call(role: str, server: str, tool: str, args: dict, rows: int, ms: int, dt: float = 0.4,
             preview: str = "", refused: bool = False, reason: str | None = None) -> None:
        emit("tool_call", dt, role=role, server=server, tool=tool, args=args, rows_returned=rows,
             latency_ms=ms, refused=refused, result_preview=preview, refusal_reason=reason)

    inv = verdict["invoice"]
    ocr = doc.get("read_method") == "ocr"
    chars = 3100 + (int(doc_id[-3:]) * 97) % 900

    emit("stage", 0.0, stage="convert", status="started")
    call("extractor", "documents", "convert_document", {"doc_id": doc_id}, 1, 2400 if ocr else 600, dt=0.6,
         preview=f"{'OCR' if ocr else 'Text layer'}, {chars:,} characters")
    emit("markdown_ready", 2.4 if ocr else 0.8, method="ocr" if ocr else "markitdown",
         language="en", char_count=chars, truncated=False)
    emit("stage", 0.1, stage="convert", status="done")

    emit("stage", 0.2, stage="extract", status="started")
    call("extractor", "documents", "get_document_metadata", {"doc_id": doc_id}, 1, 14,
         preview="1 page, text layer present" if not ocr else "1 page, no text layer")
    if inject_refusal:
        call("extractor", "transactions", "get_vendor", {"name_or_vat_number": inv["supplier_name"]}, 0, 0,
             refused=True, reason="Not on the extractor's allowlist")
    emit("invoice_extracted", 3.5, invoice=inv, low_confidence_fields=verdict.get("low_confidence_fields", []))
    n_fields = 15 - len(verdict.get("low_confidence_fields", []))
    country = {"NL": "the Netherlands", "DE": "Germany", "BE": "Belgium"}.get(inv["supplier_country"], inv["supplier_country"])
    emit("llm_note", 0.3, role="extractor",
         text=f"Read {n_fields} fields from {'a scanned image' if ocr else 'a text PDF'}. Supplier is in {country}; "
              f"{len(inv['lines'])} line item{'s' if len(inv['lines']) != 1 else ''}; total \u20ac{inv['total_incl_vat']}.")
    emit("stage", 0.1, stage="extract", status="done")

    emit("stage", 0.3, stage="checks", status="started")
    for chk in verdict["checks"]:
        meta = next(c for c in CHECKS if c["name"] == chk["name"])
        if chk["status"] != "skipped" and meta["tool"]:
            args = {"vendor_id": "V-" + doc_id[-3:]} if meta["server"] != "tax_rules" else {"invoice": doc_id}
            rows = 0 if chk["name"] in ("not_duplicate", "not_already_paid") and chk["status"] == "pass" else 1
            preview = chk["detail"] if chk["status"] == "fail" else "masked result, " + ("no matches" if rows == 0 else "1 record")
            call("verifier", meta["server"], meta["tool"], args, rows, 8 + (len(meta["tool"]) * 3) % 40,
                 dt=0.5, preview=preview)
        emit("check_result", 0.7, name=chk["name"], server=chk["server"], status=chk["status"],
             detail=chk["detail"], severity=chk.get("severity"))
    emit("stage", 0.2, stage="checks", status="done")

    emit("stage", 0.3, stage="agent", status="started")
    if verdict["verdict"] not in ("MATCHED", "NOT_FOUND"):
        call("verifier", "transactions", "find_transactions",
             {"vendor_id": "V-" + doc_id[-3:], "date_from": "2026-08-01", "date_to": inv["invoice_date"]},
             4, 27, dt=2.5, preview="4 rows of 20 max, IBANs masked")
    emit("llm_note", 3.0, role="verifier", text=verdict["auditor_note"])
    emit("stage", 0.2, stage="agent", status="done")

    emit("stage", 0.2, stage="verdict", status="started")
    emit("verdict", 0.8, verdict=verdict)
    emit("stage", 0.1, stage="verdict", status="done")

    emit("stage", 0.2, stage="report", status="started")
    if verdict.get("review_case_id"):
        call("reporter", "review_cases", "create_review_case", {"invoice_id": doc_id}, 1, 8,
             preview=f"Case {verdict['review_case_id']} opened")
    emit("stage", 0.3, stage="report", status="done")
    return out


# --------------------------------------------------------------------------
# In-memory state, loaded from fixtures once per process
# --------------------------------------------------------------------------
class _State:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {d["doc_id"]: d for d in _read_json("documents.json")}
        self.verdicts: dict[str, dict] = {
            p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (FIXTURES / "verdicts").glob("*.json")}
        self.cases: dict[str, dict] = {c["case_id"]: c for c in _read_json("cases.json")}
        self.audit: list[dict] = [json.loads(line) for line in
                                  (FIXTURES / "audit_log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        self.evals: list[dict] = _read_json("eval_reports.json")
        self.uploads: dict[str, Path] = {}
        self.running: set[str] = set()


_STATE: _State | None = None


def _st() -> _State:
    """Load fixtures on first use, so importing this module never fails."""
    global _STATE
    if _STATE is None:
        _STATE = _State()
    return _STATE


# --------------------------------------------------------------------------
# Service functions. Same signatures as agent/service.py (todo.md section 1).
# --------------------------------------------------------------------------
async def list_documents() -> list[Any]:
    return [_obj(d) for d in sorted(_st().docs.values(), key=lambda d: d["doc_id"])]


async def upload_document(filename: str, data: bytes) -> Any:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    next_num = max(int(d[-3:]) for d in _st().docs) + 1
    doc_id = f"INV-{next_num:03d}"
    path = WORK_DIR / f"{doc_id}_{Path(filename).name}"
    path.write_bytes(data)
    _st().uploads[doc_id] = path
    suffix = path.suffix.lower().lstrip(".")
    doc = {"doc_id": doc_id, "filename": Path(filename).name,
           "mime_type": "application/pdf" if suffix == "pdf" else f"image/{'jpeg' if suffix == 'jpg' else suffix}",
           "size_bytes": len(data), "pages": 1, "read_method": "markitdown" if suffix == "pdf" else "ocr",
           "vendor_name": None, "vendor_country": None, "invoice_number": None, "invoice_date": None,
           "amount_total": None, "currency": "EUR", "status": "NOT_VERIFIED", "verdict": None,
           "main_finding": None, "risk_flags": [], "uploaded_at": _now()}
    _st().docs[doc_id] = doc
    return _obj(doc)


def get_document_file(doc_id: str) -> Path:
    """Path to the original file. Fixture invoices have no file, so the UI
    falls back to drawing the extracted fields as a page."""
    return _st().uploads.get(doc_id, FIXTURES / "invoices" / f"{doc_id}.pdf")


def _verdict_for(doc_id: str) -> dict:
    if doc_id in _st().verdicts:
        return copy.deepcopy(_st().verdicts[doc_id])
    doc = _st().docs[doc_id]
    inv = {"supplier_name": "Unknown supplier", "supplier_country": "NL", "supplier_vat_number": None,
           "supplier_kvk_number": None, "supplier_address": None, "buyer_name": None, "buyer_vat_number": None,
           "buyer_address": None, "invoice_number": None, "invoice_date": None, "supply_date": None,
           "po_number": None, "currency": "EUR", "lines": [], "vat_lines": [], "total_excl_vat": "0.00",
           "total_vat": "0.00", "total_incl_vat": "0.00", "iban_last4": None, "reverse_charge_text": None}
    checks = [{"name": c["name"], "server": c["server"], "status": "skipped",
               "detail": "Mock service: there is no fixture for this uploaded file.", "severity": None} for c in CHECKS]
    return {"doc_id": doc_id, "invoice_id": doc_id, "verdict": "NEEDS_REVIEW",
            "reason": "Mock service has no fixture for this file", "confidence": 0.0,
            "auditor_note": f"{doc['filename']} was uploaded to the mock service, which only knows the 20 test invoices. "
                            "Switch to INVOICE_SERVICE=real to verify it for real.",
            "reasoning": "", "discrepancies": [], "risk_flags": [], "checks": checks,
            "evidence_transaction_ids": [], "evidence": [], "vat": None, "bank": None,
            "low_confidence_fields": [], "invoice": inv, "review_case_id": None,
            "model": "mock", "seconds": 0, "tool_calls": 0, "created_at": _now()}


async def verify(doc_id: str) -> AsyncIterator[Any]:
    """Stream events for one invoice. The last event is the verdict (plus report stages)."""
    if doc_id not in _st().docs:
        yield _obj({"type": "error", "doc_id": doc_id, "seq": 1, "ts": _now(),
                    "message": f"No document {doc_id} in the inbox.", "stage": "convert"})
        return
    doc = _st().docs[doc_id]
    verdict = _verdict_for(doc_id)
    recorded = FIXTURES / "events" / f"{doc_id}.jsonl"
    if recorded.exists():
        events = [json.loads(line) for line in recorded.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        events = synth_events(doc, verdict)

    doc["status"] = "VERIFYING"
    _st().running.add(doc_id)
    run_start = datetime.now(timezone.utc)
    first_ts = datetime.fromisoformat(events[0]["ts"])
    prev_ts = first_ts
    try:
        for ev in events:
            ts = datetime.fromisoformat(ev["ts"])
            gap = min((ts - prev_ts).total_seconds(), 4.0) / max(SPEED, 0.01)
            prev_ts = ts
            if gap > 0:
                await asyncio.sleep(gap)
            ev = dict(ev)
            ev["ts"] = (run_start + (ts - first_ts) / max(SPEED, 0.01)).isoformat()
            if ev["type"] == "tool_call":
                _st().audit.append({k: ev.get(k) for k in ("doc_id", "ts", "role", "server", "tool", "args",
                                                           "rows_returned", "latency_ms", "refused",
                                                           "result_preview", "refusal_reason")}
                                | {"seq": len(_st().audit) + 1})
            if ev["type"] == "verdict":
                v = ev["verdict"]
                doc.update(status="VERIFIED", verdict=v["verdict"], risk_flags=list(v["risk_flags"]),
                           main_finding=(v["risk_flags"][0] if v["risk_flags"] else "All checks pass"),
                           vendor_name=doc.get("vendor_name") or v["invoice"]["supplier_name"])
                _st().verdicts[doc_id] = v
                _ensure_case(doc, v)
            yield _obj(ev)
    except asyncio.CancelledError:
        doc["status"] = "NOT_VERIFIED"
        raise
    finally:
        _st().running.discard(doc_id)
        if doc["status"] == "VERIFYING":
            doc["status"] = "NOT_VERIFIED"


def _ensure_case(doc: dict, v: dict) -> None:
    cid = v.get("review_case_id")
    if not cid or cid in _st().cases:
        return
    flag = v["risk_flags"][0] if v["risk_flags"] else None
    passed = len([c for c in v["checks"] if c["status"] == "pass"])
    _st().cases[cid] = {"case_id": cid, "doc_id": doc["doc_id"], "vendor_name": doc["vendor_name"],
                     "error_type": flag, "severity": SEVERITY.get(flag, "high"), "verdict": v["verdict"],
                     "status": "OPEN", "created_at": _now(), "summary": v["auditor_note"],
                     "amount": doc["amount_total"], "currency": "EUR", "checks_passed": passed,
                     "checks_total": len(v["checks"]),
                     "notes": [{"ts": _now(), "author": "Reporter agent", "text": f"Opened case from verdict {v['verdict']}."}],
                     "decision": None}


async def get_verdict(doc_id: str) -> Any | None:
    doc = _st().docs.get(doc_id)
    if not doc or doc["status"] != "VERIFIED" or doc_id not in _st().verdicts:
        return None
    return _obj(_st().verdicts[doc_id])


async def list_cases(status: str | None = None) -> list[Any]:
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    cases = [c for c in _st().cases.values() if status in (None, "ALL") or c["status"] == status]
    cases.sort(key=lambda c: (sev_rank.get(c["severity"], 3), c["status"] != "OPEN", c["case_id"]))
    return [_obj(c) for c in cases]


async def get_case(case_id: str) -> Any:
    return _obj(_st().cases[case_id])


async def set_case_status(case_id: str, status: str, reviewer: str, reason: str) -> Any:
    """Humans only. Never exposed to agents."""
    if not reason or not reason.strip():
        raise ValueError("A reason is required.")
    if status not in ("APPROVED", "REJECTED", "ESCALATED"):
        raise ValueError(f"Unknown status {status}")
    case = _st().cases[case_id]
    ts = _now()
    case["status"] = status
    case["decision"] = {"status": status, "reviewer": reviewer, "reason": reason.strip(), "ts": ts}
    case["notes"].append({"ts": ts, "author": reviewer, "text": f"{status.title()}: {reason.strip()}"})
    return _obj(case)


async def add_case_note(case_id: str, text: str, author: str) -> Any:
    """Not yet in the contract: the review page needs it for 'Add a note' (U5)."""
    if not text.strip():
        raise ValueError("The note is empty.")
    _st().cases[case_id]["notes"].append({"ts": _now(), "author": author, "text": text.strip()})
    return _obj(_st().cases[case_id])


async def export_evidence_pack(case_id: str, fmt: str = "pdf") -> Path:
    case = _st().cases[case_id]
    v = _st().verdicts.get(case["doc_id"], {})
    out_dir = WORK_DIR / "outbox"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [("Case", case_id), ("Invoice", case["doc_id"]), ("Vendor", case["vendor_name"]),
            ("Verdict", case["verdict"]), ("Status", case["status"]), ("Summary", case["summary"])]
    rows += [(f"Check: {c['name']}", f"{c['status']}: {c['detail']}") for c in v.get("checks", [])]
    rows += [(f"Note {n['ts'][:16]}", f"{n['author']}: {n['text']}") for n in case["notes"]]
    if fmt == "xlsx":
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "Evidence"
            for r in rows:
                ws.append(list(r))
            path = out_dir / f"evidence_{case_id}.xlsx"
            wb.save(path)
            return path
        except ImportError:
            path = out_dir / f"evidence_{case_id}.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(rows)
            return path
    path = out_dir / f"evidence_{case_id}.pdf"
    path.write_bytes(_tiny_pdf([f"Evidence pack {case_id} (mock)"] + [f"{a}: {b}" for a, b in rows]))
    return path


def _tiny_pdf(lines: list[str]) -> bytes:
    """A minimal one-page PDF, so the mock can export without extra libraries."""
    def esc(s: str) -> str:
        s = s.encode("latin-1", "replace").decode("latin-1")
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    text = ["BT", "/F1 10 Tf", "40 800 Td", "14 TL"]
    for line in lines[:52]:
        text.append(f"({esc(line[:110])}) Tj T*")
    text.append("ET")
    stream = "\n".join(text).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


async def get_audit_log(doc_id: str | None = None, role: str | None = None,
                        server: str | None = None) -> list[Any]:
    rows = [a for a in _st().audit
            if (doc_id is None or a["doc_id"] == doc_id)
            and (role is None or a["role"] == role)
            and (server is None or a["server"] == server)]
    return [_obj(a) for a in rows]


async def run_eval(model: str, runs_per_invoice: int = 3) -> AsyncIterator[Any]:
    """Pretend to run the eval: streams progress, then stores a new sample report."""
    base = copy.deepcopy(_st().evals[0])
    docs = sorted(_st().docs)[:20]
    total = len(docs) * runs_per_invoice
    done = 0
    started = _now()
    for run in range(1, runs_per_invoice + 1):
        for doc_id in docs:
            await asyncio.sleep(0.12 / max(SPEED, 0.01))
            done += 1
            yield _obj({"done": done, "total": total, "doc_id": doc_id, "run": run, "model": model,
                        "finished": False, "report_id": None, "message": f"Run {run} of {runs_per_invoice}: {doc_id}"})
    report = base | {"report_id": f"EVAL-{len(_st().evals) + 1:04d}", "model": model,
                     "runs_per_invoice": runs_per_invoice, "started_at": started, "finished_at": _now(),
                     "duration_s": int(total * 47), "sample": True}
    for row in report["rows"]:
        row["runs"] = (row["runs"] * runs_per_invoice)[:runs_per_invoice]
    _st().evals.append(report)
    yield _obj({"done": total, "total": total, "doc_id": None, "run": runs_per_invoice, "model": model,
                "finished": True, "report_id": report["report_id"], "message": "Finished"})


async def list_eval_runs() -> list[Any]:
    return [_obj(r) for r in reversed(_st().evals)]


async def system_status() -> Any:
    return _obj({"ollama_up": True, "model": "qwen3:8b", "servers_up": 6, "servers_total": 6,
                 "service_mode": "mock", "roles": ROLES})
