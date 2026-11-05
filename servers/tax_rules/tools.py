"""Dutch VAT (BTW) rules as fixed, auditable tools. No model judgement lives here.

Online checks (VIES, KvK) are deliberately left out: they need the internet. These tools are
offline format, checksum and rule checks only.
"""

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from stdnum.eu import vat as eu_vat
from stdnum.exceptions import ValidationError

from servers.common.db import read_only

# Characters an OCR engine commonly confuses, in both directions.
OCR_SWAPS = {"B": "8", "8": "B", "O": "0", "0": "O", "I": "1", "1": "I", "S": "5", "5": "S", "Z": "2", "2": "Z",
             "G": "6", "6": "G"}
MAX_REPAIR_EDITS = 2
TOLERANCE = Decimal("0.01")

VALID_CATEGORIES = ("standard", "reduced_food", "reduced_books", "reduced_medicine", "zero")
CATEGORY_TO_BAND = {"standard": "standard", "reduced_food": "reduced", "reduced_books": "reduced",
                    "reduced_medicine": "reduced", "zero": "zero"}

# The fields Dutch invoice rules require (Belastingdienst) plus the KvK number on Dutch invoices.
MANDATORY = [
    ("supplier.name", "the supplier's name"), ("supplier.address", "the supplier's address"),
    ("supplier.vat_number", "the supplier's VAT number"), ("buyer.name", "the customer's name"),
    ("buyer.address", "the customer's address"), ("invoice_date", "the invoice date"),
    ("invoice_number", "a unique sequential invoice number"), ("supply_date", "the date of supply"),
    ("lines", "a description and quantity of the goods or services"),
    ("vat_breakdown", "the amount excluding VAT, the VAT rate and the VAT amount per rate"),
    ("total_incl_vat", "the total including VAT"),
]


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _get(data: dict, path: str):
    for part in path.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


# --------------------------------------------------------------------------- validate_vat_number


def _vat_error(number: str) -> str | None:
    """None if the number is valid, else a short reason."""
    try:
        eu_vat.validate(number)
        return None
    except ValidationError as exc:
        return str(exc).rstrip(".")
    except Exception as exc:  # noqa: BLE001 - unknown country prefixes and odd input become a reason
        return f"not a recognised VAT number ({type(exc).__name__})"


def _repair_candidates(compact: str) -> list[str]:
    """Numbers reachable by swapping up to two OCR look-alike characters (after the country code)."""
    prefix, body = compact[:2], compact[2:]
    positions = [i for i, ch in enumerate(body) if ch in OCR_SWAPS]
    found: list[str] = []
    for i in positions:
        found.append(prefix + body[:i] + OCR_SWAPS[body[i]] + body[i + 1:])
    if MAX_REPAIR_EDITS >= 2:
        for a_index, a in enumerate(positions):
            for b in positions[a_index + 1:]:
                chars = list(body)
                chars[a], chars[b] = OCR_SWAPS[chars[a]], OCR_SWAPS[chars[b]]
                found.append(prefix + "".join(chars))
    return found


def validate_vat_number(vat_number: str, repair_ocr_errors: bool = False) -> dict:
    """Check a VAT number's format and checksum.

    Dutch numbers (NL + 9 digits + B + 2 digits) use stdnum.nl.btw, which covers both the old
    11-test and the mod-97 check used for sole traders since 2020; other EU numbers use
    stdnum.eu.vat. With repair_ocr_errors, a number that fails but is exactly one swap of OCR
    look-alikes (B/8, O/0, I/1...) away from a valid number is accepted as that number, and the
    result says it was repaired. If several different repairs are valid, nothing is repaired.
    """
    compact = re.sub(r"[\s.\-]", "", vat_number or "").upper()
    if len(compact) < 4:
        return {"valid": False, "normalized": compact, "country": compact[:2] or None,
                "reason": "too short to be a VAT number", "repaired_from": None}
    error = _vat_error(compact)
    result = {"valid": error is None, "normalized": compact, "country": compact[:2],
              "reason": error, "repaired_from": None}
    if error is None or not repair_ocr_errors:
        return result
    valid = sorted({c for c in _repair_candidates(compact) if _vat_error(c) is None})
    if len(valid) == 1:
        return {"valid": True, "normalized": valid[0], "country": valid[0][:2],
                "reason": None, "repaired_from": compact}
    if len(valid) > 1:
        result["reason"] = f"{error}; several corrections are possible, so none was applied"
    return result


