"""The recorded event streams in fixtures/events/ must always match the event contract.

The UI builds its mock service and its replay mode from these files. If someone changes
agent/schemas.py in a way that breaks them, this test fails and says which stream.
"""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from agent.schemas import Event, VerdictStatus

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "events"
INDEX = json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))
ADAPTER = TypeAdapter(Event)


def load(name: str):
    return [ADAPTER.validate_json(line) for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_the_index_lists_every_stream_and_nothing_else():
    on_disk = {p.name for p in FIXTURES.glob("*.jsonl")}
    assert {entry["file"] for entry in INDEX} == on_disk and len(INDEX) >= 4


@pytest.mark.parametrize("entry", INDEX, ids=[e["file"] for e in INDEX])
def test_each_stream_follows_the_contract(entry):
    events = load(entry["file"])
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert {e.doc_id for e in events} == {entry["doc_id"]}
    assert events[-1].type == "verdict" and events[-1].verdict.verdict.value == entry["verdict"]
    assert events[-1].verdict.review_case_id == entry["review_case_id"]
    kinds = [e.type for e in events]
    assert len(events) == entry["events"] and kinds.count("verdict") == 1
    needed_kinds = ["stage", "markdown_ready", "invoice_extracted", "check_result", "tool_call"]
    if entry["file"] != "prompt_injection.jsonl":  # the model never sees an injected invoice, so it writes no note
        needed_kinds.append("llm_note")
    for needed in needed_kinds:
        assert needed in kinds, needed
    stages = [(e.stage.value, e.status.value) for e in events if e.type == "stage"]
    expected_stages = ["convert", "extract", "checks", "verdict"]
    if entry["file"] != "prompt_injection.jsonl":
        expected_stages.insert(3, "agent")
    for stage in expected_stages:
        assert (stage, "started") in stages and (stage, "done") in stages


def test_the_scenarios_cover_the_cases_the_ui_demo_needs():
    verdicts = {entry["file"]: entry["verdict"] for entry in INDEX}
    assert verdicts["clean.jsonl"] == VerdictStatus.MATCHED.value
    assert verdicts["prompt_injection.jsonl"] == VerdictStatus.NEEDS_REVIEW.value
    assert verdicts["duplicate.jsonl"] == verdicts["reverse_charge_error.jsonl"] == VerdictStatus.MISMATCH.value
    clean = load("clean.jsonl")
    assert not any(e.type == "stage" and e.stage.value == "report" for e in clean)  # no case for a clean match
    injected = load("prompt_injection.jsonl")
    injection = next(e for e in injected if e.type == "check_result" and e.name == "injection_scan")
    assert injection.status.value == "fail" and not any(
        e.type == "stage" and e.stage.value == "agent" for e in injected)  # the model never saw the text
