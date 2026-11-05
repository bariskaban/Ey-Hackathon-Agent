"""tax_rules, vendor_checks and anomaly_stats: the fixed-rule tools."""

import copy
import json
import sqlite3

import pytest

from servers.anomaly_stats import tools as anomaly
from servers.common.config import INVOICES_DIR
from servers.common.db import writable
from servers.tax_rules import tools as tax
from servers.vendor_checks import tools as vendors


def all_vendors():
    from servers.common.db import read_only

    with read_only() as con:
        return [dict(r) for r in con.execute("SELECT * FROM vendors")]


# --------------------------------------------------------------------------- tax_rules: VAT numbers


def test_vendor_vat_numbers_are_valid_in_every_country():
    for vendor in all_vendors():
        assert tax.validate_vat_number(vendor["vat_number"])["valid"], vendor["vat_number"]


def test_vat_number_formatting_is_forgiving_and_garbage_is_rejected():
    assert tax.validate_vat_number("nl 3674.1825-3 b01")["valid"]
    for bad in ("NL857412369B01", "NL12345", "XX123456789", "", "hello world"):
        assert not tax.validate_vat_number(bad)["valid"], bad


def test_ocr_repair_fixes_a_single_lookalike_misread_only_when_asked():
    misread = "NL222998994801"  # B01 read as 801
    assert not tax.validate_vat_number(misread)["valid"]
    repaired = tax.validate_vat_number(misread, repair_ocr_errors=True)
    assert repaired["valid"] and repaired["normalized"] == "NL222998994B01" and repaired["repaired_from"] == misread


def test_ocr_repair_never_invents_a_valid_number_from_a_wrong_one():
    wrong = "NL222998995B01"  # a genuinely different (invalid) number: no look-alike swap makes it valid
    result = tax.validate_vat_number(wrong, repair_ocr_errors=True)
    assert not result["valid"] and result["repaired_from"] is None


# --------------------------------------------------------------------------- tax_rules: rates and treatment


def test_vat_rates_follow_the_table_and_its_validity_dates():
    assert tax.get_vat_rate("standard", "2026-10-01")["rate"] == 21.0
    assert tax.get_vat_rate("reduced_food", "2026-10-01")["rate"] == 9.0
    assert tax.get_vat_rate("reduced_books", "2026-10-01")["rate"] == 9.0
    assert tax.get_vat_rate("zero", "2026-10-01")["rate"] == 0.0
    assert tax.get_vat_rate("reduced_food", "2018-06-01")["rate"] == 6.0  # before the 2019 change
    with pytest.raises(ValueError):
        tax.get_vat_rate("luxury", "2026-10-01")
    with pytest.raises(ValueError):
        tax.get_vat_rate("standard", "01-10-2026")


def test_expected_vat_treatment():
    assert tax.expected_vat_treatment("NL", "NL", "NL367418253B01")["treatment"] == "domestic"
    services = tax.expected_vat_treatment("DE", "NL", "NL367418253B01", "services")
    assert services["treatment"] == "reverse_charge" and services["invoice_rate"] == 0.0
    assert any("Reverse charge" in item for item in services["must_show"])
    assert tax.expected_vat_treatment("BE", "NL", "NL367418253B01", "goods")["treatment"] == "intra_community_zero_rate"
    assert tax.expected_vat_treatment("DE", "NL", None)["treatment"] == "manual_review"  # no buyer VAT number
    assert tax.expected_vat_treatment("US", "NL", "NL367418253B01")["treatment"] == "manual_review"
    with pytest.raises(ValueError):
        tax.expected_vat_treatment("DE", "NL", "x", "gadgets")


# --------------------------------------------------------------------------- tax_rules: requirements and arithmetic


def test_clean_invoices_meet_the_requirements_and_add_up(ground_truth):
    for entry in ground_truth:
        if entry["error_type"] == "CLEAN":
            invoice = entry["expected_invoice"]
            assert tax.check_invoice_requirements(invoice)["ok"], entry["case"]
            assert tax.validate_invoice_math(invoice)["ok"], entry["case"]


def test_missing_fields_are_named(case):
    result = tax.check_invoice_requirements(case("missing_fields")["expected_invoice"])
    assert not result["ok"] and {m["field"] for m in result["missing"]} == {"supply_date", "supplier.kvk_number"}


def test_reverse_charge_needs_the_buyer_vat_number_and_foreign_suppliers_need_no_kvk(case):
    invoice = copy.deepcopy(case("clean_reverse_be")["expected_invoice"])
    assert tax.check_invoice_requirements(invoice)["ok"]  # a Belgian supplier has no KvK number
    invoice["buyer"]["vat_number"] = None
    assert [m["field"] for m in tax.check_invoice_requirements(invoice)["missing"]] == ["buyer.vat_number"]


