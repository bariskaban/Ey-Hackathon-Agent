"""Evaluation: run every test invoice several times and compare with the known answers.

Twenty invoices are too few for meaningful percentages per error type, so results are reported
per invoice: expected against actual verdict, whether the planted error was caught and by which
check, whether repeated runs agree, time and tool calls.

Scans are scored with one extra category. A clean scan whose key field OCR could not read, and
that was therefore sent to a person, is a `safe_fail`: the system did not guess. It is reported
separately from a wrong verdict, which is a `fail`.
"""

import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agent import pipeline
from agent.gateway import Gateway
from agent.llm import LLM
from agent.schemas import (
    CheckStatus,
    EvalInvoiceResult,
    EvalProgress,
    EvalReport,
    Event,
    VerdictStatus,
)
from servers.common.config import INVOICES_DIR

SAFE_FAIL_VERDICT = VerdictStatus.NEEDS_REVIEW


@dataclass
class RunResult:
    """What one verification run produced."""

    verdict: VerdictStatus
    error_types: frozenset[str]
    failed_checks: frozenset[str]
    seconds: float
    tool_calls: int
    needs_review_reasons: list[str] = field(default_factory=list)


def load_ground_truth(path: Path | None = None) -> list[dict]:
    return json.loads((path or INVOICES_DIR / "ground_truth.json").read_text(encoding="utf-8"))


def _caught(truth: dict, run: RunResult) -> bool:
    """Was the planted error found? For a clean invoice, 'caught' means nothing was wrongly reported."""
    if truth["error_type"] == "CLEAN":
        return not run.error_types
    return truth["error_type"] in run.error_types


def _is_safe_fail(truth: dict, run: RunResult) -> bool:
    """The run sent the invoice to a person because something could not be read, without claiming a
    wrong finding. Only scans can be unreadable."""
    wrong_claims = run.error_types - ({truth["error_type"]} if truth["error_type"] != "CLEAN" else set())
    return (truth["scanned"] and run.verdict == SAFE_FAIL_VERDICT
            and VerdictStatus(truth["expected_verdict"]) != SAFE_FAIL_VERDICT and not wrong_claims)


def score_invoice(truth: dict, runs: list[RunResult]) -> EvalInvoiceResult:
    """Combine the runs of one invoice into a single result."""
    expected = VerdictStatus(truth["expected_verdict"])
    right = [r.verdict == expected and _caught(truth, r) for r in runs]
    safe = [not ok and _is_safe_fail(truth, r) for ok, r in zip(right, runs, strict=True)]
    if all(right):
        outcome, note = "pass", None
    elif all(ok or s for ok, s in zip(right, safe, strict=True)):
        outcome = "safe_fail"
        reasons = next((r.needs_review_reasons for r, s in zip(runs, safe, strict=True) if s), [])
        note = "sent to a person instead of guessing: " + (reasons[0] if reasons else "a field could not be read")
    else:
        outcome = "fail"
        bad = next(r for r, ok, s in zip(runs, right, safe, strict=True) if not ok and not s)
        note = f"expected {expected.value}, got {bad.verdict.value}"
        if truth["error_type"] != "CLEAN" and truth["error_type"] not in bad.error_types:
            note += f"; did not report {truth['error_type']}"
        extra = bad.error_types - {truth["error_type"]}
        if extra:
            note += f"; also reported {', '.join(sorted(extra))}"
    verdicts = {r.verdict for r in runs}
    findings = {r.error_types for r in runs}
    return EvalInvoiceResult(
        invoice_id=truth["invoice_id"], expected_verdict=expected, expected_error=truth["error_type"],
        verdicts=[r.verdict for r in runs], error_caught=[_caught(truth, r) for r in runs],
        caught_by=sorted(set().union(*(r.failed_checks for r in runs))),
        consistent=len(verdicts) == 1 and len(findings) == 1, outcome=outcome, passed=outcome == "pass",
        seconds=round(sum(r.seconds for r in runs) / len(runs), 1),
        tool_calls=round(sum(r.tool_calls for r in runs) / len(runs)), scanned=truth["scanned"], note=note)


