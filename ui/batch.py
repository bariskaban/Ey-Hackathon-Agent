"""The "Verify all" run, shared by every page.

One batch runs at a time for the whole app, in a background task that belongs to no page. The inbox starts
it; the Live verification pages read `state` to follow it and to keep their own buttons from starting a
second run of a document the batch is about to do or is doing.
"""
from __future__ import annotations

import asyncio
from typing import Any

from nicegui import background_tasks

from ui.services import svc

state: dict[str, Any] = {"running": False, "current": None, "index": 0, "total": 0, "done": 0, "task": None}


async def _run(doc_ids: list[str]) -> None:
    """Verify the given invoices one after another."""
    state.update(running=True, total=len(doc_ids), index=0, done=0, task=asyncio.current_task())
    svc.queue_documents(doc_ids)
    try:
        for i, doc_id in enumerate(doc_ids, 1):
            state.update(current=doc_id, index=i)
            async for _ in svc.verify(doc_id):
                pass
            state["done"] += 1
    finally:
        svc.unqueue_documents(doc_ids)
        state.update(running=False, current=None, task=None)


def start(doc_ids: list[str]) -> None:
    """Begin a batch. The caller checks that none is running. The state is set at once, not when the task
    first runs, so a page that loads right after the click already sees the batch."""
    state.update(running=True, total=len(doc_ids), index=0, done=0, current=None)
    svc.queue_documents(doc_ids)
    background_tasks.create(_run(doc_ids), name="verify_all")


def cancel() -> bool:
    """Stop the batch. The invoice being verified finishes; the rest are not started."""
    task = state["task"]
    if task and not task.done():
        task.cancel()
        return True
    return False
