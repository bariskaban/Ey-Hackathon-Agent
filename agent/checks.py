"""Fixed checks, run by code (not by the model), and the deterministic verdict rules.

Each check calls MCP tools through the gateway and returns a CheckOutcome: one CheckResult plus
any discrepancies and evidence. The verdict is then decided by `decide_verdict` from fixed rules,
so the same invoice gives the same verdict on every run. The language model may add reasoning and
may escalate to NEEDS_REVIEW, but it can never lower a verdict.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from agent.schemas import (
    DEFAULT_SEVERITY,
    CheckResult,
    CheckStatus,
    Discrepancy,
    ErrorType,
    Invoice,
    Severity,
    VerdictStatus,
)

AMOUNT_TOLERANCE_PCT = Decimal("0.02")  # invoice may differ from the PO by 2% ...
AMOUNT_TOLERANCE_MIN = Decimal("1.00")  # ... or EUR 1, whichever is larger
PAYMENT_MATCH_WINDOW_DAYS = 120

# Fields that must be right for an automatic verdict to be trustworthy. If the extractor was not
# sure about one of these, a person has to look.
CRITICAL_FIELDS = {"invoice_number", "total_incl_vat", "total_excl_vat", "supplier.name",
                   "supplier.vat_number", "supplier.iban"}

INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|rules|prompts?)",
    r"disregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above)",
    r"(?:note|message|instruction|attention)s?\s+(?:to|for)\s+(?:the\s+)?(?:ai|a\.i\.|assistant|llm|language model|system|model)",
    r"mark\s+(?:it|this|this invoice|the invoice)\s+as\s+(?:verified|matched|approved|valid|paid|ok)",
    r"do\s+not\s+(?:run|perform|execute|do)\s+(?:any\s+)?(?:further|more|other)\s+checks?",
    r"(?:system|developer)\s+prompt",
    r"you\s+are\s+(?:now\s+)?(?:an?\s+)?(?:ai|assistant|language model)",
    r"already\s+(?:been\s+)?(?:verified|approved)\s+by",
]
INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)


@dataclass
class CheckOutcome:
    result: CheckResult
    discrepancies: list[Discrepancy] = field(default_factory=list)
    evidence_transaction_ids: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)


@dataclass
class Context:
    """Everything a check needs. `call` runs a tool as the verifier role through the gateway."""

    invoice: Invoice
    markdown: str
    call: Callable[[str, dict], Awaitable[dict]]
    vendor: dict | None = None  # filled by vendor_lookup
    po: dict | None = None  # filled by the first check that needs the purchase order
    po_loaded: bool = False


# --------------------------------------------------------------------------- helpers


def _passed(name: str, server: str, detail: str) -> CheckOutcome:
    return CheckOutcome(CheckResult(name=name, server=server, status=CheckStatus.PASS, detail=detail))


def _skipped(name: str, server: str, detail: str) -> CheckOutcome:
    return CheckOutcome(CheckResult(name=name, server=server, status=CheckStatus.SKIPPED, detail=detail))


def _failed(name: str, server: str, error: ErrorType, detail: str, discrepancies: list[Discrepancy],
            evidence: list[str] | None = None, flags: list[str] | None = None) -> CheckOutcome:
    severity = DEFAULT_SEVERITY[error]
    return CheckOutcome(
        CheckResult(name=name, server=server, status=CheckStatus.FAIL, detail=detail, severity=severity,
                    error_type=error),
        discrepancies, evidence or [], flags or [])


def _discrepancy(error: ErrorType, field_name: str | None, expected, found, detail: str) -> Discrepancy:
    return Discrepancy(type=error, field=field_name, expected=None if expected is None else str(expected),
                       found=None if found is None else str(found), severity=DEFAULT_SEVERITY[error], detail=detail)


def _norm_invoice_no(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _money(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


async def _load_po(ctx: Context) -> dict | None:
    """Fetch the purchase order once, for every check that needs it."""
    if not ctx.po_loaded:
        ctx.po_loaded = True
        po_number = ctx.invoice.po_number
        if po_number:
            result = await ctx.call("get_purchase_order", {"po_number": po_number})
            if result["ok"] and result["data"].get("found"):
                ctx.po = result["data"]
    return ctx.po


# --------------------------------------------------------------------------- the checks


async def check_vendor_lookup(ctx: Context) -> CheckOutcome:
    name, server = "vendor_lookup", "transactions"
    supplier = ctx.invoice.supplier
    queries = [q for q in (supplier.vat_number, supplier.name) if q]
    if not queries:
        return _skipped(name, server, "the invoice has no supplier VAT number or name to look up")
    last = None
    for query in queries:
        result = await ctx.call("get_vendor", {"name_or_vat_number": query})
        if not result["ok"]:
            return _skipped(name, server, f"vendor lookup failed: {result['error']}")
        last = result["data"]
        if last["found"]:
            ctx.vendor = last["vendor"]
            by = "VAT number" if last["match_type"] == "vat_number" else "name"
            return _passed(name, server, f"found {ctx.vendor['name']} ({ctx.vendor['id']}) by {by}")
    flags, detail = [], "supplier is not in the vendor master"
    if last and last["candidates"]:
        best = last["candidates"][0]
        flags.append(f"supplier name resembles known vendor {best['vendor']['name']} (score {best['score']})")
        detail += f"; closest existing vendor: {best['vendor']['name']} (score {best['score']})"
    found = f"{supplier.name} / {supplier.vat_number}"
    return _failed(name, server, ErrorType.UNKNOWN_VENDOR, detail,
                   [_discrepancy(ErrorType.UNKNOWN_VENDOR, "supplier", "a vendor in the master", found, detail)],
                   flags=flags)


def check_injection_scan(ctx: Context) -> CheckOutcome:
    name, server = "injection_scan", "host"
    text = ctx.markdown + "\n" + (ctx.invoice.notes or "")
    match = INJECTION_RE.search(text)
    if not match:
        return _passed(name, server, "no instructions aimed at an AI system found in the document text")
    snippet = " ".join(text[match.start():match.start() + 160].split())  # the matched text and what follows it
    detail = f"the document contains text aimed at an AI system: \"{snippet}\""
    return _failed(name, server, ErrorType.PROMPT_INJECTION, detail,
                   [_discrepancy(ErrorType.PROMPT_INJECTION, "notes", "invoice data only", snippet, detail)],
                   flags=["possible prompt injection: the extracted data cannot be trusted"])


async def check_duplicate_invoice(ctx: Context) -> CheckOutcome:
    name, server = "duplicate_invoice", "transactions"
    if ctx.vendor is None:
        return _skipped(name, server, "vendor not found, cannot compare against its invoices")
    if not ctx.invoice.invoice_number:
        return _skipped(name, server, "the invoice has no invoice number")
    result = await ctx.call("lookup_invoice_number", {"vendor_id": ctx.vendor["id"],
                                                      "invoice_no": ctx.invoice.invoice_number})
    if not result["ok"]:
        return _skipped(name, server, f"lookup failed: {result['error']}")
    matches = result["data"]["matches"]
    if not matches:
        return _passed(name, server, f"invoice number {ctx.invoice.invoice_number} has not been seen for this vendor")
    best = matches[0]
    detail = (f"invoice number matches {best['invoice_no']} already seen for this vendor "
              f"({best['match_type'].replace('_', ' ')}, {best['status']}, {best['date']}, EUR {best['amount']:.2f})")
    return _failed(name, server, ErrorType.DUPLICATE_INVOICE, detail,
                   [_discrepancy(ErrorType.DUPLICATE_INVOICE, "invoice_number", "a number not seen before",
                                 f"{ctx.invoice.invoice_number} ~ {best['invoice_no']}", detail)])


async def check_already_paid(ctx: Context) -> CheckOutcome:
    name, server = "already_paid", "transactions"
    total = _money(ctx.invoice.total_incl_vat)
    if ctx.vendor is None or total is None:
        return _skipped(name, server, "needs a known vendor and an invoice total")
    this_number = _norm_invoice_no(ctx.invoice.invoice_number)
    payments: list[dict] = []
    po = await _load_po(ctx)
    if po is not None:
        payments = po["payments"]
        source = f"payments against {po['po_number']}"
    else:
        window_start = date.fromisoformat(ctx.invoice.invoice_date) - timedelta(days=PAYMENT_MATCH_WINDOW_DAYS) \
            if ctx.invoice.invoice_date else None
        args = {"vendor_id": ctx.vendor["id"], "amount_min": float(total) - 0.01, "amount_max": float(total) + 0.01}
        if window_start:
            args["date_from"] = window_start.isoformat()
        result = await ctx.call("find_transactions", args)
        if not result["ok"]:
            return _skipped(name, server, f"lookup failed: {result['error']}")
        payments = result["data"]["transactions"]
        source = "recent payments of the same amount to this vendor"
    same_amount = [p for p in payments if abs(Decimal(str(p["amount_eur"])) - total) <= Decimal("0.01")
                   and _norm_invoice_no(p.get("invoice_no")) != this_number]  # same number = duplicate check's job
    if not same_amount:
        return _passed(name, server, f"no payment of EUR {total:.2f} found in {source}")
    payment = same_amount[0]
    detail = (f"a payment of EUR {payment['amount_eur']:.2f} was already made on {payment['date']} "
              f"({payment['txn_id']}, invoice {payment.get('invoice_no') or 'n/a'})")
    return _failed(name, server, ErrorType.ALREADY_PAID, detail,
                   [_discrepancy(ErrorType.ALREADY_PAID, "total_incl_vat", "no earlier payment",
                                 f"{payment['txn_id']} on {payment['date']}", detail)],
                   evidence=[p["txn_id"] for p in same_amount])


async def check_po_amount_match(ctx: Context) -> CheckOutcome:
    name, server = "po_amount_match", "transactions"
    if ctx.vendor is None:
        return _skipped(name, server, "vendor not found")
    if not ctx.invoice.po_number:
        return _skipped(name, server, "the invoice has no purchase order number")
    po = await _load_po(ctx)
    if po is None:
        return _skipped(name, server, f"purchase order {ctx.invoice.po_number} was not found")
    invoice_total, po_total = _money(ctx.invoice.total_excl_vat), Decimal(str(po["total_excl_vat"]))
    if invoice_total is None:
        return _skipped(name, server, "the invoice has no total excluding VAT")
    difference = invoice_total - po_total
    allowed = max(AMOUNT_TOLERANCE_MIN, po_total * AMOUNT_TOLERANCE_PCT)
    if abs(difference) <= allowed:
        return _passed(name, server, f"invoice EUR {invoice_total:.2f} matches PO EUR {po_total:.2f} excl. VAT")
    pct = difference / po_total * 100
    detail = (f"invoice total EUR {invoice_total:.2f} excl. VAT differs from PO {po['po_number']} "
              f"EUR {po_total:.2f} by EUR {difference:+.2f} ({pct:+.1f}%)")
    return _failed(name, server, ErrorType.WRONG_AMOUNT, detail,
                   [_discrepancy(ErrorType.WRONG_AMOUNT, "total_excl_vat", f"{po_total:.2f}",
                                 f"{invoice_total:.2f}", detail)])


async def check_goods_receipt_match(ctx: Context) -> CheckOutcome:
    name, server = "goods_receipt_match", "transactions"
    if ctx.vendor is None:
        return _skipped(name, server, "vendor not found")
    po = await _load_po(ctx)
    if po is None:
        return _skipped(name, server, "no purchase order to compare receipts against")
    po_lines = {line["sku"].upper(): line for line in po["lines"]}
    problems: list[Discrepancy] = []
    for index, line in enumerate(ctx.invoice.lines):
        po_line = po_lines.get((line.sku or "").upper())
        if po_line is None or line.quantity is None:
            continue
        invoiced, received = Decimal(str(line.quantity)), Decimal(str(po_line["qty_received"]))
        if invoiced > received:
            detail = (f"line {index + 1} ({po_line['sku']}): invoiced {invoiced.normalize():f}, "
                      f"only {received.normalize():f} received")
            problems.append(_discrepancy(ErrorType.QUANTITY_NOT_RECEIVED, f"lines[{index}].quantity",
                                         f"{received.normalize():f}", f"{invoiced.normalize():f}", detail))
    if not problems:
        return _passed(name, server, "every invoiced quantity is covered by the goods receipts")
    return _failed(name, server, ErrorType.QUANTITY_NOT_RECEIVED, "; ".join(d.detail for d in problems), problems)


FIXED_CHECKS = [check_vendor_lookup, check_injection_scan, check_duplicate_invoice, check_already_paid,
                check_po_amount_match, check_goods_receipt_match]


async def run_check(check, ctx: Context) -> CheckOutcome:
    outcome = check(ctx)
    return await outcome if isinstance(outcome, Awaitable) else outcome


# --------------------------------------------------------------------------- the verdict


SEVERITY_ORDER = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}


def low_confidence_reasons(low_confidence_fields: list[str]) -> list[str]:
    critical = sorted(f for f in low_confidence_fields if f in CRITICAL_FIELDS)
    return [f"the extractor could not confirm these key fields in the document: {', '.join(critical)}"] if critical else []


def decide_verdict(discrepancies: list[Discrepancy], needs_review_reasons: list[str]) -> VerdictStatus:
    """Fixed rules (todo.md, agreed decisions). Same input, same verdict, always."""
    types = {d.type for d in discrepancies}
    if ErrorType.PROMPT_INJECTION in types or needs_review_reasons:
        return VerdictStatus.NEEDS_REVIEW
    if ErrorType.UNKNOWN_VENDOR in types:
        return VerdictStatus.NOT_FOUND
    if any(SEVERITY_ORDER[d.severity] >= SEVERITY_ORDER[Severity.MEDIUM] for d in discrepancies):
        return VerdictStatus.MISMATCH
    if discrepancies:
        return VerdictStatus.PARTIAL_MATCH
    return VerdictStatus.MATCHED


def confidence_for(low_confidence_fields: list[str], escalated: bool) -> float:
    value = max(0.5, 0.98 - 0.04 * len(low_confidence_fields))
    return round(min(value, 0.6) if escalated else value, 2)
