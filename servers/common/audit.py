"""Append-only audit log (JSONL). The gateway writes one entry for every tool call."""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from servers.common.config import AUDIT_LOG_PATH

_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or AUDIT_LOG_PATH)

    def write(self, *, invoice_id: str | None, role: str, server: str | None, tool: str, args: dict,
              result_size: int, rows_returned: int | None, latency_ms: int, refused: bool = False,
              reason: str | None = None, error: bool = False) -> dict:
        entry = {
            "ts": now_iso(),
            "invoice_id": invoice_id,
            "role": role,
            "server": server,
            "tool": tool,
            "args": args,
            "result_size": result_size,
            "rows_returned": rows_returned,
            "latency_ms": latency_ms,
            "refused": refused,
            "reason": reason,
            "error": error,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


def read_entries(path: Path | None = None, *, invoice_id: str | None = None, role: str | None = None,
                 server: str | None = None) -> list[dict]:
    path = Path(path or AUDIT_LOG_PATH)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if invoice_id and entry["invoice_id"] != invoice_id:
            continue
        if role and entry["role"] != role:
            continue
        if server and entry["server"] != server:
            continue
        entries.append(entry)
    return entries
