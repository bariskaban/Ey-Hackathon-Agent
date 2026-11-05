"""The evaluation runner: scoring rules, a full run over all 20 invoices, and the service functions."""

import asyncio

import pytest
from stubs import DirectGateway, trusting_extraction
from stubs_llm import DocumentAwareLLM

from agent import evaluation, service
from agent.evaluation import RunResult, score_invoice
from agent.schemas import EvalProgress, EvalReport, VerdictStatus
from servers.common.db import read_only

pytestmark = pytest.mark.usefixtures("temp_db")

V = VerdictStatus


def truth(error="WRONG_AMOUNT", verdict="MISMATCH", scanned=False):
    return {"invoice_id": "INV-1", "error_type": error, "expected_verdict": verdict, "scanned": scanned}


def run(verdict, errors=(), checks=(), seconds=10.0, calls=15, reasons=()):
    return RunResult(verdict, frozenset(errors), frozenset(checks), seconds, calls, list(reasons))


# --------------------------------------------------------------------------- scoring


def test_a_correct_faulty_invoice_passes():
    result = score_invoice(truth(), [run(V.MISMATCH, {"WRONG_AMOUNT"}, {"po_amount_match"})] * 3)
    assert result.outcome == "pass" and result.passed and result.consistent and result.error_caught == [True] * 3
    assert result.caught_by == ["po_amount_match"] and result.seconds == 10.0 and result.tool_calls == 15


def test_a_correct_clean_invoice_passes_and_a_false_finding_fails():
    clean = truth("CLEAN", "MATCHED")
    assert score_invoice(clean, [run(V.MATCHED)] * 3).outcome == "pass"
    wrong = score_invoice(clean, [run(V.MISMATCH, {"WRONG_AMOUNT"})] * 3)
    assert wrong.outcome == "fail" and "also reported WRONG_AMOUNT" in wrong.note


def test_the_right_verdict_without_the_planted_error_is_a_fail():
    result = score_invoice(truth(), [run(V.MISMATCH, {"DUPLICATE_INVOICE"})] * 3)  # right verdict, wrong reason
    assert result.outcome == "fail" and "did not report WRONG_AMOUNT" in result.note


def test_a_missed_error_is_a_fail():
    result = score_invoice(truth(), [run(V.MATCHED)] * 3)
    assert result.outcome == "fail" and result.note.startswith("expected MISMATCH, got MATCHED")


def test_a_clean_scan_sent_to_a_person_is_a_safe_fail_not_a_fail():
    clean_scan = truth("CLEAN", "MATCHED", scanned=True)
    result = score_invoice(clean_scan, [run(V.NEEDS_REVIEW, reasons=["invoice_date could not be read"])] * 3)
    assert result.outcome == "safe_fail" and not result.passed and "invoice_date" in result.note
    # the same behaviour on a text PDF is not excused: nothing there is unreadable
    assert score_invoice(truth("CLEAN", "MATCHED"), [run(V.NEEDS_REVIEW)] * 3).outcome == "fail"


def test_a_safe_fail_must_not_come_with_a_wrong_claim():
    clean_scan = truth("CLEAN", "MATCHED", scanned=True)
    assert score_invoice(clean_scan, [run(V.NEEDS_REVIEW, {"WRONG_AMOUNT"})]).outcome == "fail"


def test_a_scan_with_a_real_error_sent_to_review_with_the_error_named_is_a_safe_fail():
    scan = truth("DUPLICATE_INVOICE", "MISMATCH", scanned=True)
    result = score_invoice(scan, [run(V.NEEDS_REVIEW, {"DUPLICATE_INVOICE"})])
    assert result.outcome == "safe_fail"


def test_mixed_runs_take_the_worst_outcome_and_are_not_consistent():
    clean_scan = truth("CLEAN", "MATCHED", scanned=True)
    mixed = score_invoice(clean_scan, [run(V.MATCHED), run(V.NEEDS_REVIEW), run(V.MATCHED)])
    assert mixed.outcome == "safe_fail" and not mixed.consistent
    good = run(V.MISMATCH, {"WRONG_AMOUNT"})
    flip = score_invoice(truth(), [good, run(V.MATCHED), good])
    assert flip.outcome == "fail" and not flip.consistent and flip.error_caught == [True, False, True]


def test_injection_is_expected_to_need_review_and_scores_as_a_pass():
    injection = truth("PROMPT_INJECTION", "NEEDS_REVIEW")
    assert score_invoice(injection, [run(V.NEEDS_REVIEW, {"PROMPT_INJECTION"})] * 3).outcome == "pass"


# --------------------------------------------------------------------------- the runner


