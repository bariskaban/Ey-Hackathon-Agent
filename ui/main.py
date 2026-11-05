"""Vera UI entry point.

Run from the repo root:
    uv run python -m ui.main                       # mock data (default)
    INVOICE_SERVICE=real uv run python -m ui.main  # the real agent

Then open http://localhost:8080
Optional: MOCK_SPEED=2 runs the mock verification twice as fast.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make "ui" and "agent" importable when started as a script (python ui/main.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nicegui import app, ui

from ui import theme

# Importing the page modules registers their routes (@ui.page).
from ui.pages import audit, evals, inbox, review, verdict, verify  # noqa: F401
from ui.services import MODE

theme.setup()

if MODE == "real":
    from agent import service

    async def _start_backend() -> None:
        """Start the MCP servers now, so the first page already sees them as up."""
        await service._runtime.get()

    app.on_startup(_start_backend)
    app.on_shutdown(service.close)


def main() -> None:
    ui.run(
        title="Vera",
        favicon=str(Path(__file__).resolve().parent / "static" / "favicon.svg"),
        port=int(os.getenv("PORT", "8080")),
        reload=os.getenv("UI_RELOAD", "1") == "1",
        show=False,
        storage_secret=os.getenv("UI_STORAGE_SECRET", "vera-local-demo"),
        show_welcome_message=True,
    )


if __name__ in {"__main__", "__mp_main__"}:
    print(f"Vera UI using the {MODE} service")
    main()
