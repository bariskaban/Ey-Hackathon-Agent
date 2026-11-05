"""Tickmark UI entry point.

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

from nicegui import ui  # noqa: E402

from ui import theme  # noqa: E402
from ui.services import MODE  # noqa: E402

# Importing the page modules registers their routes (@ui.page).
from ui.pages import audit, evals, inbox, review, verdict, verify  # noqa: E402,F401

theme.setup()


def main() -> None:
    ui.run(
        title="Tickmark",
        favicon=str(Path(__file__).resolve().parent / "static" / "favicon.svg"),
        port=int(os.getenv("PORT", "8080")),
        reload=os.getenv("UI_RELOAD", "1") == "1",
        show=False,
        storage_secret=os.getenv("UI_STORAGE_SECRET", "tickmark-local-demo"),
        show_welcome_message=True,
    )


if __name__ in {"__main__", "__mp_main__"}:
    print(f"Tickmark UI using the {MODE} service")
    main()