# --------------------------------------------------------------------------- get_vat_rate


def get_vat_rate(category: str, on_date: str) -> dict:
    """The VAT percentage for a category on a date: 21% standard, 9% reduced (food, books, medicines), 0%.

    Rates live in a table with validity dates, because rates and categories change.
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(VALID_CATEGORIES)}")
    try:
        day = date.fromisoformat(on_date).isoformat()
    except ValueError:
        raise ValueError(f"date must be an ISO date (YYYY-MM-DD), got {on_date!r}") from None
    band = CATEGORY_TO_BAND[category]
    with read_only() as con:
        row = con.execute(
            "SELECT rate, valid_from, valid_to FROM vat_rates WHERE category = ? AND valid_from <= ? "
            "AND (valid_to IS NULL OR valid_to >= ?)", (band, day, day)).fetchone()
    if row is None:
        raise ValueError(f"no VAT rate is defined for {category} on {day}")
    return {"category": category, "date": day, "rate": row["rate"], "band": band,
            "valid_from": row["valid_from"], "valid_to": row["valid_to"]}


# --------------------------------------------------------------------------- expected_vat_treatment


def expected_vat_treatment(supplier_country: str, buyer_country: str, buyer_vat_number: str | None,
                           supply_type: str = "services") -> dict:
    """How VAT must be handled between supplier and buyer.

    * both in the Netherlands: domestic sale, Dutch VAT is charged
    * business-to-business from another EU country: reverse charge for services (0% on the invoice,
      the text "Reverse charge", both VAT numbers); intra-community supply for goods (0%, both numbers)
    * anything else: outside what these rules cover, so a person decides
    """
    supplier, buyer = (supplier_country or "").upper(), (buyer_country or "").upper()
    if supply_type not in ("goods", "services"):
        raise ValueError("supply_type must be 'goods' or 'services'")
    with read_only() as con:
        eu = {r["code"] for r in con.execute("SELECT code FROM eu_countries")}
    if supplier == "NL" and buyer == "NL":
        return {"treatment": "domestic", "charge_vat": True, "invoice_rate": None,
                "must_show": ["Dutch VAT at the rate of the goods or services"],
                "reason": "supplier and buyer are both in the Netherlands"}
    if supplier in eu and buyer in eu and supplier != buyer:
        if not buyer_vat_number:
            return {"treatment": "manual_review", "charge_vat": None, "invoice_rate": None, "must_show": [],
                    "reason": "cross-border supply but no buyer VAT number: cannot tell whether the buyer is a business"}
        if supply_type == "services":
            return {"treatment": "reverse_charge", "charge_vat": False, "invoice_rate": 0.0,
                    "must_show": ["the text 'Reverse charge'", "the supplier's VAT number", "the buyer's VAT number"],
                    "reason": f"business-to-business services from {supplier} to {buyer}"}
        return {"treatment": "intra_community_zero_rate", "charge_vat": False, "invoice_rate": 0.0,
                "must_show": ["0% VAT", "the supplier's VAT number", "the buyer's VAT number"],
                "reason": f"business-to-business supply of goods from {supplier} to {buyer}"}
    return {"treatment": "manual_review", "charge_vat": None, "invoice_rate": None, "must_show": [],
            "reason": f"supplier country {supplier or '?'} and buyer country {buyer or '?'} are outside the rules "
                      "covered here"}


# --------------------------------------------------------------------------- check_invoice_requirements


def check_invoice_requirements(invoice_json: dict) -> dict:
    """Which mandatory invoice fields are missing (Belastingdienst rules, plus the KvK number)."""
    if not isinstance(invoice_json, dict):
        raise ValueError("invoice_json must be an object")  # noqa: TRY004 - ValueError becomes a readable tool error
    missing: list[dict] = []
    for path, meaning in MANDATORY:
        value = _get(invoice_json, path)
        if value in (None, "", [], {}):
            missing.append({"field": path, "needs": meaning})
    lines = invoice_json.get("lines") or []
    for index, line in enumerate(lines):
        for key in ("description", "quantity"):
            if line.get(key) in (None, ""):
                missing.append({"field": f"lines[{index}].{key}", "needs": f"the line's {key}"})
    for index, row in enumerate(invoice_json.get("vat_breakdown") or []):
        for key in ("rate", "base", "vat"):
            if row.get(key) in (None, ""):
                missing.append({"field": f"vat_breakdown[{index}].{key}", "needs": f"the VAT summary's {key}"})
    if invoice_json.get("reverse_charge") and not _get(invoice_json, "buyer.vat_number"):
        missing.append({"field": "buyer.vat_number", "needs": "the buyer's VAT number (required for reverse charge)"})
    is_dutch = (_get(invoice_json, "supplier.country") or "").upper() == "NL" or         str(_get(invoice_json, "supplier.vat_number") or "").upper().startswith("NL")
    if is_dutch and not _get(invoice_json, "supplier.kvk_number"):
        missing.append({"field": "supplier.kvk_number", "needs": "the supplier's KvK (Chamber of Commerce) number"})
    return {"ok": not missing, "missing": missing}


# --------------------------------------------------------------------------- validate_invoice_math


def validate_invoice_math(invoice_json: dict) -> dict:
    """Recompute line totals, VAT per rate and the totals, and list every figure that does not add up."""
    if not isinstance(invoice_json, dict):
        raise ValueError("invoice_json must be an object")  # noqa: TRY004 - ValueError becomes a readable tool error
    errors: list[dict] = []

    def compare(field: str, expected: Decimal, found: Decimal | None) -> None:
        if found is None or abs(expected - found) > TOLERANCE:
            errors.append({"field": field, "expected": str(_money(expected)),
                           "found": None if found is None else str(found)})

    bases: dict[Decimal, Decimal] = {}
    for index, line in enumerate(invoice_json.get("lines") or []):
        qty, price, rate = _dec(line.get("quantity")), _dec(line.get("unit_price")), _dec(line.get("vat_rate"))
        total = _dec(line.get("line_total"))
        if qty is None or price is None:
            continue
        expected = _money(qty * price)
        compare(f"lines[{index}].line_total", expected, total)
        if rate is not None:
            bases[rate.normalize()] = bases.get(rate.normalize(), Decimal(0)) + (total if total is not None else expected)

    stated = {}
    for index, row in enumerate(invoice_json.get("vat_breakdown") or []):
        rate, base, vat = _dec(row.get("rate")), _dec(row.get("base")), _dec(row.get("vat"))
        if rate is None:
            continue
        stated[rate.normalize()] = (index, base, vat)
    for rate, base_sum in bases.items():
        if rate not in stated:
            errors.append({"field": "vat_breakdown", "expected": f"a row for {rate}% (lines total {base_sum})",
                           "found": None})
            continue
        index, base, vat = stated[rate]
        compare(f"vat_breakdown[{index}].base", base_sum, base)
        compare(f"vat_breakdown[{index}].vat", _money(base_sum * rate / 100), vat)

    breakdown_bases = [s[1] for s in stated.values() if s[1] is not None]
    breakdown_vat = [s[2] for s in stated.values() if s[2] is not None]
    total_excl, total_vat, total_incl = (_dec(invoice_json.get(k)) for k in
                                         ("total_excl_vat", "total_vat", "total_incl_vat"))
    if breakdown_bases:
        compare("total_excl_vat", sum(breakdown_bases, Decimal(0)), total_excl)
    if breakdown_vat:
        compare("total_vat", sum(breakdown_vat, Decimal(0)), total_vat)
    if total_excl is not None and total_vat is not None:
        compare("total_incl_vat", total_excl + total_vat, total_incl)
    return {"ok": not errors, "errors": errors}
