"""Fixed checks, run by code (not by the model), and the deterministic verdict rules.

Each check calls MCP tools through the gateway and returns a CheckOutcome: one CheckResult plus
any discrepancies and evidence. The verdict is then decided by `decide_verdict` from fixed rules,
so the same invoice gives the same verdict on every run. The language model adds reasoning and
suggestions; it can never lower a verdict (and raising one is opt-in, see agent/pipeline.py).
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
    # other wordings: addressed to "automated reviewers", telling the reader what verdict to give or what to skip
    r"(?:instruction|message|note|notice|attention)s?\s+(?:to|for)\s+(?:the\s+)?(?:automated|automatic)\s+\w+",
    r"\b(?:report|return|output|set|give|mark|record|classify)\b[^.\n]{0,40}\b(?:verdict|status|result)\b[^.\n]{0,30}\b(?:matched|approved|verified|valid|clean)\b",
    r"\bskip\s+(?:all\s+|every\s+|any\s+|the\s+|further\s+)*(?:checks?|verification|validation|review)",
    r"\bpre-?approved\s+by\b",
]
INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)


@dataclass
class CheckOutcome:
    result: CheckResult
    discrepancies: list[Discrepancy] = field(default_factory=list)
    evidence_transaction_ids: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)  # data cannot be trusted: a person must decide
    incomplete: list[str] = field(default_factory=list)  # something could not be read: a person must look


@dataclass
class Context:
    """Everything a check needs. `call` runs a tool as the verifier role through the gateway."""

    invoice: Invoice
    markdown: str
    call: Callable[[str, dict], Awaitable[dict]]
    vendor: dict | None = None  # filled by vendor_lookup
    po: dict | None = None  # filled by the first check that needs the purchase order
    po_loaded: bool = False
    ocr: bool = False  # the document is a scan: identifiers may be misread
    supplier_vat: str | None = None  # the supplier VAT number to use (after an OCR repair, if one was needed)
    treatment: dict | None = None  # filled by vat_treatment: how VAT must be handled for this supplier
    verified_fields: set[str] = field(default_factory=set)  # key fields confirmed by master data


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


def _date(value: str | None) -> date | None:
    """A valid ISO date, or None (a misread date on a scan must not crash a check)."""
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


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
    queries = [q for q in (ctx.supplier_vat or supplier.vat_number, supplier.name) if q]
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
            if last["match_type"] == "vat_number":
                ctx.verified_fields.add("supplier.vat_number")
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


APPROVAL_LIMIT = 5000.0  # invoices just under this amount, close together, may be one split purchase
SPLIT_WINDOW_DAYS = 7
ANOMALY_Z = 3.0  # standard deviations above the vendor's average ...
ANOMALY_TIMES_MEDIAN = 3.0  # ... and at least this many times the vendor's median invoice


def _invoice_dict(ctx: Context) -> dict:
    return ctx.invoice.model_dump(mode="json")


async def check_vat_number_valid(ctx: Context) -> CheckOutcome:
    name, server = "vat_number_valid", "tax_rules"
    vat = ctx.invoice.supplier.vat_number
    if not vat:
        return _skipped(name, server, "the invoice shows no supplier VAT number (see mandatory_fields)")
    result = await ctx.call("validate_vat_number", {"vat_number": vat, "repair_ocr_errors": ctx.ocr})
    if not result["ok"]:
        return _skipped(name, server, f"validation failed: {result['error']}")
    data = result["data"]
    if data["valid"] and data["repaired_from"]:
        ctx.supplier_vat = data["normalized"]
        outcome = _passed(name, server, f"{data['repaired_from']} is not valid as read, but {data['normalized']} "
                                        "is: a scan misread, corrected")
        outcome.risk_flags.append(f"supplier VAT number misread by OCR and corrected to {data['normalized']}")
        return outcome
    if data["valid"]:
        return _passed(name, server, f"{data['normalized']} is a valid VAT number")
    detail = f"{vat} is not a valid VAT number ({data['reason']})"
    flags = ["the document is a scan: confirm the VAT number against the original"] if ctx.ocr else []
    return _failed(name, server, ErrorType.INVALID_VAT_NUMBER, detail,
                   [_discrepancy(ErrorType.INVALID_VAT_NUMBER, "supplier.vat_number", "a valid VAT number", vat,
                                 detail)], flags=flags)


async def check_invoice_math(ctx: Context) -> CheckOutcome:
    name, server = "invoice_math", "tax_rules"
    result = await ctx.call("validate_invoice_math", {"invoice_json": _invoice_dict(ctx)})
    if not result["ok"]:
        return _skipped(name, server, f"validation failed: {result['error']}")
    errors = result["data"]["errors"]
    if not errors:
        return _passed(name, server, "line totals, VAT per rate and the totals all add up")
    problems = [_discrepancy(ErrorType.VAT_ARITHMETIC_ERROR, e["field"], e["expected"], e["found"],
                             f"{e['field']}: expected {e['expected']}, the invoice says {e['found']}")
                for e in errors]
    return _failed(name, server, ErrorType.VAT_ARITHMETIC_ERROR, "; ".join(d.detail for d in problems), problems)


async def check_mandatory_fields(ctx: Context) -> CheckOutcome:
    name, server = "mandatory_fields", "tax_rules"
    result = await ctx.call("check_invoice_requirements", {"invoice_json": _invoice_dict(ctx)})
    if not result["ok"]:
        return _skipped(name, server, f"validation failed: {result['error']}")
    missing = result["data"]["missing"]
    if _date(ctx.invoice.invoice_date) is None and not any(m["field"] == "invoice_date" for m in missing):
        missing.append({"field": "invoice_date", "needs": "a readable invoice date"})
    if not missing:
        return _passed(name, server, "every mandatory invoice field is present")
    fields = ", ".join(m["field"] for m in missing)
    if ctx.ocr:  # on a scan, "missing" usually means "could not be read": a person should look, not a rejection
        outcome = _skipped(name, server, f"could not read from the scan: {fields}")
        outcome.incomplete.append(f"these fields could not be read from the scan: {fields}")
        return outcome
    problems = [_discrepancy(ErrorType.MISSING_MANDATORY_FIELDS, m["field"], m["needs"], None,
                             f"missing: {m['needs']}") for m in missing]
    return _failed(name, server, ErrorType.MISSING_MANDATORY_FIELDS, f"missing {fields}", problems)


def _country(ctx: Context, party_country: str | None, vat_number: str | None, default: str | None = None) -> str | None:
    if party_country:
        return party_country.upper()
    if vat_number and vat_number[:2].isalpha():
        return vat_number[:2].upper()
    return default


async def check_vat_treatment(ctx: Context) -> CheckOutcome:
    name, server = "vat_treatment", "tax_rules"
    invoice = ctx.invoice
    supplier_country = ctx.vendor["country"] if ctx.vendor else _country(
        ctx, invoice.supplier.country, ctx.supplier_vat or invoice.supplier.vat_number)
    buyer_country = _country(ctx, invoice.buyer.country, invoice.buyer.vat_number, default="NL")
    if not supplier_country:
        return _skipped(name, server, "the supplier's country is unknown")
    result = await ctx.call("expected_vat_treatment", {
        "supplier_country": supplier_country, "buyer_country": buyer_country,
        "buyer_vat_number": invoice.buyer.vat_number, "supply_type": "services"})
    if not result["ok"]:
        return _skipped(name, server, f"rule lookup failed: {result['error']}")
    ctx.treatment = result["data"]
    treatment = ctx.treatment["treatment"]
    if treatment == "manual_review":
        outcome = _skipped(name, server, ctx.treatment["reason"])
        outcome.needs_review.append(f"VAT treatment needs a person: {ctx.treatment['reason']}")
        return outcome

    rates = {Decimal(str(line.vat_rate)) for line in invoice.lines if line.vat_rate is not None}
    charged_vat = any(rate != 0 for rate in rates) or (_money(invoice.total_vat) or Decimal(0)) != 0
    problems: list[str] = []
    if treatment in ("reverse_charge", "intra_community_zero_rate"):
        if charged_vat:
            problems.append(f"VAT was charged ({', '.join(f'{r.normalize():f}%' for r in sorted(rates))}) on a "
                            f"business-to-business supply from {supplier_country}, which must be {ctx.treatment['reason']}"
                            " with 0% VAT")
        if treatment == "reverse_charge" and not invoice.reverse_charge:
            problems.append("the invoice does not say 'Reverse charge'")
        if not invoice.buyer.vat_number:
            problems.append("the buyer's VAT number is not shown")
    elif invoice.reverse_charge:
        problems.append("the invoice says 'Reverse charge', but this is a domestic supply")
    if not problems:
        return _passed(name, server, f"{treatment.replace('_', ' ')}: the invoice follows the rule "
                                     f"({ctx.treatment['reason']})")
    detail = "; ".join(problems)
    return _failed(name, server, ErrorType.REVERSE_CHARGE_ERROR, detail,
                   [_discrepancy(ErrorType.REVERSE_CHARGE_ERROR, "vat_breakdown", treatment.replace("_", " "),
                                 "VAT charged" if charged_vat else "no VAT", detail)])


async def check_vat_rate(ctx: Context) -> CheckOutcome:
    name, server = "vat_rate", "tax_rules"
    if ctx.treatment is None or ctx.treatment["treatment"] != "domestic":
        return _skipped(name, server, "only domestic supplies are checked against the Dutch rate table")
    po = await _load_po(ctx)
    day = _date(ctx.invoice.supply_date) or _date(ctx.invoice.invoice_date)
    if po is None or day is None:
        return _skipped(name, server, "needs a purchase order (for the VAT category) and a readable date")
    categories = {line["sku"].upper(): line["vat_category"] for line in po["lines"]}
    rates: dict[str, Decimal] = {}
    problems: list[Discrepancy] = []
    checked = 0
    for index, line in enumerate(ctx.invoice.lines):
        category = categories.get((line.sku or "").upper())
        if category is None or line.vat_rate is None:
            continue
        if category not in rates:
            result = await ctx.call("get_vat_rate", {"category": category, "on_date": day.isoformat()})
            if not result["ok"]:
                return _skipped(name, server, f"rate lookup failed: {result['error']}")
            rates[category] = Decimal(str(result["data"]["rate"]))
        checked += 1
        if Decimal(str(line.vat_rate)) != rates[category]:
            detail = (f"line {index + 1} ({line.sku}, {category.replace('_', ' ')}): charged "
                      f"{Decimal(str(line.vat_rate)).normalize():f}% but the rate is {rates[category].normalize():f}%")
            problems.append(_discrepancy(ErrorType.WRONG_VAT_RATE, f"lines[{index}].vat_rate",
                                         f"{rates[category].normalize():f}", f"{Decimal(str(line.vat_rate)).normalize():f}",
                                         detail))
    if checked == 0:
        return _skipped(name, server, "no invoice line could be matched to a purchase order line")
    if not problems:
        return _passed(name, server, f"all {checked} lines use the correct VAT rate")
    return _failed(name, server, ErrorType.WRONG_VAT_RATE, "; ".join(d.detail for d in problems), problems)


async def check_bank_details(ctx: Context) -> CheckOutcome:
    name, server = "bank_details", "vendor_checks"
    iban = ctx.invoice.supplier.iban
    if ctx.vendor is None or not iban:
        return _skipped(name, server, "needs a known vendor and an IBAN on the invoice")
    result = await ctx.call("compare_bank_details", {"vendor_id": ctx.vendor["id"], "iban": iban})
    if not result["ok"]:
        return _skipped(name, server, f"comparison failed: {result['error']}")
    data = result["data"]
    flags = []
    if data["vendor_iban_changed_recently"]:
        flags.append(f"the vendor's bank account was changed recently ({data['vendor_iban_changed_at']})")
    if data["result"] == "match":
        ctx.verified_fields.add("supplier.iban")
        outcome = _passed(name, server, f"the IBAN ({data['invoice_iban_masked']}) matches the vendor master")
        outcome.risk_flags.extend(flags)
        return outcome
    if data["result"] == "ocr_misread_likely" and ctx.ocr:
        ctx.verified_fields.add("supplier.iban")
        outcome = _passed(name, server, f"the IBAN as read is invalid but is {data['edit_distance']} character(s) from "
                                        f"the vendor master's ({data['vendor_iban_masked']}): a scan misread")
        outcome.risk_flags.append(f"supplier IBAN misread by OCR; the vendor master's IBAN "
                                  f"({data['vendor_iban_masked']}) is assumed")
        outcome.risk_flags.extend(flags)
        return outcome
    bank = ""
    check = await ctx.call("validate_iban", {"iban": iban})
    if check["ok"] and check["data"]["bank_name"]:
        bank = f" (a {check['data']['bank_name']} account)"
    detail = (f"the IBAN on the invoice ({data['invoice_iban_masked']}{bank}) differs from the vendor master "
              f"({data['vendor_iban_masked']})")
    return _failed(name, server, ErrorType.CHANGED_BANK_DETAILS, detail,
                   [_discrepancy(ErrorType.CHANGED_BANK_DETAILS, "supplier.iban", data["vendor_iban_masked"],
                                 data["invoice_iban_masked"], detail)],
                   flags=[*flags, "bank details changed: confirm with the vendor by phone, using a number you already have"])


async def check_sanctions(ctx: Context) -> CheckOutcome:
    name, server = "sanctions", "vendor_checks"
    screened = ctx.vendor["name"] if ctx.vendor else ctx.invoice.supplier.name
    if not screened:
        return _skipped(name, server, "no supplier name to screen")
    country = ctx.vendor["country"] if ctx.vendor else ctx.invoice.supplier.country
    result = await ctx.call("screen_sanctions", {"name": screened, **({"country": country} if country else {})})
    if not result["ok"]:
        return _skipped(name, server, f"screening failed: {result['error']}")
    matches = result["data"]["matches"]
    if not matches:
        return _passed(name, server, f"no similar name on the sanctions lists (screened '{screened}')")
    best = matches[0]
    detail = (f"'{screened}' resembles '{best['listed_name']}' ({best['list']}, score {best['score']})")
    return _failed(name, server, ErrorType.SANCTIONED_VENDOR, detail,
                   [_discrepancy(ErrorType.SANCTIONED_VENDOR, "supplier.name", "no sanctions-list match",
                                 f"{best['listed_name']} ({best['score']})", detail)],
                   flags=["a sanctions-list match sends the invoice to review; it is not an automatic rejection"])


async def check_amount_anomaly(ctx: Context) -> CheckOutcome:
    name, server = "amount_anomaly", "anomaly_stats"
    total = _money(ctx.invoice.total_incl_vat)
    if ctx.vendor is None or total is None:
        return _skipped(name, server, "needs a known vendor and an invoice total")
    profile = await ctx.call("vendor_amount_profile", {"vendor_id": ctx.vendor["id"], "amount": float(total)})
    if not profile["ok"]:
        return _skipped(name, server, f"statistics failed: {profile['error']}")
    stats = profile["data"]
    problems: list[Discrepancy] = []
    if stats["suppressed"]:
        detail = "too little history for this vendor to judge the amount"
    else:
        detail = (f"EUR {total:.2f} is {stats['z_score']} standard deviations from this vendor's average "
                  f"(typical EUR {stats['typical_low']:.0f} to {stats['typical_high']:.0f}, median {stats['median']:.0f})")
        if stats["z_score"] >= ANOMALY_Z and (stats["times_median"] or 0) >= ANOMALY_TIMES_MEDIAN:
            problems.append(_discrepancy(ErrorType.UNUSUAL_AMOUNT, "total_incl_vat",
                                         f"EUR {stats['typical_low']:.0f} to {stats['typical_high']:.0f}",
                                         f"{total:.2f}", f"unusually large: {detail}"))
    on_date = _date(ctx.invoice.invoice_date)
    if on_date is not None:
        split = await ctx.call("split_invoice_check", {"vendor_id": ctx.vendor["id"], "window_days": SPLIT_WINDOW_DAYS,
                                                       "threshold": APPROVAL_LIMIT, "on_date": on_date.isoformat()})
        if split["ok"] and split["data"]["possible_split"]:
            detail = (f"{split['data']['count']} invoices from this vendor just under the EUR {APPROVAL_LIMIT:.0f} "
                      f"approval limit within {SPLIT_WINDOW_DAYS} days (EUR {split['data']['total']:.2f} together)")
            problems.append(_discrepancy(ErrorType.UNUSUAL_AMOUNT, "total_incl_vat", "one purchase",
                                         f"{split['data']['count']} invoices", f"possible split invoice: {detail}"))
    if problems:
        return _failed(name, server, ErrorType.UNUSUAL_AMOUNT, "; ".join(d.detail for d in problems), problems)
    return _passed(name, server, detail)


async def check_employee_overlap(ctx: Context) -> CheckOutcome:
    name, server = "employee_overlap", "vendor_checks"
    if ctx.vendor is None:
        return _skipped(name, server, "vendor not found")
    result = await ctx.call("vendor_employee_overlap", {"vendor_id": ctx.vendor["id"]})
    if not result["ok"]:
        return _skipped(name, server, f"comparison failed: {result['error']}")
    if not result["data"]["overlap"]:
        return _passed(name, server, "the vendor shares no address or bank account with an employee")
    fields = " and ".join(result["data"]["matched_fields"])
    detail = f"the vendor shares its {fields} with an employee"
    outcome = CheckOutcome(CheckResult(name=name, server=server, status=CheckStatus.FAIL, detail=detail,
                                       severity=Severity.HIGH))
    outcome.needs_review.append(detail)
    outcome.risk_flags.append("possible conflict of interest")
    return outcome


async def check_date_flags(ctx: Context) -> CheckOutcome:
    name, server = "date_flags", "anomaly_stats"
    day = _date(ctx.invoice.invoice_date)
    if day is None:
        return _skipped(name, server, "no readable invoice date")
    result = await ctx.call("date_flags", {"on_date": day.isoformat()})
    if not result["ok"]:
        return _skipped(name, server, f"lookup failed: {result['error']}")
    data = result["data"]
    notes = [text for flag, text in ((data["weekend"], f"a {data['weekday']}"),
                                     (data["public_holiday"], f"a public holiday ({data['public_holiday']})"),
                                     (data["future"], "in the future")) if flag]
    if not notes:
        return _passed(name, server, f"the invoice date ({data['date']}) is an ordinary working day")
    outcome = _passed(name, server, f"the invoice date ({data['date']}) is {' and '.join(notes)}")
    outcome.risk_flags.append(f"invoice dated on {' and '.join(notes)}")
    return outcome


# Order matters: the VAT number is validated (and repaired, for scans) before the vendor is looked up,
# and the VAT treatment runs before the VAT rate check.
FIXED_CHECKS = [check_vat_number_valid, check_vendor_lookup, check_injection_scan, check_duplicate_invoice,
                check_already_paid, check_po_amount_match, check_goods_receipt_match, check_invoice_math,
                check_mandatory_fields, check_vat_treatment, check_vat_rate, check_bank_details, check_sanctions,
                check_amount_anomaly, check_employee_overlap, check_date_flags]


async def run_check(check, ctx: Context) -> CheckOutcome:
    outcome = check(ctx)
    return await outcome if isinstance(outcome, Awaitable) else outcome


# --------------------------------------------------------------------------- the verdict


SEVERITY_ORDER = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}


def low_confidence_reasons(low_confidence_fields: list[str]) -> list[str]:
    critical = sorted(f for f in low_confidence_fields if f in CRITICAL_FIELDS)
    return [f"the extractor could not confirm these key fields in the document: {', '.join(critical)}"] if critical else []


def decide_verdict(discrepancies: list[Discrepancy], needs_review_reasons: list[str],
                   incomplete_reasons: list[str] = ()) -> VerdictStatus:
    """Fixed rules (todo.md, agreed decisions). Same input, same verdict, always.

    Two kinds of reasons for a person to look:
      * needs_review_reasons: the data cannot be trusted (an injection, an unconfirmed key field, the model
        asking for a person). These override everything, because a discrepancy found in untrusted data may
        not be real.
      * incomplete_reasons: something could not be read (a scan). A discrepancy that was found anyway still
        stands (a duplicate is a duplicate), so it is reported as such; only a clean result is held back.
    """
    types = {d.type for d in discrepancies}
    if ErrorType.PROMPT_INJECTION in types or needs_review_reasons:
        return VerdictStatus.NEEDS_REVIEW
    if ErrorType.UNKNOWN_VENDOR in types:
        return VerdictStatus.NOT_FOUND
    if any(SEVERITY_ORDER[d.severity] >= SEVERITY_ORDER[Severity.MEDIUM] for d in discrepancies):
        return VerdictStatus.MISMATCH
    if incomplete_reasons:
        return VerdictStatus.NEEDS_REVIEW
    if discrepancies:
        return VerdictStatus.PARTIAL_MATCH
    return VerdictStatus.MATCHED


def confidence_for(low_confidence_fields: list[str], escalated: bool) -> float:
    value = max(0.5, 0.98 - 0.04 * len(low_confidence_fields))
    return round(min(value, 0.6) if escalated else value, 2)
