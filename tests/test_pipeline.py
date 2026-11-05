"""The verification pipeline over all 20 test invoices, without Ollama.

A stub model "extracts" the ground-truth invoice, so these tests exercise the real document
reading, tool calls, fixed checks and verdict rules. The invoices whose planted error needs a
Phase 5 check (VAT rules, bank details, sanctions, anomalies) are marked as expected failures
until those checks exist (strict, so the marker must be removed when they start passing).
"""

import asyncio
import json
import time

import pytest
from stubs import TOOLS, DirectGateway, StubLLM, trusting_extraction

from agent import checks, pipeline
from agent.schemas import (
    CheckStatus,
    Discrepancy,
    ErrorType,
    Invoice,
    Severity,
    VerdictEvent,
    VerdictStatus,
)
from servers.documents import tools as document_tools

NEEDS_PHASE_5 = {"vat_arithmetic", "wrong_vat_rate", "reverse_charge_error", "invalid_vat", "missing_fields",
                 "changed_bank", "sanctioned", "unusual_amount"}


def run_pipeline(entry, **kwargs):
    invoice_json = Invoice.model_validate(entry["expected_invoice"]).model_dump_json()
    events, llm, gateway = [], StubLLM(invoice_json), DirectGateway()
    with trusting_extraction():
        verdict = asyncio.run(pipeline.verify_document(entry["invoice_id"], gateway, llm, events.append,
                                                       use_agent=False, **kwargs))
    return verdict, events, gateway


def params(ground_truth_cases):
    return [pytest.param(key, marks=pytest.mark.xfail(strict=True, reason="needs a Phase 5 check"))
            if key in NEEDS_PHASE_5 else key for key in ground_truth_cases]


ALL_CASES = ["wrong_amount", "quantity_not_received", "duplicate", "already_paid", "unknown_vendor",
             "vat_arithmetic", "wrong_vat_rate", "reverse_charge_error", "invalid_vat", "missing_fields",
             "changed_bank", "sanctioned", "unusual_amount", "prompt_injection", "clean_standard",
             "clean_mixed_rates", "clean_reverse_be", "clean_reverse_de", "clean_books", "clean_services"]


@pytest.mark.parametrize("key", params(ALL_CASES))
def test_verdict_and_error_type_match_ground_truth(inbox, case, key):
    entry = case(key)
    verdict, _events, _gateway = run_pipeline(entry)
    assert verdict.verdict.value == entry["expected_verdict"], verdict.reasoning
    expected_error = entry["error_type"]
    found = {d.type.value for d in verdict.discrepancies}
    if expected_error == "CLEAN":
        assert found == set() and all(c.status != CheckStatus.FAIL for c in verdict.checks)
    else:
        assert expected_error in found
        failed = {c.name for c in verdict.checks if c.status == CheckStatus.FAIL}
        assert set(entry["expected_failed_checks"]) <= failed


def test_event_stream_follows_the_contract(inbox, case):
    verdict, events, _ = run_pipeline(case("quantity_not_received"))
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert isinstance(events[-1], VerdictEvent) and events[-1].verdict == verdict
    kinds = [e.type for e in events]
    assert kinds.count("verdict") == 1 and "markdown_ready" in kinds and "invoice_extracted" in kinds
    stages = [(e.stage.value, e.status.value) for e in events if e.type == "stage"]
    for stage in ("convert", "extract", "checks", "verdict"):
        assert (stage, "started") in stages and (stage, "done") in stages
    assert stages.index(("convert", "done")) < stages.index(("extract", "started"))
    check_events = [e for e in events if e.type == "check_result"]
    assert [c.name for c in check_events] == [c.name for c in verdict.checks]
    assert any(e.type == "tool_call" and e.tool == "convert_document" and e.role == "extractor" for e in events)
    assert any(e.type == "tool_call" and e.tool == "get_purchase_order" and e.role == "verifier" for e in events)


def test_events_survive_a_json_round_trip(inbox, case):
    from pydantic import TypeAdapter

    from agent.schemas import Event

    _, events, _ = run_pipeline(case("duplicate"))
    adapter = TypeAdapter(Event)
    for event in events:
        assert adapter.validate_json(adapter.dump_json(event)) == event


def test_prompt_injection_is_flagged_and_changes_nothing_else(inbox, case):
    verdict, events, _ = run_pipeline(case("prompt_injection"))
    assert verdict.verdict == VerdictStatus.NEEDS_REVIEW
    injection = next(d for d in verdict.discrepancies if d.type == ErrorType.PROMPT_INJECTION)
    assert injection.severity == Severity.HIGH and "NOTE TO AI SYSTEM" in injection.found
    # the injected text asked to be marked MATCHED and to skip checks: every check still ran
    assert [c.name for c in verdict.checks] == [f.__name__.removeprefix("check_") for f in checks.FIXED_CHECKS]
    assert not any(e.type == "stage" and e.stage.value == "agent" for e in events)  # the model never saw it


def test_dependent_checks_are_skipped_for_an_unknown_vendor(inbox, case):
    verdict, _, _ = run_pipeline(case("unknown_vendor"))
    status = {c.name: c.status for c in verdict.checks}
    assert verdict.verdict == VerdictStatus.NOT_FOUND
    assert status["vendor_lookup"] == CheckStatus.FAIL
    assert status["duplicate_invoice"] == status["po_amount_match"] == CheckStatus.SKIPPED


def test_already_paid_names_the_payment_as_evidence(inbox, case):
    verdict, _, _ = run_pipeline(case("already_paid"))
    assert len(verdict.evidence_transaction_ids) == 1 and verdict.evidence_transaction_ids[0].startswith("T")