def test_invoice_math_finds_the_planted_error_and_only_that(case):
    result = tax.validate_invoice_math(case("vat_arithmetic")["expected_invoice"])
    assert [e["field"] for e in result["errors"]] == ["total_incl_vat"]
    assert float(result["errors"][0]["found"]) - float(result["errors"][0]["expected"]) == pytest.approx(5.40)


def test_invoice_math_checks_lines_vat_per_rate_and_totals(case):
    invoice = copy.deepcopy(case("clean_mixed_rates")["expected_invoice"])
    invoice["lines"][0]["line_total"] = "1.00"
    invoice["vat_breakdown"][0]["vat"] = "0.01"
    invoice["total_excl_vat"] = "5.00"
    fields = {e["field"] for e in tax.validate_invoice_math(invoice)["errors"]}
    assert {"lines[0].line_total", "vat_breakdown[0].vat", "total_excl_vat"} <= fields


def test_invoice_math_tolerates_a_cent(case):
    invoice = copy.deepcopy(case("clean_standard")["expected_invoice"])
    invoice["total_incl_vat"] = str(float(invoice["total_incl_vat"]) + 0.01)
    assert tax.validate_invoice_math(invoice)["ok"]


def test_tax_tools_reject_non_objects():
    with pytest.raises(ValueError):
        tax.validate_invoice_math("not an invoice")
    with pytest.raises(ValueError):
        tax.check_invoice_requirements(None)


# --------------------------------------------------------------------------- vendor_checks


def test_iban_validation_and_bank_names():
    vendor = next(v for v in all_vendors() if v["country"] == "NL")
    result = vendors.validate_iban(vendor["iban"])
    assert result["valid"] and result["bank_name"] and result["iban_masked"] == "****" + vendor["iban"][-4:]
    assert "iban" not in result
    assert not vendors.validate_iban(vendor["iban"][:-1] + "0")["valid"] or vendor["iban"][-1] == "0"
    assert vendors.validate_iban("NL02ABNA0123456789")["bank_name"] == "ABN AMRO"
    assert not vendors.validate_iban("hello")["valid"]
    for v in all_vendors():
        assert vendors.validate_iban(v["iban"])["valid"]


def test_bank_details_comparison(case):
    invoice = case("changed_bank")["expected_invoice"]
    vendor = next(v for v in all_vendors() if v["vat_number"] == invoice["supplier"]["vat_number"])
    assert vendors.compare_bank_details(vendor["id"], vendor["iban"])["result"] == "match"
    different = vendors.compare_bank_details(vendor["id"], invoice["supplier"]["iban"])
    assert different["result"] == "different" and different["invoice_iban_valid"] and not different["matches"]
    assert different["vendor_iban_masked"] == "****" + vendor["iban"][-4:]
    misread = vendors.compare_bank_details(vendor["id"], vendor["iban"][:-1])  # a digit dropped: invalid
    assert misread["result"] == "ocr_misread_likely" and not misread["invoice_iban_valid"]


def test_a_valid_but_different_iban_is_never_treated_as_a_misread(case):
    vendor = all_vendors()[0]
    other = next(v["iban"] for v in all_vendors() if v["country"] == vendor["country"] and v["id"] != vendor["id"])
    assert vendors.compare_bank_details(vendor["id"], other)["result"] == "different"


def test_recent_bank_changes_are_reported(temp_db):
    vendor = all_vendors()[0]
    assert vendors.compare_bank_details(vendor["id"], vendor["iban"])["vendor_iban_changed_recently"] is False
    with writable() as con:
        con.execute("UPDATE vendors SET iban_changed_at = '2026-10-20' WHERE id = ?", (vendor["id"],))
    assert vendors.compare_bank_details(vendor["id"], vendor["iban"])["vendor_iban_changed_recently"] is True


def test_sanctions_screening_finds_the_variant_and_not_the_innocent(case):
    planted = case("sanctioned")["expected_invoice"]["supplier"]["name"]
    result = vendors.screen_sanctions(planted)
    assert result["match_count"] >= 1 and result["matches"][0]["listed_name"] == "Northern Star Maritime Holdings"
    assert result["matches"][0]["score"] >= 85 and "never rejects" in result["note"]
    # the sample invoices plant a second sanctioned vendor, on purpose
    samples = json.loads((INVOICES_DIR / "sample_ground_truth.json").read_text(encoding="utf-8"))
    planted_names = {planted} | {s["expected_invoice"]["supplier"]["name"] for s in samples
                                 if s["slug"] == "sanctioned-vendor"}
    assert vendors.screen_sanctions("Volga Steel Exports B.V.")["match_count"] >= 1
    for vendor in all_vendors():
        if vendor["name"] not in planted_names:
            assert vendors.screen_sanctions(vendor["name"])["match_count"] == 0, vendor["name"]
    assert vendors.screen_sanctions("Rijnmond Kantoorwaren B.V.")["match_count"] == 0


