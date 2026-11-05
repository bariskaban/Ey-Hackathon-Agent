"""Test setup: generate a fresh synthetic dataset in a temporary folder and point the project at it.

The environment variables must be set before any project module is imported, because the
config module reads them at import time. The MCP servers started by the gateway inherit them.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_DATA_DIR = Path(tempfile.mkdtemp(prefix="invoice-test-data-"))
os.environ["INVOICE_DATA_DIR"] = str(_DATA_DIR)
os.environ["INVOICE_DB_PATH"] = str(_DATA_DIR / "db.sqlite")
os.environ["INVOICE_AUDIT_LOG"] = str(_DATA_DIR / "audit.jsonl")

subprocess.run([sys.executable, str(ROOT / "data" / "generate.py")], check=True, cwd=ROOT,
               stdout=subprocess.DEVNULL)


@pytest.fixture(scope="session")
def ground_truth() -> list[dict]:
    return json.loads((_DATA_DIR / "invoices" / "ground_truth.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def case(ground_truth):
    """Look up the test invoice for a planted-error case, e.g. case('duplicate')."""
    by_case = {g["case"]: g for g in ground_truth}
    return lambda key: by_case[key]


@pytest.fixture(scope="session")
def inbox(ground_truth):
    """Render the 20 invoice PDFs into the test data folder and put them in a test inbox."""
    from servers.documents import manifest

    subprocess.run([sys.executable, str(ROOT / "data" / "render_pdfs.py")], check=True, cwd=ROOT,
                   stdout=subprocess.DEVNULL)
    folder = _DATA_DIR / "inbox"
    folder.mkdir()
    for entry in ground_truth:
        shutil.copy2(_DATA_DIR / "invoices" / entry["file"], folder / entry["file"])
    os.environ["INVOICE_INBOX_DIR"] = str(folder)
    os.environ["INVOICE_CACHE_DIR"] = str(_DATA_DIR / "cache")
    result = manifest.sync_inbox()
    yield folder, result
    os.environ.pop("INVOICE_INBOX_DIR")
    os.environ.pop("INVOICE_CACHE_DIR")
