"""Test doubles shared by the pipeline and service tests."""

import contextlib
from types import SimpleNamespace

from agent import extract
from agent.roles import ROLES
from servers.documents import tools as document_tools
from servers.transactions import tools as transaction_tools

TOOLS = {name: ("transactions", getattr(transaction_tools, name)) for name in (
    "get_vendor", "lookup_invoice_number", "find_transactions", "get_purchase_order", "vendor_payment_summary")}
TOOLS.update({name: ("documents", getattr(document_tools, name)) for name in (
    "list_documents", "convert_document", "get_document_metadata")})

class DirectGateway:
    """Same contract as the real gateway (role allowlists, result dict), calling the tools in-process."""

    def __init__(self):
        self.calls = []

    async def call(self, role, tool, args=None, invoice_id=None):
        server, fn = TOOLS[tool]
        self.calls.append((role, tool))
        base = {"server": server, "latency_ms": 1, "rows_returned": 0}
        if server not in ROLES[role].servers:
            return {**base, "ok": False, "data": None, "error": "refused", "refused": True}
        try:
            return {**base, "ok": True, "data": fn(**(args or {})), "error": None, "refused": False}
        except ValueError as exc:
            return {**base, "ok": False, "data": None, "error": str(exc), "refused": False}

    def llm_tools(self, role):
        return []


class StubLLM:
    """Returns the ground-truth invoice as if it were the model's extraction."""

    def __init__(self, invoice_json: str):
        self.invoice_json = invoice_json
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return SimpleNamespace(message=SimpleNamespace(content=self.invoice_json, tool_calls=None))


@contextlib.contextmanager
def trusting_extraction():
    """The stub returns the true values, which a noisy OCR scan may not contain verbatim, so the
    source-text confidence check would flag them. Switch it off for tests about something else."""
    original = extract.find_low_confidence_fields
    extract.find_low_confidence_fields = lambda invoice, markdown: []
    try:
        yield
    finally:
        extract.find_low_confidence_fields = original


