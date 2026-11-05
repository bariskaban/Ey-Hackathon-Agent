"""Shared contract between the backend and the UI.

This module is the single source of truth for names used across the project.
The Invoice models are written (they are what the extractor produces). Verdict, Event,
ReviewCase and the rest are still to be written; see todo.md section 1.
"""

import re
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, WithJsonSchema


def _to_decimal(value):
    """Accept numbers and printed amounts such as 'EUR 1,234.56' or '21%'."""
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("a boolean is not a number")  # noqa: TRY004 - pydantic validators must raise ValueError
    if isinstance(value, int | float):
        return Decimal(str(value))
    if isinstance(value, str):
        text = re.sub(r"[^\d,.\-]", "", value)
        if not text:
            return None
        if "," in text and "." in text:
            if text.rfind(".") > text.rfind(","):
                text = text.replace(",", "")
            else:
                text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            text = text.replace(",", ".")
        try:
            return Decimal(text)
        except InvalidOperation:
            raise ValueError(f"not a number: {value!r}") from None
    raise ValueError(f"not a number: {value!r}")


# Money and quantities are Decimals in Python and plain numbers in the JSON schema the model sees.
Number = Annotated[Decimal | None, BeforeValidator(_to_decimal),
                   WithJsonSchema({"anyOf": [{"type": "number"}, {"type": "null"}]})]


class Party(BaseModel):
    name: str | None = None
    address: str | None = None
    country: str | None = Field(None, description="ISO 3166 alpha-2 code, e.g. NL, BE, DE")
    vat_number: str | None = None
    kvk_number: str | None = Field(None, description="Dutch Chamber of Commerce number")
    iban: str | None = None


class InvoiceLine(BaseModel):
    sku: str | None = Field(None, description="Item or product code")
    description: str | None = None
    quantity: Number = None
    unit_price: Number = Field(None, description="Price per unit excluding VAT")
    vat_rate: Number = Field(None, description="VAT percentage on this line, e.g. 21 or 9")
    line_total: Number = Field(None, description="Quantity times unit price, excluding VAT")


class VatLine(BaseModel):
    rate: Number = Field(None, description="VAT percentage, e.g. 21")
    base: Number = Field(None, description="Amount excluding VAT at this rate")
    vat: Number = Field(None, description="VAT amount at this rate")


class Invoice(BaseModel):
    """An invoice as printed. Fields that are not on the document are null, never guessed."""

    invoice_number: str | None = None
    invoice_date: str | None = Field(None, description="ISO date YYYY-MM-DD")
    supply_date: str | None = Field(None, description="Date of supply or delivery, ISO date")
    due_date: str | None = Field(None, description="Payment due date, ISO date")
    po_number: str | None = Field(None, description="Purchase order reference")
    currency: str | None = Field("EUR", description="ISO 4217 code")
    supplier: Party = Field(default_factory=Party, description="The party that issued the invoice and is paid")
    buyer: Party = Field(default_factory=Party, description="The party billed")
    lines: list[InvoiceLine] = Field(default_factory=list)
    vat_breakdown: list[VatLine] = Field(default_factory=list, description="One entry per VAT rate")
    total_excl_vat: Number = None
    total_vat: Number = None
    total_incl_vat: Number = None
    reverse_charge: bool = Field(False, description="True only if the invoice states reverse charge")
    notes: str | None = Field(None, description="Free-text remarks on the invoice, copied verbatim")


class VerdictStatus(str, Enum):  # the `verdict` field of the Verdict model (to be written)
    MATCHED = "MATCHED"  # all checks pass
    PARTIAL_MATCH = "PARTIAL_MATCH"  # matches, only low-severity discrepancies
    MISMATCH = "MISMATCH"  # at least one medium or high discrepancy
    NOT_FOUND = "NOT_FOUND"  # no vendor, PO or transaction to compare against
    NEEDS_REVIEW = "NEEDS_REVIEW"  # the system can't decide; a human must


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class ErrorType(str, Enum):
    WRONG_AMOUNT = "WRONG_AMOUNT"
    QUANTITY_NOT_RECEIVED = "QUANTITY_NOT_RECEIVED"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    ALREADY_PAID = "ALREADY_PAID"
    UNKNOWN_VENDOR = "UNKNOWN_VENDOR"
    VAT_ARITHMETIC_ERROR = "VAT_ARITHMETIC_ERROR"
    WRONG_VAT_RATE = "WRONG_VAT_RATE"
    REVERSE_CHARGE_ERROR = "REVERSE_CHARGE_ERROR"
    INVALID_VAT_NUMBER = "INVALID_VAT_NUMBER"
    MISSING_MANDATORY_FIELDS = "MISSING_MANDATORY_FIELDS"
    CHANGED_BANK_DETAILS = "CHANGED_BANK_DETAILS"
    SANCTIONED_VENDOR = "SANCTIONED_VENDOR"
    UNUSUAL_AMOUNT = "UNUSUAL_AMOUNT"  # also covers split invoices; say which in `detail`
    PROMPT_INJECTION = "PROMPT_INJECTION"


