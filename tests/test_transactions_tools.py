import sqlite3

import pytest

from servers.common.db import read_only
from servers.common.masking import mask_iban
from servers.transactions import tools


def vendor_id_of(invoice: dict) -> str:
    return tools.get_vendor(invoice["supplier"]["vat_number"])["vendor"]["id"]


# --------------------------------------------------------------------------- get_vendor


def test_get_vendor_by_vat_number_masks_iban(case):
    invoice = case("clean_standard")["expected_invoice"]
    result = tools.get_vendor(invoice["supplier"]["vat_number"])
    assert result["found"] and result["match_type"] == "vat_number"
    vendor = result["vendor"]
    assert vendor["name"] == invoice["supplier"]["name"]
    assert vendor["iban_masked"].startswith("****") and len(vendor["iban_masked"]) == 8
    assert "iban" not in vendor


def test_get_vendor_vat_number_formatting_is_forgiving(case):
    vat = case("clean_standard")["expected_invoice"]["supplier"]["vat_number"]
    spaced = f"{vat[:2]} {vat[2:5]} {vat[5:]}"
    assert tools.get_vendor(spaced.lower())["found"]


def test_get_vendor_exact_name_is_case_insensitive(case):
    name = case("clean_standard")["expected_invoice"]["supplier"]["name"]
    assert tools.get_vendor(name.upper())["match_type"] == "name_exact"


def test_get_vendor_near_miss_name_gives_candidates_not_a_match():
    result = tools.get_vendor("Northern Star Maritime Holding")
    assert result["found"] is False and result["match_type"] == "name_fuzzy"
    assert result["candidates"][0]["vendor"]["name"] == "Northern Star Maritime Holding B.V."
    assert result["candidates"][0]["score"] >= 80


def test_get_vendor_unknown(case):
    invoice = case("unknown_vendor")["expected_invoice"]
    assert tools.get_vendor(invoice["supplier"]["vat_number"])["found"] is False
    assert tools.get_vendor(invoice["supplier"]["name"])["found"] is False


def test_get_vendor_sql_injection_is_just_a_name():
    result = tools.get_vendor("x' OR 1=1 --")
    assert result["found"] is False and result["candidates"] == []


def test_get_vendor_empty_rejected():
    with pytest.raises(ValueError):
        tools.get_vendor("   ")


# --------------------------------------------------------------------------- lookup_invoice_number


def test_duplicate_invoice_found_despite_different_format(case):
    invoice = case("duplicate")["expected_invoice"]
    result = tools.lookup_invoice_number(vendor_id_of(invoice), invoice["invoice_number"])
    assert result["count"] >= 1
    best = result["matches"][0]
    assert best["match_type"] == "same_after_normalising"
    assert best["invoice_no"] != invoice["invoice_number"]
    assert best["status"] == "approved"


def test_clean_invoice_number_has_no_match(case):
    invoice = case("clean_standard")["expected_invoice"]
    result = tools.lookup_invoice_number(vendor_id_of(invoice), invoice["invoice_number"])
    assert result["count"] == 0


def test_invoice_number_ocr_lookalike(case):
    invoice = case("duplicate")["expected_invoice"]
    vendor_id = vendor_id_of(invoice)
    original = tools.lookup_invoice_number(vendor_id, invoice["invoice_number"])["matches"][0]["invoice_no"]
    garbled = original.replace("0", "O", 1)  # an OCR engine reading 0 as O
    assert garbled != original
    result = tools.lookup_invoice_number(vendor_id, garbled)
    assert result["matches"] and result["matches"][0]["invoice_no"] == original


def test_a_misread_digit_counts_only_with_the_same_amount_and_close_dates(case):
    invoice = case("duplicate")["expected_invoice"]
    vendor_id = vendor_id_of(invoice)
    original = tools.lookup_invoice_number(vendor_id, invoice["invoice_number"])["matches"][0]
    number = original["invoice_no"]
    misread = number[:-2] + ("7" if number[-2] != "7" else "1") + number[-1]  # a digit a scan could misread
    assert tools.lookup_invoice_number(vendor_id, misread)["count"] == 0  # the number alone is not enough
    day = original["date"]
    same = tools.lookup_invoice_number(vendor_id, misread, amount=original["amount"], invoice_date=day)
    assert same["matches"] and same["matches"][0]["match_type"] == "similar_number_same_amount"
    other_amount = tools.lookup_invoice_number(vendor_id, misread, amount=original["amount"] + 5, invoice_date=day)
    assert other_amount["count"] == 0  # a different amount is a different invoice
    next_month = tools.lookup_invoice_number(vendor_id, misread, amount=original["amount"], invoice_date="2030-01-01")
    assert next_month["count"] == 0  # a recurring monthly bill is not a duplicate


def test_sequential_invoice_numbers_are_not_flagged_as_duplicates(case):
    invoice = case("clean_standard")["expected_invoice"]
    vendor_id = vendor_id_of(invoice)
    with read_only() as con:
        seen = con.execute("SELECT invoice_no FROM invoices_seen WHERE vendor_id = ? LIMIT 1", (vendor_id,)).fetchone()[0]
    prefix, digits = seen[:-1], seen[-1]
    neighbour = prefix + str((int(digits) + 3) % 10)
    assert tools.lookup_invoice_number(vendor_id, neighbour)["count"] <= 1  # only itself, if equal


