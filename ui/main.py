"""Vera: the web interface.

Run from the repo root:
    python -m ui.main          # then open http://localhost:8080
    vera                       # the same, after `pip install -e .`

Settings (environment variables):
    PORT=8080                  port to listen on
    HOST=127.0.0.1             address to listen on; the default keeps Vera reachable from this machine only
    UI_RELOAD=0                set to 1 while developing, to restart on file changes
    UI_STORAGE_SECRET=...      signs the browser session cookie; set your own value if others can reach the port
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make "ui" and "agent" importable when started as a script (python ui/main.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nicegui import app, ui

from agent import service
from ui import theme

# Importing the page modules registers their routes (@ui.page).
from ui.pages import audit, demo, evals, inbox, review, verdict, verify  # noqa: F401

theme.setup()


async def _start_backend() -> None:
    """Start the MCP servers now, so the first page already sees them as up."""
    await service._runtime.get()


app.on_startup(_start_backend)
app.on_shutdown(service.close)


def main() -> None:
    ui.run(
        title="Vera",
        favicon=str(Path(__file__).resolve().parent / "static" / "favicon.svg"),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8080")),
        reload=os.getenv("UI_RELOAD", "0") == "1",
        show=False,
        storage_secret=os.getenv("UI_STORAGE_SECRET", "vera-local"),
        show_welcome_message=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
