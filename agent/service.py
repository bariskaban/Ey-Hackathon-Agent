"""The only interface the UI calls (todo.md section 1).

Everything here is async except `get_document_file`. `verify()` streams the events defined in
agent/schemas.py and records them to data/runs/<doc_id>.jsonl, which powers the UI's replay mode.
"""

import asyncio
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import TypeAdapter

from agent import evaluation, pipeline
from agent.gateway import Gateway
from agent.llm import LLM
from agent.schemas import (
    AuditEntry,
    DocumentInfo,
    ErrorEvent,
    EvalProgress,
    EvalReport,
    Event,
    ReviewCase,
    Stage,
    SystemStatus,
    Verdict,
)
from servers.common import audit
from servers.common.config import DATA_DIR, INVOICES_DIR, RUNS_DIR, db_path
from servers.common.db import read_only, writable
from servers.documents import manifest
from servers.documents.convert import find_tesseract
from servers.registry import SERVERS, built_servers
from servers.review_cases import store
from servers.review_cases import tools as review_case_tools

_EVENT = TypeAdapter(Event)
_END = object()


# --------------------------------------------------------------------------- runtime (started on first use)


class _Runtime:
    def __init__(self):
        self.gateway: Gateway | None = None
        self.llm: LLM | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> tuple[Gateway, LLM]:
        async with self._lock:
            if self.gateway is None:
                self.gateway = await Gateway().start()
                self.llm = LLM()
            return self.gateway, self.llm


_runtime = _Runtime()


async def close() -> None:
    """Stop the MCP servers. Call when the application shuts down."""
    if _runtime.gateway is not None:
        await _runtime.gateway.close()
    _runtime.gateway = None
    _runtime.llm = None


def _runs_dir() -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return RUNS_DIR


def _doc_info(raw: dict) -> DocumentInfo:
    verdict = None
    stored = _verdict_path(raw["doc_id"])
    if stored.exists():
        verdict = Verdict.model_validate_json(stored.read_text(encoding="utf-8")).verdict
    return DocumentInfo(doc_id=raw["doc_id"], filename=raw["filename"], type=raw["mime_type"],
                        size_bytes=raw["size_bytes"], pages=raw["pages"], verdict=verdict)


def _verdict_path(doc_id: str) -> Path:
    return _runs_dir() / f"{doc_id}.verdict.json"


# --------------------------------------------------------------------------- documents


async def list_documents() -> list[DocumentInfo]:
    return [_doc_info(raw) for raw in manifest.load()["documents"].values()]


