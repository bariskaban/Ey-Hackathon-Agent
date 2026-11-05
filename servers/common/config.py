"""Paths and constants shared by the data scripts, the MCP servers and the agent.

Every path can be overridden with an environment variable, which keeps tests
and demos away from the real data.
"""

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = Path(os.environ.get("INVOICE_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("INVOICE_DB_PATH", DATA_DIR / "db.sqlite"))
INBOX_DIR = Path(os.environ.get("INVOICE_INBOX_DIR", DATA_DIR / "inbox"))
INVOICES_DIR = DATA_DIR / "invoices"
RUNS_DIR = DATA_DIR / "runs"
OUTBOX_DIR = DATA_DIR / "outbox"
AUDIT_LOG_PATH = Path(os.environ.get("INVOICE_AUDIT_LOG", DATA_DIR / "audit.jsonl"))

# The synthetic data is generated "as of" this date so every run is identical.
AS_OF = date(2026, 11, 5)


def db_path() -> Path:
    """The database path, read at call time so tests can point at a temporary copy."""
    return Path(os.environ.get("INVOICE_DB_PATH", DB_PATH))
