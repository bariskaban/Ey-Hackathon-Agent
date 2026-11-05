"""The only write access in the system. Append-only: cases and notes can be added, never changed or
deleted, and the agent can never approve or close a case (only a person sets the status, in the UI)."""

import json

from agent.schemas import VerdictStatus
from servers.common.config import DATA_DIR
from servers.common.db import read_only, writable
from servers.review_cases import evidence as evidence_mod
from servers.review_cases import store

MAX_EVIDENCE_BYTES = 400_000
MAX_NOTE_CHARS = 2000


def create_review_case(invoice_id: str, verdict: str, evidence: dict) -> dict:
    """Open a review case for an invoice. `evidence` holds the verdict, the extracted invoice and the document info.

    If the invoice already has an open case, no second case is created: a note is added to it instead.
    """
    if verdict not in {v.value for v in VerdictStatus}:
        raise ValueError(f"verdict must be one of {', '.join(v.value for v in VerdictStatus)}")
    if not invoice_id.strip():
        raise ValueError("invoice_id must not be empty")
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")  # noqa: TRY004 - ValueError becomes a readable tool error
    payload = json.dumps(evidence, ensure_ascii=False, default=str)
    if len(payload.encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise ValueError("evidence is too large")
    with writable() as con:
        open_case = con.execute("SELECT case_id FROM review_cases WHERE invoice_id = ? AND status = 'open' "
                                "ORDER BY created_at DESC LIMIT 1", (invoice_id,)).fetchone()
        if open_case:
            store.add_note(con, open_case["case_id"], "agent", f"Invoice verified again: verdict {verdict}.")
            return {"case_id": open_case["case_id"], "created": False, "status": "open"}
        case_id = store.next_case_id(con)
        con.execute("INSERT INTO review_cases (case_id, invoice_id, verdict, evidence_json, created_at, status) "
                    "VALUES (?, ?, ?, ?, ?, 'open')", (case_id, invoice_id, verdict, payload, store.now()))
        return {"case_id": case_id, "created": True, "status": "open"}


def add_case_note(case_id: str, note: str) -> dict:
    """Add a note to a case (append-only). Returns the updated case."""
    text = (note or "").strip()
    if not text:
        raise ValueError("note must not be empty")
    if len(text) > MAX_NOTE_CHARS:
        raise ValueError(f"note is too long (limit {MAX_NOTE_CHARS} characters)")
    with writable() as con:
        store.get_case(con, case_id)  # raises for an unknown case
        store.add_note(con, case_id, "agent", text)
        return _summary(store.get_case(con, case_id))


def export_evidence_pack(case_id: str, fmt: str = "pdf") -> dict:
    """Write the case's evidence pack (pdf or xlsx) to the outbox and return where it is."""
    with read_only() as con:
        case = store.get_case(con, case_id)
    path = evidence_mod.export(case, fmt)
    return {"case_id": case_id, "format": fmt, "path": path.relative_to(DATA_DIR).as_posix(),
            "size_bytes": path.stat().st_size}


def _summary(case: dict) -> dict:
    """What the agent sees of a case: no raw evidence blob."""
    return {key: case[key] for key in ("case_id", "invoice_id", "verdict", "status", "error_types",
                                       "top_severity", "notes", "created_at")}