# Default severity per error type. A check may override it, for example by amount.
DEFAULT_SEVERITY: dict[ErrorType, Severity] = {
    ErrorType.WRONG_AMOUNT: Severity.MEDIUM,
    ErrorType.QUANTITY_NOT_RECEIVED: Severity.MEDIUM,
    ErrorType.DUPLICATE_INVOICE: Severity.HIGH,
    ErrorType.ALREADY_PAID: Severity.HIGH,
    ErrorType.UNKNOWN_VENDOR: Severity.HIGH,
    ErrorType.VAT_ARITHMETIC_ERROR: Severity.LOW,
    ErrorType.WRONG_VAT_RATE: Severity.MEDIUM,
    ErrorType.REVERSE_CHARGE_ERROR: Severity.MEDIUM,
    ErrorType.INVALID_VAT_NUMBER: Severity.MEDIUM,
    ErrorType.MISSING_MANDATORY_FIELDS: Severity.MEDIUM,
    ErrorType.CHANGED_BANK_DETAILS: Severity.HIGH,
    ErrorType.SANCTIONED_VENDOR: Severity.HIGH,
    ErrorType.UNUSUAL_AMOUNT: Severity.MEDIUM,
    ErrorType.PROMPT_INJECTION: Severity.HIGH,
}


# --------------------------------------------------------------------------- documents


class DocumentInfo(BaseModel):
    doc_id: str
    filename: str
    type: str = Field(description="MIME type")
    size_bytes: int
    pages: int
    verdict: VerdictStatus | None = Field(None, description="Latest verdict, if the document was verified")


# --------------------------------------------------------------------------- verdict


class Discrepancy(BaseModel):
    type: ErrorType
    field: str | None = Field(None, description="Where on the invoice, e.g. 'supplier.iban' or 'lines[1].quantity'")
    expected: str | None = None
    found: str | None = None
    severity: Severity
    detail: str | None = None


class CheckResult(BaseModel):
    name: str = Field(description="e.g. vendor_lookup, duplicate_invoice, po_amount_match")
    server: str = Field(description="The MCP server used, or 'host' for checks run by the agent code")
    status: CheckStatus
    detail: str = ""
    severity: Severity | None = Field(None, description="Set when status is fail")
    error_type: ErrorType | None = Field(None, description="Set when status is fail")


class Verdict(BaseModel):
    invoice_id: str
    verdict: VerdictStatus
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    evidence_transaction_ids: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    review_case_id: str | None = None
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    reasoning: str = ""
    needs_review_reasons: list[str] = Field(default_factory=list, description="Why a person must look, if NEEDS_REVIEW")
    created_at: str | None = None


# --------------------------------------------------------------------------- events (streamed by verify())


class Stage(str, Enum):
    CONVERT = "convert"
    EXTRACT = "extract"
    CHECKS = "checks"
    AGENT = "agent"
    VERDICT = "verdict"
    REPORT = "report"


class StageStatus(str, Enum):
    STARTED = "started"
    DONE = "done"
    FAILED = "failed"


class EventBase(BaseModel):
    seq: int = Field(description="1, 2, 3 ... within one verify() run")
    ts: str = Field(description="ISO-8601 UTC")
    doc_id: str


class StageEvent(EventBase):
    type: Literal["stage"] = "stage"
    stage: Stage
    status: StageStatus


class MarkdownReadyEvent(EventBase):
    type: Literal["markdown_ready"] = "markdown_ready"
    method: str = Field(description="markitdown, pdf_text or ocr")
    language: str
    char_count: int
    truncated: bool