def collect(coro_gen):
    async def go():
        return [item async for item in coro_gen]

    return asyncio.run(go())


def test_every_invoice_passes_with_a_faithful_extraction_and_stays_consistent(inbox, ground_truth):
    llm = DocumentAwareLLM(ground_truth)
    with trusting_extraction():
        items = collect(evaluation.run_evaluation(DirectGateway(), llm, runs_per_invoice=2, use_agent=False))
    progress, report = items[:-1], items[-1]
    assert [p.completed for p in progress] == list(range(1, 41)) and all(isinstance(p, EvalProgress) for p in progress)
    assert isinstance(report, EvalReport) and report.total == 20 and report.runs_per_invoice == 2
    failing = [(r.invoice_id, r.note) for r in report.invoices if r.outcome != "pass"]
    assert failing == [] and report.passed == 20 and report.consistent == 20 and report.failed == 0
    assert llm.calls == 40  # one extraction per run: runs really are repeated
    assert {r.expected_error for r in report.invoices} >= {"CLEAN", "WRONG_AMOUNT", "PROMPT_INJECTION",
                                                           "DUPLICATE_INVOICE"}


def test_evaluation_never_opens_review_cases(inbox, ground_truth):
    with trusting_extraction():
        collect(evaluation.run_evaluation(DirectGateway(), DocumentAwareLLM(ground_truth), 1, use_agent=False,
                                          doc_ids=["INV-001", "INV-004", "INV-018"]))
    with read_only() as con:
        assert con.execute("SELECT COUNT(*) FROM review_cases").fetchone()[0] == 0


def test_a_subset_and_bad_arguments(inbox, ground_truth):
    with trusting_extraction():
        report = collect(evaluation.run_evaluation(DirectGateway(), DocumentAwareLLM(ground_truth), 1,
                                                   use_agent=False, doc_ids=["INV-002", "INV-003"]))[-1]
    assert [r.invoice_id for r in report.invoices] == ["INV-002", "INV-003"]
    for kwargs in ({"runs_per_invoice": 0}, {"doc_ids": ["INV-999"]}):
        with pytest.raises(ValueError):
            collect(evaluation.run_evaluation(DirectGateway(), DocumentAwareLLM(ground_truth),
                                              **{"runs_per_invoice": 1, **kwargs}))


def test_the_report_formats_as_a_table(inbox, ground_truth):
    with trusting_extraction():
        report = collect(evaluation.run_evaluation(DirectGateway(), DocumentAwareLLM(ground_truth), 1,
                                                   use_agent=False, doc_ids=["INV-001"]))[-1]
    text = evaluation.format_report(report)
    assert "INV-001" in text and "pass 1/1" in text and "stub-model" in text


# --------------------------------------------------------------------------- the service


def test_service_streams_progress_saves_the_report_and_lists_it(inbox, ground_truth, monkeypatch):
    monkeypatch.setattr(service, "LLM", lambda model=None, **kw: DocumentAwareLLM(ground_truth, model or "x"))

    async def scenario():
        await service._runtime.get()
        try:
            with trusting_extraction():
                first = [p async for p in service.run_eval("model-a", 1, doc_ids=["INV-001", "INV-002"],
                                                           use_agent=False)]
                second = [p async for p in service.run_eval("model-b", 1, doc_ids=["INV-001"], use_agent=False)]
            return first, second, await service.list_eval_runs(), await service.list_documents()
        finally:
            await service.close()

    first, second, reports, documents = asyncio.run(scenario())
    assert [p.completed for p in first] == [1, 2, 2] and first[-1].finished and not first[0].finished
    assert second[-1].finished and second[-1].run_id != first[-1].run_id
    assert [r.model for r in reports] == ["model-b", "model-a"]  # newest first
    assert reports[1].total == 2 and reports[1].passed == 2
    assert all(d.verdict is None for d in documents)  # an evaluation does not touch the UI's recorded verdicts


def test_verifying_the_same_document_again_and_again_is_never_refused(inbox, ground_truth):
    """The call budget limits one run. It used to carry over, so a later verification was refused."""
    async def scenario():
        await service._runtime.get()
        service._runtime.llm = DocumentAwareLLM(ground_truth)
        try:
            verdicts, refusals = [], 0
            with trusting_extraction():
                for _ in range(5):
                    async for event in service.verify("INV-001", use_agent=False):
                        refusals += event.type == "tool_call" and event.refused
                        if event.type == "verdict":
                            verdicts.append(event.verdict.verdict)
            return verdicts, refusals
        finally:
            await service.close()

    verdicts, refusals = asyncio.run(scenario())
    assert refusals == 0 and verdicts == [VerdictStatus.MISMATCH] * 5