async def upload_document(filename: str, data: bytes) -> DocumentInfo:
    """Add a file to the inbox. Only the file's base name is used; the content is checked before it is accepted."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name) or "upload"
    inbox = manifest.inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / name
    try:
        # Exclusive create: never replace a document that is already in the inbox, because a
        # replaced file would change what an earlier verdict and its audit trail refer to.
        with target.open("xb") as f:
            f.write(data)
    except FileExistsError:
        raise ValueError(
            f"{name} was not accepted: a document with this file name is already in the inbox. "
            "Rename the file and upload it again"
        ) from None
    result = manifest.sync_inbox()
    rejected = next((r for r in result["rejected"] if r["filename"] == name), None)
    if rejected:
        target.unlink(missing_ok=True)
        manifest.sync_inbox()
        raise ValueError(f"{name} was not accepted: {rejected['reason']}")
    raw = next(d for d in result["documents"].values() if d["filename"] == name)
    return _doc_info(raw)


def get_document_file(doc_id: str) -> Path:
    """The file itself, for the PDF/image preview in the UI."""
    return manifest.resolve(doc_id)[1]


# --------------------------------------------------------------------------- verification


async def verify(doc_id: str, *, use_agent: bool = True) -> AsyncIterator[Event]:
    """Verify a document and stream events. The last event is the verdict (or an error)."""
    gateway, llm = await _runtime.get()
    queue: asyncio.Queue = asyncio.Queue()
    events: list[Event] = []

    def emit(event: Event) -> None:
        events.append(event)
        queue.put_nowait(event)

    async def run() -> None:
        try:
            verdict = await pipeline.verify_document(doc_id, gateway, llm, emit, use_agent=use_agent)
            if verdict is not None:
                _verdict_path(doc_id).write_text(verdict.model_dump_json(indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - any failure is reported to the UI as an error event
            emit(ErrorEvent(seq=len(events) + 1, ts=audit.now_iso(), doc_id=doc_id,
                            message=f"{type(exc).__name__}: {exc}", stage=Stage.EXTRACT))
        finally:
            _write_run(doc_id, events)
            queue.put_nowait(_END)

    task = asyncio.create_task(run())
    try:
        while (item := await queue.get()) is not _END:
            yield item
    finally:
        await task


def _write_run(doc_id: str, events: list[Event]) -> None:
    lines = [_EVENT.dump_json(e).decode("utf-8") for e in events]
    (_runs_dir() / f"{doc_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_run(doc_id: str) -> list[Event]:
    """The recorded events of the last run, for replay."""
    path = _runs_dir() / f"{doc_id}.jsonl"
    if not path.exists():
        return []
    return [_EVENT.validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def get_verdict(doc_id: str) -> Verdict | None:
    path = _verdict_path(doc_id)
    return Verdict.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


# --------------------------------------------------------------------------- audit, status


async def get_audit_log(doc_id: str | None = None, role: str | None = None,
                        server: str | None = None) -> list[AuditEntry]:
    entries = audit.read_entries(invoice_id=doc_id, role=role, server=server)
    return [AuditEntry(**{k: v for k, v in e.items() if k in AuditEntry.model_fields}) for e in entries]


async def system_status() -> SystemStatus:
    llm = _runtime.llm or LLM()
    try:
        models = await llm.available_models()
        ollama_up = True
    except Exception:  # noqa: BLE001 - "Ollama is not reachable" is exactly what the status reports
        models, ollama_up = [], False
    try:
        find_tesseract()
        tesseract_ok = True
    except RuntimeError:
        tesseract_ok = False
    started = set(_runtime.gateway.tool_servers()) if _runtime.gateway else set()
    servers = {name: name in started for name in SERVERS if name in built_servers()}
    return SystemStatus(
        ollama_up=ollama_up, model=llm.model, model_available=llm.model in models, models=models,
        servers=servers, database_ok=db_path().exists(), tesseract_ok=tesseract_ok,
        documents_in_inbox=len(manifest.load()["documents"]))


# --------------------------------------------------------------------------- later phases


async def list_cases(status: str | None = None) -> list[ReviewCase]:
    """Review cases, most severe first, then newest first."""
    with read_only() as con:
        return [ReviewCase(**c) for c in store.list_cases(con, status)]


async def get_case(case_id: str) -> ReviewCase:
    with read_only() as con:
        return ReviewCase(**store.get_case(con, case_id))


async def set_case_status(case_id: str, status: str, reviewer: str, reason: str) -> ReviewCase:
    """Humans only. This is never exposed to an agent: it is not an MCP tool and no role can reach it."""
    with writable() as con:
        return ReviewCase(**store.set_status(con, case_id, status, reviewer, reason))


async def add_case_note(case_id: str, text: str, author: str) -> ReviewCase:
    """A person's note on a case. Notes are append-only; like set_case_status, this is never an MCP tool."""
    if not text.strip() or not author.strip():
        raise ValueError("a note needs text and an author")
    with writable() as con:
        store.get_case(con, case_id)  # raises for an unknown case
        store.add_note(con, case_id, author.strip(), text.strip())
        return ReviewCase(**store.get_case(con, case_id))


async def export_evidence_pack(case_id: str, fmt: str = "pdf") -> Path:
    """Write the case's evidence pack (pdf or xlsx) and return its path."""
    return DATA_DIR / review_case_tools.export_evidence_pack(case_id, fmt)["path"]


def _eval_dir() -> Path:
    folder = _runs_dir() / "eval"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


async def run_eval(model: str, runs_per_invoice: int = 3, *, doc_ids: list[str] | None = None,
                   use_agent: bool = True) -> AsyncIterator[EvalProgress]:
    """Verify every test invoice `runs_per_invoice` times with `model` and stream progress.

    The last item has finished=True; the full report is then in `list_eval_runs()`. Evaluation never opens
    review cases and never overwrites the recorded runs shown in the UI. The 20 test invoices are copied
    into a private, temporary inbox for the run, so they never appear in the user's inbox.
    """
    llm = LLM(model=model)
    last: EvalProgress | None = None
    with tempfile.TemporaryDirectory(prefix="vera-eval-") as staging:
        folder = Path(staging)
        for source in INVOICES_DIR.glob("INV-*.pdf"):
            shutil.copy2(source, folder / source.name)
        manifest.sync_inbox(folder)
        async with Gateway(env={"INVOICE_INBOX_DIR": str(folder)}) as gateway:
            async for item in evaluation.run_evaluation(gateway, llm, runs_per_invoice, doc_ids=doc_ids,
                                                        use_agent=use_agent):
                if isinstance(item, EvalReport):
                    (_eval_dir() / f"{item.run_id}.json").write_text(item.model_dump_json(indent=2),
                                                                      encoding="utf-8")
                    yield EvalProgress(run_id=item.run_id, model=item.model, completed=last.total if last else 0,
                                       total=last.total if last else 0, finished=True)
                else:
                    last = item
                    yield item


async def list_eval_runs() -> list[EvalReport]:
    """Saved evaluation reports, newest first."""
    reports = [EvalReport.model_validate_json(f.read_text(encoding="utf-8")) for f in _eval_dir().glob("*.json")]
    return sorted(reports, key=lambda r: (r.created_at, r.run_id), reverse=True)