class InvoiceExtractedEvent(EventBase):
    type: Literal["invoice_extracted"] = "invoice_extracted"
    invoice: Invoice
    low_confidence_fields: list[str] = Field(default_factory=list)


class CheckResultEvent(EventBase):
    type: Literal["check_result"] = "check_result"
    name: str
    server: str
    status: CheckStatus
    detail: str = ""
    severity: Severity | None = None
    error_type: ErrorType | None = None


class ToolCallEvent(EventBase):
    type: Literal["tool_call"] = "tool_call"
    role: str
    server: str | None
    tool: str
    args: dict
    rows_returned: int | None = None
    latency_ms: int = 0
    refused: bool = False
    error: str | None = None


class LlmNoteEvent(EventBase):
    type: Literal["llm_note"] = "llm_note"
    role: str
    text: str


class VerdictEvent(EventBase):
    type: Literal["verdict"] = "verdict"
    verdict: Verdict


class ErrorEvent(EventBase):
    type: Literal["error"] = "error"
    message: str
    stage: Stage | None = None


Event = Annotated[
    StageEvent | MarkdownReadyEvent | InvoiceExtractedEvent | CheckResultEvent | ToolCallEvent
    | LlmNoteEvent | VerdictEvent | ErrorEvent,
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------- audit, cases, eval, status


class AuditEntry(BaseModel):
    ts: str
    invoice_id: str | None
    role: str
    server: str | None
    tool: str
    args: dict
    result_size: int
    rows_returned: int | None
    latency_ms: int
    refused: bool = False
    reason: str | None = None
    error: bool = False


class CaseStatus(str, Enum):
    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class CaseNote(BaseModel):
    ts: str
    author: str = Field(description="'agent' or the name of a person")
    text: str


class ReviewCase(BaseModel):
    case_id: str
    invoice_id: str
    verdict: VerdictStatus
    status: CaseStatus = CaseStatus.OPEN
    error_types: list[ErrorType] = Field(default_factory=list)
    top_severity: Severity | None = None
    evidence: dict = Field(default_factory=dict, description="The Verdict as a dict, plus the extracted invoice")
    notes: list[CaseNote] = Field(default_factory=list)
    created_at: str
    status_changed_by: str | None = None
    status_reason: str | None = None


class EvalProgress(BaseModel):
    run_id: str
    model: str
    invoice_id: str | None = None
    run_index: int | None = None
    completed: int
    total: int
    last_result: str | None = Field(None, description="'pass' or 'fail' for the run that just finished")
    finished: bool = False


class EvalInvoiceResult(BaseModel):
    invoice_id: str
    expected_verdict: VerdictStatus
    expected_error: str = Field(description="An ErrorType name or CLEAN")
    verdicts: list[VerdictStatus] = Field(description="One verdict per run")
    error_caught: list[bool] = Field(description="Whether the planted error was found, per run")
    caught_by: list[str] = Field(default_factory=list, description="Checks that failed")
    consistent: bool = Field(description="Every run gave the same verdict and the same findings")
    outcome: Literal["pass", "safe_fail", "fail"] = Field(
        description="pass: right verdict and error every run. safe_fail: a scan that could not be read "
                    "was sent to a person instead of approved or rejected. fail: anything else.")
    passed: bool = Field(description="outcome == 'pass'")
    seconds: float = Field(description="Mean seconds per run")
    tool_calls: int = Field(description="Mean tool calls per run")
    scanned: bool = False
    note: str | None = Field(None, description="Why an invoice did not pass, in one sentence")


class EvalReport(BaseModel):
    run_id: str
    model: str
    created_at: str
    runs_per_invoice: int
    invoices: list[EvalInvoiceResult]
    passed: int
    safe_failed: int = 0
    failed: int = 0
    total: int
    consistent: int
    mean_seconds: float
    used_agent: bool = True


class SystemStatus(BaseModel):
    ollama_up: bool
    model: str
    model_available: bool
    models: list[str] = Field(default_factory=list)
    servers: dict[str, bool] = Field(default_factory=dict, description="MCP server name -> started")
    database_ok: bool
    tesseract_ok: bool
    documents_in_inbox: int = 0