def make_report(run_id: str, model: str, runs_per_invoice: int, results: list[EvalInvoiceResult],
                used_agent: bool) -> EvalReport:
    return EvalReport(
        run_id=run_id, model=model, created_at=datetime.now(UTC).isoformat(timespec="milliseconds"),
        runs_per_invoice=runs_per_invoice, invoices=results,
        passed=sum(r.outcome == "pass" for r in results), safe_failed=sum(r.outcome == "safe_fail" for r in results),
        failed=sum(r.outcome == "fail" for r in results), total=len(results),
        consistent=sum(r.consistent for r in results),
        mean_seconds=round(sum(r.seconds for r in results) / len(results), 1) if results else 0.0,
        used_agent=used_agent)


def new_run_id(model: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in model).strip("-")
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S-%f}"[:-3] + f"-{slug}"


def _run_result(verdict, events: list[Event], seconds: float) -> RunResult:
    return RunResult(
        verdict=verdict.verdict, error_types=frozenset(d.type.value for d in verdict.discrepancies),
        failed_checks=frozenset(c.name for c in verdict.checks if c.status == CheckStatus.FAIL),
        seconds=seconds, tool_calls=sum(e.type == "tool_call" for e in events),
        needs_review_reasons=verdict.needs_review_reasons)


async def run_evaluation(gateway: Gateway, llm: LLM, runs_per_invoice: int = 3, *, doc_ids: list[str] | None = None,
                         use_agent: bool = True, truth: list[dict] | None = None,
                         pipeline_fn: Callable = pipeline.verify_document) -> AsyncIterator[EvalProgress | EvalReport]:
    """Verify each invoice `runs_per_invoice` times. Yields an EvalProgress after every run, then the report.

    Evaluation runs never open review cases and never overwrite the recorded runs of the UI.
    """
    if runs_per_invoice < 1:
        raise ValueError("runs_per_invoice must be at least 1")
    entries = [t for t in (truth or load_ground_truth()) if not doc_ids or t["invoice_id"] in doc_ids]
    if not entries:
        raise ValueError("no invoices to evaluate")
    run_id, total, done = new_run_id(llm.model), len(entries) * runs_per_invoice, 0
    results: list[EvalInvoiceResult] = []
    for entry in entries:
        runs: list[RunResult] = []
        for run_index in range(1, runs_per_invoice + 1):
            events: list[Event] = []
            started = time.perf_counter()
            verdict = await pipeline_fn(entry["invoice_id"], gateway, llm, events.append, use_agent=use_agent,
                                        open_cases=False)
            seconds = time.perf_counter() - started
            if verdict is None:
                raise RuntimeError(f"{entry['invoice_id']} could not be verified: "
                                   f"{next((e.message for e in events if e.type == 'error'), 'unknown error')}")
            runs.append(_run_result(verdict, events, seconds))
            done += 1
            expected = VerdictStatus(entry["expected_verdict"])
            yield EvalProgress(run_id=run_id, model=llm.model, invoice_id=entry["invoice_id"], run_index=run_index,
                               completed=done, total=total,
                               last_result="pass" if runs[-1].verdict == expected and _caught(entry, runs[-1]) else "fail")
        results.append(score_invoice(entry, runs))
    yield make_report(run_id, llm.model, runs_per_invoice, results, use_agent)


def format_report(report: EvalReport) -> str:
    """A plain-text table for the terminal."""
    assessment = "with" if report.used_agent else "without"
    title = f"model {report.model}   {report.runs_per_invoice} runs per invoice   {assessment} the model's assessment"
    header = (f"{'invoice':8} {'expected':14} {'results':36} {'outcome':10} {'same':5} {'sec':>5} {'calls':>5}"
              "  caught by")
    lines = [title, "", header]
    for r in report.invoices:
        got = ",".join(v.value.replace("NEEDS_REVIEW", "REVIEW").replace("PARTIAL_MATCH", "PARTIAL")
                       for v in r.verdicts)
        same = "yes" if r.consistent else "NO"
        lines.append(f"{r.invoice_id:8} {r.expected_verdict.value:14} {got[:36]:36} {r.outcome:10} {same:5} "
                     f"{r.seconds:5.1f} {r.tool_calls:5}  {', '.join(r.caught_by)}")
        if r.note:
            lines.append(f"         -> {r.note}")
    summary = (f"pass {report.passed}/{report.total}   safe fail {report.safe_failed}   fail {report.failed}   "
               f"consistent across runs {report.consistent}/{report.total}   mean {report.mean_seconds}s per run")
    return "\n".join([*lines, "", summary])