def test_same_invoice_gives_the_same_verdict_every_time(inbox, case):
    first = [run_pipeline(case("wrong_amount"))[0].model_dump(exclude={"created_at"}) for _ in range(3)]
    assert first[0] == first[1] == first[2]


def test_non_english_document_gives_needs_review_without_checks(inbox, case, monkeypatch):
    original = document_tools.convert_document

    def dutch(**kwargs):
        return {**original(**kwargs), "language": "nl", "language_confidence": 0.99}

    monkeypatch.setitem(TOOLS, "convert_document", ("documents", dutch))
    verdict, events, gateway = run_pipeline(case("clean_standard"))
    assert verdict.verdict == VerdictStatus.NEEDS_REVIEW and "not in English" in verdict.needs_review_reasons[0]
    assert verdict.checks == [] and not any(t[0] == "verifier" for t in gateway.calls)
    assert any(e.type == "stage" and e.stage.value == "extract" and e.status.value == "failed" for e in events)


def test_unknown_document_reports_an_error_event(inbox):
    events, gateway = [], DirectGateway()
    entry = {"invoice_id": "INV-999", "expected_invoice": {}}
    verdict = asyncio.run(pipeline.verify_document(entry["invoice_id"], gateway, StubLLM("{}"), events.append,
                                                   use_agent=False))
    assert verdict is None and events[-1].type == "error" and "unknown doc_id" in events[-1].message


def test_low_confidence_on_a_key_field_needs_review(inbox, case):
    entry = case("clean_standard")
    invoice = Invoice.model_validate(entry["expected_invoice"])
    invoice.total_incl_vat = invoice.total_incl_vat + 1  # a value that is nowhere in the document text
    events, gateway = [], DirectGateway()
    verdict = asyncio.run(pipeline.verify_document(entry["invoice_id"], gateway, StubLLM(invoice.model_dump_json()),
                                                   events.append, use_agent=False))
    assert verdict.verdict == VerdictStatus.NEEDS_REVIEW
    assert "total_incl_vat" in verdict.needs_review_reasons[0] and verdict.confidence < 0.98


# --------------------------------------------------------------------------- verdict rules


def _d(error: ErrorType, severity: Severity) -> Discrepancy:
    return Discrepancy(type=error, severity=severity)


@pytest.mark.parametrize(("discrepancies", "reasons", "expected"), [
    ([], [], VerdictStatus.MATCHED),
    ([_d(ErrorType.VAT_ARITHMETIC_ERROR, Severity.LOW)], [], VerdictStatus.PARTIAL_MATCH),
    ([_d(ErrorType.WRONG_AMOUNT, Severity.MEDIUM)], [], VerdictStatus.MISMATCH),
    ([_d(ErrorType.CHANGED_BANK_DETAILS, Severity.HIGH)], [], VerdictStatus.MISMATCH),
    ([_d(ErrorType.UNKNOWN_VENDOR, Severity.HIGH)], [], VerdictStatus.NOT_FOUND),
    ([_d(ErrorType.UNKNOWN_VENDOR, Severity.HIGH), _d(ErrorType.WRONG_AMOUNT, Severity.MEDIUM)], [],
     VerdictStatus.NOT_FOUND),
    ([_d(ErrorType.PROMPT_INJECTION, Severity.HIGH), _d(ErrorType.WRONG_AMOUNT, Severity.MEDIUM)], [],
     VerdictStatus.NEEDS_REVIEW),
    ([], ["unreadable scan"], VerdictStatus.NEEDS_REVIEW),
    ([_d(ErrorType.WRONG_AMOUNT, Severity.MEDIUM)], ["unreadable scan"], VerdictStatus.NEEDS_REVIEW),
])
def test_decide_verdict(discrepancies, reasons, expected):
    assert checks.decide_verdict(discrepancies, reasons) == expected


def test_the_model_can_escalate_but_never_lower_a_verdict(inbox, case):
    """A clean MATCHED invoice becomes NEEDS_REVIEW if the model asks; a MISMATCH never becomes MATCHED."""
    import agent.pipeline as p

    async def opinion(escalate):
        async def fake(*args, **kwargs):
            return p.AgentOpinion(reasoning="looks fine", escalate_to_review=escalate, escalation_reason="odd wording")
        return fake

    def run_with(entry, escalate):
        invoice_json = Invoice.model_validate(entry["expected_invoice"]).model_dump_json()
        events = []
        original = p.run_agent
        p.run_agent = asyncio.run(opinion(escalate))
        try:
            with trusting_extraction():
                return asyncio.run(p.verify_document(entry["invoice_id"], DirectGateway(), StubLLM(invoice_json),
                                                     events.append, use_agent=True))
        finally:
            p.run_agent = original

    clean, faulty = case("clean_standard"), case("wrong_amount")
    assert run_with(clean, False).verdict == VerdictStatus.MATCHED
    assert run_with(clean, True).verdict == VerdictStatus.NEEDS_REVIEW
    assert run_with(faulty, False).verdict == VerdictStatus.MISMATCH  # "looks fine" does not lower it


def test_pipeline_is_quick_without_the_model(inbox, case):
    started = time.perf_counter()
    run_pipeline(case("clean_books"))
    assert time.perf_counter() - started < 5


def test_json_dump_of_verdict_has_every_contract_field(inbox, case):
    verdict, _, _ = run_pipeline(case("wrong_amount"))
    data = json.loads(verdict.model_dump_json())
    assert {"invoice_id", "verdict", "discrepancies", "evidence_transaction_ids", "risk_flags", "checks",
            "confidence", "reasoning"} <= set(data)
