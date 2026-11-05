"""The company's records, exposed through five narrow read-only tools.

These are plain functions so they can be tested directly; server.py wires them to MCP.
Rules: read-only connection, parameterised queries only, rows capped, IBANs masked,
employee data never returned.
"""

import re
from datetime import date

from rapidfuzz import fuzz, process

from servers.common.config import AS_OF
from servers.common.db import read_only
from servers.common.limits import MAX_ROWS, MIN_GROUP_SIZE, clamp_offset, page_info
from servers.common.masking import mask_iban
from servers.transactions.vat import po_total_incl_vat

VAT_LIKE = re.compile(r"^[A-Za-z]{2}[0-9A-Za-z .\-]{8,}$")
OCR_CONFUSABLES = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"})
FUZZY_VENDOR_CUTOFF = 80
FUZZY_INVOICE_CUTOFF = 95


# --------------------------------------------------------------------------- helpers


def normalize_invoice_no(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _confusable_form(normalized: str) -> str:
    """Fold look-alike characters (O/0, I/1/L, S/5, B/8) and drop leading zeros in digit runs."""
    folded = normalized.translate(OCR_CONFUSABLES)
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", folded)


def _parse_date(value: str | None, name: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from None


def _vendor_view(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "country": row["country"],
        "vat_number": row["vat_number"],
        "kvk_number": row["kvk_number"],
        "address": row["address"],
        "iban_masked": mask_iban(row["iban"]),
        "iban_changed_at": row["iban_changed_at"],
        "status": row["status"],
    }


def _require_vendor(con, vendor_id: str):
    row = con.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown vendor_id {vendor_id!r}")
    return row


# --------------------------------------------------------------------------- tools


def get_vendor(name_or_vat_number: str) -> dict:
    """Find a vendor by VAT number or name. The IBAN is masked.

    A VAT number or an exact name gives found=true. A near-miss name gives found=false and
    a short list of candidates with scores, because a person must confirm a fuzzy match.
    """
    query = " ".join(name_or_vat_number.split())
    if not query:
        raise ValueError("name_or_vat_number must not be empty")
    with read_only() as con:
        if VAT_LIKE.match(query) and sum(ch.isdigit() for ch in query) >= 8:
            compact = re.sub(r"[ .\-]", "", query).upper()
            row = con.execute("SELECT * FROM vendors WHERE vat_number = ?", (compact,)).fetchone()
            if row:
                return {"found": True, "match_type": "vat_number", "vendor": _vendor_view(row), "candidates": []}
        row = con.execute("SELECT * FROM vendors WHERE LOWER(name) = LOWER(?)", (query,)).fetchone()
        if row:
            return {"found": True, "match_type": "name_exact", "vendor": _vendor_view(row), "candidates": []}

        names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM vendors")}
        hits = process.extract(query, names, scorer=fuzz.WRatio, score_cutoff=FUZZY_VENDOR_CUTOFF, limit=3)
        candidates = []
        for _name, score, vendor_id in hits:
            candidate = con.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
            candidates.append({"score": round(score, 1), "vendor": _vendor_view(candidate)})
        return {
            "found": False,
            "match_type": "name_fuzzy" if candidates else "none",
            "vendor": None,
            "candidates": candidates,
        }


def lookup_invoice_number(vendor_id: str, invoice_no: str) -> dict:
    """Exact and fuzzy matches of an invoice number against invoices already seen for the vendor.

    The same number written differently (F-2026-001 / F2026001), OCR look-alikes (O/0, I/1)
    and near-identical numbers all count as matches, with a match_type.
    """
    normalized = normalize_invoice_no(invoice_no)
    if not normalized:
        raise ValueError("invoice_no must contain letters or digits")
    folded = _confusable_form(normalized)
    with read_only() as con:
        _require_vendor(con, vendor_id)
        rows = con.execute(
            "SELECT invoice_no, normalized_invoice_no, amount, date, status FROM invoices_seen "
            "WHERE vendor_id = ? ORDER BY date DESC", (vendor_id,)).fetchall()

    matches = []
    for row in rows:
        other = row["normalized_invoice_no"]
        if row["invoice_no"] == invoice_no:
            match_type, score = "exact", 100.0
        elif other == normalized:
            match_type, score = "same_after_normalising", 100.0
        elif _confusable_form(other) == folded:
            match_type, score = "ocr_lookalike", 98.0
        else:
            score = fuzz.ratio(normalized, other)
            if score < FUZZY_INVOICE_CUTOFF:
                continue
            match_type = "fuzzy"
        matches.append({"invoice_no": row["invoice_no"], "match_type": match_type, "score": round(score, 1),
                        "amount": row["amount"], "date": row["date"], "status": row["status"]})
    matches.sort(key=lambda m: -m["score"])
    return {"vendor_id": vendor_id, "query_normalized": normalized, "count": len(matches),
            "matches": matches[:10]}


def find_transactions(vendor_id: str, date_from: str | None = None, date_to: str | None = None,
                      amount_min: float | None = None, amount_max: float | None = None,
                      reference: str | None = None, offset: int = 0) -> dict:
    """Payments to a vendor, newest first, at most 20 per call. The IBAN is masked.

    Optional filters: payment date range, amount range (incl. VAT) and the exact reference
    (usually a purchase order number). Use next_offset to read further pages.
    """
    date_from, date_to = _parse_date(date_from, "date_from"), _parse_date(date_to, "date_to")
    where, params = ["vendor_id = ?"], [vendor_id]
    for clause, value in (("date >= ?", date_from), ("date <= ?", date_to), ("amount_eur >= ?", amount_min),
                          ("amount_eur <= ?", amount_max), ("reference = ?", reference)):
        if value is not None:
            where.append(clause)
            params.append(value)
    where_sql = " AND ".join(where)  # only fixed clauses from the tuple above, never user input
    offset = clamp_offset(offset)
    with read_only() as con:
        _require_vendor(con, vendor_id)
        total = con.execute(f"SELECT COUNT(*) FROM transactions WHERE {where_sql}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM transactions WHERE {where_sql} ORDER BY date DESC, txn_id DESC LIMIT ? OFFSET ?",
            [*params, MAX_ROWS, offset]).fetchall()
    return {
        "vendor_id": vendor_id,
        "transactions": [
            {"txn_id": r["txn_id"], "date": r["date"], "amount_eur": r["amount_eur"],
             "reference": r["reference"], "invoice_no": r["invoice_no"],
             "iban_masked": mask_iban(r["iban"]), "status": r["status"]}
            for r in rows
        ],
        **page_info(total, offset, len(rows)),
    }


def get_purchase_order(po_number: str) -> dict:
    """A purchase order with its lines, quantities received and what has been paid against it."""
    with read_only() as con:
        po = con.execute("SELECT * FROM purchase_orders WHERE po_number = ?", (po_number,)).fetchone()
        if po is None:
            return {"found": False, "po_number": po_number}
        vendor = _require_vendor(con, po["vendor_id"])
        lines = con.execute("SELECT * FROM po_lines WHERE po_number = ? ORDER BY line_no", (po_number,)).fetchall()
        received = {r["sku"]: r["qty"] for r in con.execute(
            "SELECT sku, SUM(qty_received) AS qty FROM goods_receipts WHERE po_number = ? GROUP BY sku",
            (po_number,))}
        payments = con.execute(
            "SELECT txn_id, date, amount_eur, invoice_no FROM transactions WHERE reference = ? ORDER BY date",
            (po_number,)).fetchall()
        total_incl = po_total_incl_vat(con, vendor["country"], po["date"], lines)

    paid = round(sum(p["amount_eur"] for p in payments), 2)
    return {
        "found": True,
        "po_number": po_number,
        "vendor_id": po["vendor_id"],
        "date": po["date"],
        "status": po["status"],
        "total_excl_vat": po["total_excl_vat"],
        "total_incl_vat": total_incl,
        "lines": [
            {"line_no": line["line_no"], "sku": line["sku"], "description": line["description"],
             "qty_ordered": line["qty"], "qty_received": received.get(line["sku"], 0.0),
             "unit_price": line["unit_price"], "vat_category": line["vat_category"]}
            for line in lines
        ],
        "payments": [{"txn_id": p["txn_id"], "date": p["date"], "amount_eur": p["amount_eur"],
                      "invoice_no": p["invoice_no"]} for p in payments],
        "paid_incl_vat": paid,
        "remaining_incl_vat": round(total_incl - paid, 2),
    }


def _period_bounds(period: str) -> tuple[str | None, str | None]:
    period = period.strip().lower()
    if period == "all":
        return None, None
    if period == "last_12_months":
        start = date(AS_OF.year - 1, AS_OF.month, AS_OF.day)
        return start.isoformat(), AS_OF.isoformat()
    if m := re.fullmatch(r"(\d{4})", period):
        return f"{m[1]}-01-01", f"{m[1]}-12-31"
    if m := re.fullmatch(r"(\d{4})-q([1-4])", period):
        first = (int(m[2]) - 1) * 3 + 1
        last = first + 2
        end_day = {3: 31, 6: 30, 9: 30, 12: 31}[last]
        return f"{m[1]}-{first:02d}-01", f"{m[1]}-{last:02d}-{end_day}"
    if m := re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", period):
        return f"{m[1]}-{m[2]}-01", f"{m[1]}-{m[2]}-31"
    raise ValueError("period must be 'all', 'last_12_months', YYYY, YYYY-Qn or YYYY-MM")


def vendor_payment_summary(vendor_id: str, period: str | int = "last_12_months") -> dict:
    """Totals and counts only (no rows). Groups with fewer than 5 payments are suppressed.

    period: 'all', 'last_12_months', a year (2025), a quarter (2025-Q3) or a month (2025-09).
    """
    period = str(period)  # models and JSON clients often send a bare year as a number
    date_from, date_to = _period_bounds(period)
    sql = "SELECT COUNT(*), COALESCE(SUM(amount_eur), 0) FROM transactions WHERE vendor_id = ?"
    params: list = [vendor_id]
    if date_from:
        sql += " AND date >= ? AND date <= ?"
        params += [date_from, date_to]
    with read_only() as con:
        _require_vendor(con, vendor_id)
        count, total = con.execute(sql, params).fetchone()
    result = {"vendor_id": vendor_id, "period": period, "date_from": date_from, "date_to": date_to,
              "payment_count": count}
    if count < MIN_GROUP_SIZE:
        return {**result, "total_paid": None, "average_payment": None,
                "note": f"suppressed: fewer than {MIN_GROUP_SIZE} payments in this period"}
    return {**result, "total_paid": round(total, 2), "average_payment": round(total / count, 2)}