def test_lookup_unknown_vendor_rejected():
    with pytest.raises(ValueError):
        tools.lookup_invoice_number("V999", "F-2026-001")


# --------------------------------------------------------------------------- find_transactions


def busy_vendor() -> str:
    with read_only() as con:
        return con.execute("SELECT vendor_id FROM transactions GROUP BY vendor_id "
                           "ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]


def test_find_transactions_caps_rows_and_paginates():
    vendor_id = busy_vendor()
    first = tools.find_transactions(vendor_id)
    assert first["returned"] == 20 and first["total"] > 20 and first["has_more"]
    second = tools.find_transactions(vendor_id, offset=first["next_offset"])
    assert {t["txn_id"] for t in first["transactions"]}.isdisjoint(t["txn_id"] for t in second["transactions"])


def test_find_transactions_never_returns_full_iban():
    for txn in tools.find_transactions(busy_vendor())["transactions"]:
        assert "iban" not in txn
        assert txn["iban_masked"].startswith("****")


def test_find_transactions_filters():
    vendor_id = busy_vendor()
    result = tools.find_transactions(vendor_id, date_from="2026-01-01", date_to="2026-03-31",
                                     amount_min=1000, amount_max=2000)
    assert result["total"] > 0
    for txn in result["transactions"]:
        assert "2026-01-01" <= txn["date"] <= "2026-03-31"
        assert 1000 <= txn["amount_eur"] <= 2000


def test_find_transactions_by_reference(case):
    invoice = case("already_paid")["expected_invoice"]
    result = tools.find_transactions(vendor_id_of(invoice), reference=invoice["po_number"])
    assert result["total"] == 1
    assert result["transactions"][0]["amount_eur"] == pytest.approx(float(invoice["total_incl_vat"]))


def test_find_transactions_rejects_bad_date_and_vendor():
    with pytest.raises(ValueError):
        tools.find_transactions(busy_vendor(), date_from="31/12/2025")
    with pytest.raises(ValueError):
        tools.find_transactions("V999")


# --------------------------------------------------------------------------- get_purchase_order


def test_purchase_order_shows_short_receipt(case):
    invoice = case("quantity_not_received")["expected_invoice"]
    po = tools.get_purchase_order(invoice["po_number"])
    assert po["found"]
    short = [line for line in po["lines"] if line["qty_received"] < line["qty_ordered"]]
    assert len(short) == 1
    assert float(invoice["lines"][po["lines"].index(short[0])]["quantity"]) == short[0]["qty_ordered"]


def test_purchase_order_matching_totals_for_clean_invoice(case):
    invoice = case("clean_standard")["expected_invoice"]
    po = tools.get_purchase_order(invoice["po_number"])
    assert po["total_excl_vat"] == pytest.approx(float(invoice["total_excl_vat"]))
    assert po["total_incl_vat"] == pytest.approx(float(invoice["total_incl_vat"]))
    assert po["paid_incl_vat"] == 0 and po["payments"] == []
    assert all(line["qty_received"] >= line["qty_ordered"] for line in po["lines"])


def test_purchase_order_reverse_charge_vendor_has_no_vat(case):
    po = tools.get_purchase_order(case("clean_reverse_be")["expected_invoice"]["po_number"])
    assert po["total_incl_vat"] == po["total_excl_vat"]


def test_purchase_order_already_paid(case):
    invoice = case("already_paid")["expected_invoice"]
    po = tools.get_purchase_order(invoice["po_number"])
    assert po["paid_incl_vat"] == pytest.approx(float(invoice["total_incl_vat"]))
    assert po["remaining_incl_vat"] == pytest.approx(0, abs=0.01)
    assert po["payments"][0]["invoice_no"] != invoice["invoice_number"]


def test_purchase_order_unknown(case):
    assert tools.get_purchase_order("PO-0000-0000")["found"] is False
    unknown = case("unknown_vendor")["expected_invoice"]
    assert tools.get_purchase_order(unknown["po_number"])["found"] is False


# --------------------------------------------------------------------------- vendor_payment_summary


def test_payment_summary_has_totals_but_no_rows():
    result = tools.vendor_payment_summary(busy_vendor(), "all")
    assert result["payment_count"] > 20 and result["total_paid"] > 0
    assert not any(isinstance(v, list) for v in result.values())


def test_payment_summary_periods():
    vendor_id = busy_vendor()
    assert tools.vendor_payment_summary(vendor_id, "2025-Q2")["date_from"] == "2025-04-01"
    assert tools.vendor_payment_summary(vendor_id, "2025")["date_to"] == "2025-12-31"
    assert tools.vendor_payment_summary(vendor_id)["period"] == "last_12_months"


def test_payment_summary_accepts_a_bare_year_as_number():
    assert tools.vendor_payment_summary(busy_vendor(), 2025)["date_from"] == "2025-01-01"


def test_payment_summary_suppresses_small_groups():
    result = tools.vendor_payment_summary(busy_vendor(), "1999")
    assert result["payment_count"] == 0 and result["total_paid"] is None and "suppressed" in result["note"]


def test_payment_summary_rejects_bad_period():
    with pytest.raises(ValueError):
        tools.vendor_payment_summary(busy_vendor(), "last decade")


# --------------------------------------------------------------------------- safety


def test_database_connection_is_read_only():
    with read_only() as con, pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM vendors")


def test_mask_iban():
    assert mask_iban("NL91 ABNA 0417 1643 00") == "****4300"
    assert mask_iban(None) is None
