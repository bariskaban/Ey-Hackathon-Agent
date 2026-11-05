"""The service the UI calls: streaming, run recording and replay, uploads, audit log."""

import asyncio

import pytest
from stubs import StubLLM, trusting_extraction

from agent import service
from agent.schemas import Invoice, VerdictStatus
from servers.common.config import INVOICES_DIR
from servers.documents import manifest


def test_verify_streams_records_and_replays(inbox, case):
    entry = case("quantity_not_received")
    stub = StubLLM(Invoice.model_validate(entry["expected_invoice"]).model_dump_json())

    async def scenario():
        await service._runtime.get()  # starts the real MCP servers
        service._runtime.llm = stub
        try:
            with trusting_extraction():
                events = [event async for event in service.verify(entry["invoice_id"], use_agent=False)]
            return (events, await service.get_verdict(entry["invoice_id"]), await service.list_documents(),
                    await service.get_audit_log(doc_id=entry["invoice_id"]),
                    await service.get_audit_log(doc_id=entry["invoice_id"], role="extractor"),
                    await service.get_audit_log(server="transactions"))
        finally:
            await service.close()

    events, verdict, documents, audit_all, audit_extractor, audit_tx = asyncio.run(scenario())

    assert events[-1].type == "verdict" and events[-1].verdict == verdict
    assert verdict.verdict == VerdictStatus.MISMATCH
    assert service.read_run(entry["invoice_id"]) == events  # the recording replays exactly
    listed = {d.doc_id: d for d in documents}
    assert len(listed) == 20 and listed[entry["invoice_id"]].verdict == VerdictStatus.MISMATCH

    assert {e.role for e in audit_all} == {"extractor", "verifier"}
    assert [e.tool for e in audit_extractor] == ["convert_document"]
    assert audit_tx and all(e.server == "transactions" for e in audit_tx)
    tool_events = [e for e in events if e.type == "tool_call"]
    assert len(tool_events) == len(audit_all)  # every call in the stream is in the audit log, and vice versa


def test_system_status_reports_ollama_and_the_data(inbox, monkeypatch):
    from agent.llm import LLM

    async def down(self):
        raise ConnectionError("not running")

    async def up(self):
        return ["qwen3:8b", "llama3.1:8b"]

    monkeypatch.setattr(LLM, "available_models", down)
    status = asyncio.run(service.system_status())
    assert not status.ollama_up and not status.model_available and status.models == []
    assert status.database_ok and status.documents_in_inbox == 20 and status.tesseract_ok

    monkeypatch.setattr(LLM, "available_models", up)
    status = asyncio.run(service.system_status())
    assert status.ollama_up and status.model_available == (status.model in ["qwen3:8b", "llama3.1:8b"])
    assert set(status.servers) == {"documents", "transactions"} and not any(status.servers.values())  # not started


def test_unknown_document_streams_an_error(inbox):
    async def scenario():
        await service._runtime.get()
        service._runtime.llm = StubLLM("{}")
        try:
            return [event async for event in service.verify("INV-404", use_agent=False)]
        finally:
            await service.close()

    events = asyncio.run(scenario())
    assert events[-1].type == "error" and "unknown doc_id" in events[-1].message


def test_upload_accepts_a_valid_pdf_and_rejects_the_rest(inbox):
    pdf = (INVOICES_DIR / "INV-001.pdf").read_bytes()

    async def scenario():
        accepted = await service.upload_document("INV-777.pdf", pdf)
        with pytest.raises(ValueError, match="not accepted"):
            await service.upload_document("notes.txt", b"hello")
        with pytest.raises(ValueError, match="not accepted"):
            await service.upload_document("fake.pdf", b"not a pdf")
        traversal = await service.upload_document("../../outside.pdf", pdf)
        return accepted, traversal

    accepted, traversal = asyncio.run(scenario())
    assert accepted.doc_id == "INV-777" and accepted.pages == 1 and accepted.verdict is None
    assert traversal.filename == "outside.pdf" and not (manifest.inbox_dir().parent.parent / "outside.pdf").exists()
    assert service.get_document_file("INV-777").parent == manifest.inbox_dir().resolve()
    assert not (manifest.inbox_dir() / "notes.txt").exists()


def test_functions_for_later_phases_say_so():
    async def scenario():
        for call in (service.list_cases(), service.get_case("C-1"), service.set_case_status("C-1", "approved", "a", "b"),
                     service.export_evidence_pack("C-1"), service.list_eval_runs()):
            with pytest.raises(NotImplementedError):
                await call

    asyncio.run(scenario())