def test_sanctions_screening_ignores_legal_forms_and_punctuation():
    assert vendors.screen_sanctions("NORTHERN STAR MARITIME HOLDINGS LTD.")["match_count"] == 1
    assert vendors.screen_sanctions("Aurora Petrochem")["match_count"] >= 1  # an alias
    with pytest.raises(ValueError):
        vendors.screen_sanctions("  ")


def test_employee_overlap_is_reported_without_employee_details(temp_db):
    for vendor in all_vendors():
        assert vendors.vendor_employee_overlap(vendor["id"])["overlap"] is False
    vendor = all_vendors()[3]
    with writable() as con:
        con.execute("INSERT INTO employees VALUES ('E900', 'Someone Else', ?, ?)", (vendor["address"], vendor["iban"]))
    result = vendors.vendor_employee_overlap(vendor["id"])
    assert result == {"vendor_id": vendor["id"], "overlap": True, "matched_fields": ["iban", "address"]}
    with pytest.raises(ValueError):
        vendors.vendor_employee_overlap("V999")


# --------------------------------------------------------------------------- anomaly_stats


def vendor_of(invoice):
    supplier = invoice["supplier"]
    return next(v for v in all_vendors() if v["vat_number"] == supplier["vat_number"] or v["name"] == supplier["name"])


def test_amount_profile_scores_the_planted_amount_far_above_normal(case):
    invoice = case("unusual_amount")["expected_invoice"]
    profile = anomaly.vendor_amount_profile(vendor_of(invoice)["id"], float(invoice["total_incl_vat"]))
    assert profile["z_score"] >= 3 and profile["times_median"] >= 3 and profile["percentile"] == 100.0
    assert not any(isinstance(v, list) for v in profile.values())  # scores, never rows


def test_ordinary_invoices_are_not_unusual(ground_truth):
    for entry in ground_truth:
        if entry["case"] != "unusual_amount" and entry["error_type"] != "UNKNOWN_VENDOR":
            invoice = entry["expected_invoice"]
            profile = anomaly.vendor_amount_profile(vendor_of(invoice)["id"], float(invoice["total_incl_vat"]))
            assert profile["z_score"] < 3 or profile["times_median"] < 3, entry["case"]


def test_small_histories_are_suppressed(temp_db):
    vendor = all_vendors()[0]
    with writable() as con:
        con.execute("DELETE FROM transactions WHERE vendor_id = ? AND txn_id NOT IN "
                    "(SELECT txn_id FROM transactions WHERE vendor_id = ? LIMIT 3)", (vendor["id"], vendor["id"]))
    profile = anomaly.vendor_amount_profile(vendor["id"], 100.0)
    assert profile["suppressed"] and "z_score" not in profile


def test_split_invoice_check(temp_db):
    vendor = all_vendors()[0]
    assert anomaly.split_invoice_check(vendor["id"], 7, 5000, "2026-10-20")["possible_split"] is False
    with writable() as con:
        con.executemany("INSERT INTO invoices_seen VALUES (?, ?, ?, ?, ?, 'approved')",
                        [(vendor["id"], "S-1", "S1", 4800.0, "2026-10-18"), (vendor["id"], "S-2", "S2", 4650.0, "2026-10-19")])
    result = anomaly.split_invoice_check(vendor["id"], 7, 5000, "2026-10-20")
    assert result["count"] == 2 and result["possible_split"] and result["total"] == 9450.0
    assert anomaly.split_invoice_check(vendor["id"], 1, 5000, "2026-10-20")["count"] == 1
    assert anomaly.split_invoice_check(vendor["id"], 1, 5000, "2026-10-20")["total"] is None  # one invoice: no total
    for bad in ({"window_days": 0, "threshold": 5000}, {"window_days": 7, "threshold": 0}):
        with pytest.raises(ValueError):
            anomaly.split_invoice_check(vendor["id"], **bad)


def test_date_flags():
    assert anomaly.date_flags("2026-10-25")["weekend"] is True
    assert anomaly.date_flags("2026-10-21")["weekend"] is False
    assert anomaly.date_flags("2026-12-25")["public_holiday"] == "Christmas Day"
    assert anomaly.date_flags("2026-11-06")["future"] is True and anomaly.date_flags("2026-11-05")["future"] is False
    with pytest.raises(ValueError):
        anomaly.date_flags("25/10/2026")


def test_unknown_vendor_is_an_error_everywhere():
    for call in (lambda: anomaly.vendor_amount_profile("V999", 1.0), lambda: anomaly.split_invoice_check("V999", 7, 5000),
                 lambda: vendors.compare_bank_details("V999", "NL02ABNA0123456789")):
        with pytest.raises(ValueError, match="unknown vendor_id"):
            call()


def test_read_only_servers_cannot_write(temp_db):
    from servers.common.db import read_only

    with read_only() as con, pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO sanctions VALUES ('X', 'Y', '', 'NL', '2020-01-01')")
