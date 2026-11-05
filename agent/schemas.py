"""Shared contract between the backend and the UI.

This module is the single source of truth for names used across the project.
The models (Invoice, Verdict, Event, ...) are still to be written; see todo.md
section 1. The enums below are the agreed vocabulary.
"""

from enum import Enum


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
