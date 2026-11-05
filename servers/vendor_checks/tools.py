"""Vendor and bank checks: IBAN validity, bank details against the vendor master, sanctions
screening and vendor/employee overlap. IBANs are shown masked; employee data is never returned."""

import re
from datetime import date, timedelta

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein
from stdnum import iban as stdnum_iban
from stdnum.exceptions import ValidationError

from servers.common.config import AS_OF
from servers.common.db import read_only
from servers.common.masking import compact_iban, mask_iban

RECENT_CHANGE_DAYS = 180
OCR_MAX_EDITS = 2  # an invalid IBAN within this many edits of the vendor's own IBAN is treated as a misread
SANCTIONS_CUTOFF = 85
MAX_SANCTION_MATCHES = 5
LEGAL_FORMS = re.compile(r"\b(b\.?v\.?|n\.?v\.?|ltd|llc|gmbh|ag|sa|bvba|nv|bv|kgaa|jsc|fze|co|company|inc|stichting)\b")

BANK_CODE_SLICE = {"NL": slice(4, 8), "BE": slice(4, 7), "DE": slice(4, 12)}


def _iban_error(iban: str) -> str | None:
    try:
        stdnum_iban.validate(iban)
        return None
    except ValidationError as exc:
        return str(exc).rstrip(".")
    except Exception as exc:  # noqa: BLE001 - odd input becomes a reason, not a crash
        return f"not a recognised IBAN ({type(exc).__name__})"


def validate_iban(iban: str) -> dict:
    """Check an IBAN (mod-97 via stdnum), and name the bank from its bank code. Returns the IBAN masked."""
    compact = compact_iban(iban or "")
    if len(compact) < 8:
        return {"valid": False, "iban_masked": mask_iban(compact), "country": None, "bank_code": None,
                "bank_name": None, "reason": "too short to be an IBAN"}
    error = _iban_error(compact)
    country = compact[:2]
    bank_code = compact[BANK_CODE_SLICE[country]] if country in BANK_CODE_SLICE else None
    bank_name = None
    if bank_code:
        with read_only() as con:
            row = con.execute("SELECT bank_name FROM bank_codes WHERE code = ? AND country = ?",
                              (bank_code, country)).fetchone()
        bank_name = row["bank_name"] if row else None
    return {"valid": error is None, "iban_masked": mask_iban(compact), "country": country,
            "bank_code": bank_code, "bank_name": bank_name, "reason": error}


def compare_bank_details(vendor_id: str, iban: str) -> dict:
    """Compare an invoice's IBAN with the vendor master.

    result is 'match', 'different' or 'ocr_misread_likely' (the invoice IBAN fails its checksum but is
    within two edits of the vendor's IBAN, which is what a scanned digit misread looks like; a valid IBAN
    that differs is always 'different'). Also says whether the vendor master's IBAN changed recently.
    Only the last four digits of either IBAN are shown.
    """
    with read_only() as con:
        row = con.execute("SELECT iban, iban_changed_at FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown vendor_id {vendor_id!r}")
    master, found = compact_iban(row["iban"]), compact_iban(iban or "")
    valid = _iban_error(found) is None
    distance = Levenshtein.distance(master, found)
    if found == master:
        result = "match"
    elif not valid and distance <= OCR_MAX_EDITS:
        result = "ocr_misread_likely"
    else:
        result = "different"
    changed_at = row["iban_changed_at"]
    recent = bool(changed_at) and date.fromisoformat(changed_at) >= AS_OF - timedelta(days=RECENT_CHANGE_DAYS)
    return {"vendor_id": vendor_id, "result": result, "matches": result == "match",
            "invoice_iban_valid": valid, "edit_distance": distance,
            "vendor_iban_masked": mask_iban(master), "invoice_iban_masked": mask_iban(found),
            "vendor_iban_changed_recently": recent, "vendor_iban_changed_at": changed_at}


def _normalise_name(name: str) -> str:
    text = re.sub(r"[^\w\s]", " ", name.lower())
    text = LEGAL_FORMS.sub(" ", text)
    return " ".join(text.split())


def screen_sanctions(name: str, country: str | None = None) -> dict:
    """Fuzzy-match a name against the sanctions list sample (EU and Dutch national).

    Scores run 0-100. This only ever sends an invoice to review: a match is a reason for a person to
    look, never an automatic rejection.
    """
    query = _normalise_name(name or "")
    if not query:
        raise ValueError("name must not be empty")
    with read_only() as con:
        entries = con.execute("SELECT list, name, aliases, country, listed_on FROM sanctions").fetchall()
    matches = []
    for entry in entries:
        names = [entry["name"], *(entry["aliases"] or "").split(";")]
        best = max((fuzz.token_sort_ratio(query, _normalise_name(n)) for n in names if n.strip()), default=0)
        if best >= SANCTIONS_CUTOFF:
            matches.append({"list": entry["list"], "listed_name": entry["name"], "score": round(best, 1),
                            "country": entry["country"], "listed_on": entry["listed_on"],
                            "same_country": bool(country) and country.upper() == entry["country"]})
    matches.sort(key=lambda m: -m["score"])
    return {"screened": name, "match_count": len(matches), "matches": matches[:MAX_SANCTION_MATCHES],
            "note": "a match sends the invoice to review; it never rejects it automatically"}


def vendor_employee_overlap(vendor_id: str) -> dict:
    """Does the vendor share an address or bank account with an employee? Says which field matched, never who."""
    with read_only() as con:
        vendor = con.execute("SELECT address, iban FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        if vendor is None:
            raise ValueError(f"unknown vendor_id {vendor_id!r}")
        employees = con.execute("SELECT address, iban FROM employees").fetchall()
    street = lambda text: re.sub(r"[^a-z0-9]", "", text.split(",")[0].lower())
    matched = []
    if any(compact_iban(e["iban"]) == compact_iban(vendor["iban"]) for e in employees):
        matched.append("iban")
    if any(street(e["address"]) == street(vendor["address"]) for e in employees):
        matched.append("address")
    return {"vendor_id": vendor_id, "overlap": bool(matched), "matched_fields": matched}
