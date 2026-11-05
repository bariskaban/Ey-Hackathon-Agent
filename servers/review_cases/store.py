"""Reading and writing review cases. Shared by the review_cases server (agent side: create, add
notes) and the service (human side: list, read, set the final status). The database triggers keep
evidence and notes append-only; only a person ever changes a case's status."""

import json
from datetime import UTC, datetime

from agent.schemas import CaseStatus

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def next_case_id(con) -> str:
    count = con.execute("SELECT COUNT(*) FROM review_cases").fetchone()[0]  # cases are never deleted
    return f"RC-{count + 1:04d}"


def case_view(con, row) -> dict:
    """A review case as a plain dict with the fields of agent.schemas.ReviewCase."""
    evidence = json.loads(row["evidence_json"])
    discrepancies = (evidence.get("verdict") or {}).get("discrepancies") or []
    types = list(dict.fromkeys(d["type"] for d in discrepancies))
    top = max((d["severity"] for d in discrepancies), key=SEVERITY_RANK.get, default=None)
    notes = con.execute("SELECT ts, author, text FROM case_notes WHERE case_id = ? ORDER BY note_id",
                        (row["case_id"],)).fetchall()
    return {
        "case_id": row["case_id"], "invoice_id": row["invoice_id"], "verdict": row["verdict"],
        "status": row["status"], "error_types": types, "top_severity": top, "evidence": evidence,
        "notes": [dict(n) for n in notes], "created_at": row["created_at"],
        "status_changed_by": row["status_changed_by"], "status_reason": row["status_reason"],
    }


def get_case(con, case_id: str) -> dict:
    row = con.execute("SELECT * FROM review_cases WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown case_id {case_id!r}")
    return case_view(con, row)


def list_cases(con, status: str | None = None) -> list[dict]:
    if status is not None and status not in {s.value for s in CaseStatus}:
        raise ValueError(f"status must be one of {', '.join(s.value for s in CaseStatus)}")
    sql, params = "SELECT * FROM review_cases", []
    if status:
        sql, params = sql + " WHERE status = ?", [status]
    cases = [case_view(con, r) for r in con.execute(sql + " ORDER BY created_at DESC, case_id DESC", params)]
    # most severe first, then newest first (the sort above is stable)
    cases.sort(key=lambda c: -SEVERITY_RANK.get(c["top_severity"] or "", -1))
    return cases


def add_note(con, case_id: str, author: str, text: str) -> None:
    con.execute("INSERT INTO case_notes (case_id, ts, author, text) VALUES (?, ?, ?, ?)",
                (case_id, now(), author, text))


def set_status(con, case_id: str, status: str, reviewer: str, reason: str) -> dict:
    """Human decision. Never reachable from an agent: it is not an MCP tool."""
    if status not in {s.value for s in CaseStatus}:
        raise ValueError(f"status must be one of {', '.join(s.value for s in CaseStatus)}")
    if not reviewer.strip() or not reason.strip():
        raise ValueError("a reviewer name and a reason are required")
    get_case(con, case_id)
    con.execute("UPDATE review_cases SET status = ?, status_changed_by = ?, status_changed_at = ?, "
                "status_reason = ? WHERE case_id = ?", (status, reviewer.strip(), now(), reason.strip(), case_id))
    add_note(con, case_id, reviewer.strip(), f"Status set to {status}: {reason.strip()}")
    return get_case(con, case_id)
