"""Demo: one button per step of the demo script, each opening the right invoice.

Every step can run live, or play back the recording of an earlier run at its original pace, so the demo still
works when the model is slow. Keys: 1 to 6 start that step, R switches between live and recording.
"""
from __future__ import annotations

from nicegui import ui

from ui.components.widgets import page_header, verdict_badge
from ui.services import open_case_count, svc
from ui.theme import esc, html, shell

# (invoice, title, what to point out, expected verdict)
STEPS = [
    ("INV-019", "A clean invoice from a Dutch supplier",
     "Every check passes. The verdict cites the matching purchase order and goods receipt.", "MATCHED"),
    ("INV-020", "A duplicate of an invoice already approved",
     ("Same supplier and invoice number, written in a different format. A fraud-type finding with evidence. "
      "INV-010 is the changed-IBAN case."), "MISMATCH"),
    ("INV-004", "A German supplier charging 21% Dutch VAT",
     "The tax rules say this business-to-business service should have been reverse charged.", "MISMATCH"),
    ("INV-016", "Food charged at 21% instead of 9%",
     "Wrong VAT rate, found by the rules and not by the model.", "MISMATCH"),
    ("INV-003", "An unusually large invoice",
     ("About eight times what this supplier usually bills. The anomaly check flags it from totals only; "
      "the agent never saw a single past payment."), "MISMATCH"),
    ("INV-018", "An invoice with instructions aimed at the AI",
     "The injection fails. The document is treated as data and sent to a person.", "NEEDS_REVIEW"),
]

KEYS = {str(i): i - 1 for i in range(1, len(STEPS) + 1)}


@ui.page("/demo")
async def demo_page() -> None:
    status = await svc.system_status()
    state = {"recording": False}

    def go(doc_id: str) -> None:
        ui.navigate.to(f"/verify/{doc_id}?start=1" + ("&replay=1" if state["recording"] else ""))

    def on_key(e) -> None:
        if not e.action.keydown:
            return
        name = str(e.key.name).lower()
        if name in KEYS:
            go(STEPS[KEYS[name]][0])
        elif name == "r":
            mode.set_value(not mode.value)

    with shell("demo", status, await open_case_count()):
        with ui.element("div").classes("vr-header"):
            html(page_header("Demo", "The demo script in order. Each step opens the right invoice and starts it."))
            with ui.element("div").style("display:flex;align-items:center;gap:12px;flex-wrap:wrap"):
                mode = ui.switch("Play recordings instead of running live",
                                 on_change=lambda e: state.update(recording=e.value))
                html(f'<span class="vr-small"><kbd>1</kbd> to <kbd>{len(STEPS)}</kbd> start a step, '
                     '<kbd>R</kbd> switches mode</span>')

        with ui.element("div").style("display:flex;flex-direction:column;gap:14px"):
            for n, (doc_id, title, point, verdict) in enumerate(STEPS, 1):
                with ui.element("section").classes("vr-card").style(
                        "display:flex;flex-wrap:wrap;align-items:center;gap:18px;padding:18px 22px"):
                    html(f'<span class="vr-demo-n" aria-hidden="true">{n}</span>')
                    html(f'<div style="flex:1 1 360px;min-width:0"><h2 class="vr-h3">{esc(title)}</h2>'
                         f'<p class="vr-small" style="margin:4px 0 0;font-size:14px;max-width:70ch">{esc(point)}</p></div>').style('flex:1 1 360px;min-width:0')
                    html(f'<div style="display:flex;flex-direction:column;gap:6px;align-items:flex-start;min-width:110px">'
                         f'<span class="vr-num">{esc(doc_id)}</span>{verdict_badge(verdict)}</div>')
                    ui.button("Start", on_click=lambda _, d=doc_id: go(d)).props("no-caps unelevated").classes(
                        "vr-btn vr-btn-primary").props(f'aria-label="Start step {n}: {esc(title)}"')

            for n, (title, point, href, label) in enumerate([
                ("Open the review case and export the evidence pack",
                 "Only a person can approve or reject. The pack is a PDF or spreadsheet with the evidence.",
                 "/review", "Open review queue"),
                ("Show what the AI saw",
                 "Every tool call, by role, with masked fields. Refused calls are visible too.",
                 "/audit", "Open audit log")], len(STEPS) + 1):
                with ui.element("section").classes("vr-card").style(
                        "display:flex;flex-wrap:wrap;align-items:center;gap:18px;padding:18px 22px"):
                    html(f'<span class="vr-demo-n" aria-hidden="true">{n}</span>')
                    html(f'<div style="flex:1 1 360px;min-width:0"><h2 class="vr-h3">{esc(title)}</h2>'
                         f'<p class="vr-small" style="margin:4px 0 0;font-size:14px;max-width:70ch">{esc(point)}</p></div>').style('flex:1 1 360px;min-width:0')
                    html(f'<a class="vr-btn" href="{href}">{label}</a>')

        ui.keyboard(on_key=on_key)
