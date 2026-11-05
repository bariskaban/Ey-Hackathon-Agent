"""Inbox: upload invoices, see every document and its verdict, start verification."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nicegui import background_tasks, ui

from ui.components.widgets import error_label, page_header, severity_tag, verdict_badge
from ui.services import open_case_count, svc
from ui.theme import (DOC_STATUS_LABELS, ERROR_SEVERITY, VERDICTS, esc, eur, fmt_date, html, key, shell)

ALLOWED = (".pdf", ".png", ".jpg", ".jpeg")
MAX_MB = 10
MAX_FLAGS_SHOWN = 2
STATUS_ICONS = {"VERIFIED": "✓", "VERIFYING": "↻", "QUEUED": "…", "NOT_VERIFIED": "–"}
_batch: dict[str, Any] = {"running": False, "current": None, "index": 0, "total": 0, "done": 0, "task": None}

GRID_CSS = """
.tm-ag{--ag-background-color:var(--tm-sheet);--ag-foreground-color:var(--tm-ink);--ag-header-background-color:var(--tm-sheet);
  --ag-header-foreground-color:var(--tm-graphite);--ag-border-color:var(--tm-rule);--ag-row-border-color:var(--tm-rule);
  --ag-odd-row-background-color:var(--tm-sheet);--ag-row-hover-color:var(--tm-wash);--ag-font-size:14px;
  --ag-font-family:'Atkinson Hyperlegible Next',system-ui,sans-serif;--ag-secondary-foreground-color:var(--tm-graphite);
  height:min(72vh,760px);width:100%;border:0}
.tm-ag .ag-header-cell-text{font-weight:500;font-size:13px}
.tm-ag .ag-cell{display:flex;align-items:center;line-height:1.3}
.tm-ag .ag-right-aligned-cell{justify-content:flex-end}
.tm-stack{display:flex;flex-direction:column;gap:2px;min-width:0;max-width:100%}
.tm-stack>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tm-flags{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.tm-status{display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 10px 0 6px;border:1.5px solid var(--tm-rule-strong);
  border-radius:14px;font-size:13px;font-weight:700;color:var(--tm-ink);white-space:nowrap}
.tm-status-verifying{background:var(--tm-wash);border-color:var(--tm-ink)}
.tm-status-icon{width:16px;text-align:center}
.tm-uploader{flex:1 1 300px;min-width:240px;max-width:100%;background:var(--tm-sheet);color:var(--tm-ink);
  border:1px solid var(--tm-border)}
.tm-uploader .q-uploader__header{background:var(--tm-ink);color:var(--tm-ground)}
.tm-uploader .q-uploader__list{background:var(--tm-sheet);min-height:64px}
"""


def _cmp(field: str) -> str:
    return f"(a, b, na, nb) => String(na.data.{field}).localeCompare(String(nb.data.{field}))"


COLUMNS: list[dict[str, Any]] = [
    {"headerName": "Document", "field": "doc_html", "width": 250, "sort": "asc", ":comparator": _cmp("doc_id"), "html": True},
    {"headerName": "Vendor", "field": "vendor_html", "width": 230, ":comparator": _cmp("vendor_sort"), "html": True},
    {"headerName": "Amount", "field": "amount_text", "width": 130, "type": "rightAligned",
     ":comparator": "(a, b, na, nb) => na.data.amount_num - nb.data.amount_num"},
    {"headerName": "Invoice date", "field": "date_text", "width": 140, ":comparator": _cmp("date_iso")},
    {"headerName": "Status", "field": "status_html", "width": 150, ":comparator": _cmp("status_sort"), "html": True},
    {"headerName": "Verdict", "field": "verdict_html", "width": 190, ":comparator": _cmp("verdict_sort"), "html": True},
    {"headerName": "Risk flags", "field": "flags_html", "width": 300, ":comparator": _cmp("flags_sort"), "html": True},
    {"headerName": "Action", "field": "action_html", "width": 120, "sortable": False, "html": True},
]
HTML_COLUMNS = [i for i, c in enumerate(COLUMNS) if c.pop("html", False)]


def _status_chip(st: str | None) -> str:
    st = st or "NOT_VERIFIED"
    label = DOC_STATUS_LABELS.get(st, st)
    return (f'<span class="tm-status{" tm-status-verifying" if st == "VERIFYING" else ""}">'
            f'<span class="tm-status-icon" aria-hidden="true">{STATUS_ICONS.get(st, "")}</span>{esc(label)}</span>')


def _flags_html(d: Any, st: str | None) -> str:
    if st != "VERIFIED":
        return '<span class="tm-small">Not checked yet</span>'
    flags = [key(f) for f in (getattr(d, "risk_flags", None) or [])]
    if not flags:
        return '<span class="tm-small"><span aria-hidden="true">✓</span> No flags</span>'
    tags = [severity_tag(ERROR_SEVERITY.get(f or "", "high"), error_label(f)) for f in flags[:MAX_FLAGS_SHOWN]]
    if len(flags) > MAX_FLAGS_SHOWN:
        tags.append(f'<span class="tm-small">+{len(flags) - MAX_FLAGS_SHOWN} more</span>')
    return '<div class="tm-flags">' + "".join(tags) + "</div>"


def _action_html(doc_id: str, st: str | None) -> str:
    d = esc(doc_id)
    if st == "VERIFIED":
        return f'<a class="tm-btn tm-btn-small" href="/verdict/{d}" aria-label="Open verdict for {d}">Open</a>'
    if st == "VERIFYING":
        return f'<a class="tm-btn tm-btn-small" href="/verify/{d}" aria-label="Watch verification of {d}">Watch</a>'
    return f'<a class="tm-btn tm-btn-small" href="/verify/{d}?start=1" aria-label="Verify {d}">Verify</a>'


def _row(d: Any) -> dict[str, Any]:
    """One AG Grid row. Plain fields are text; *_html fields are escaped here."""
    st, v = key(d.status) or "NOT_VERIFIED", key(d.verdict)
    vendor = d.vendor_name or "Unknown until read"
    country = f'<span class="tm-small">{esc(d.vendor_country)}</span>' if d.vendor_country else ""
    try:
        amount_num = float(d.amount_total)
    except (TypeError, ValueError):
        amount_num = -1.0
    return {
        "doc_id": d.doc_id,
        "doc_html": (f'<div class="tm-stack"><span class="tm-num" style="font-weight:500">{esc(d.doc_id)}</span>'
                     f'<span class="tm-small" title="{esc(d.filename)}">{esc(d.filename)}</span></div>'),
        "vendor_html": f'<div class="tm-stack"><span title="{esc(vendor)}">{esc(vendor)}</span>{country}</div>',
        "vendor_sort": vendor.lower(),
        "amount_text": eur(d.amount_total, d.currency),
        "amount_num": amount_num,
        "date_text": fmt_date(d.invoice_date),
        "date_iso": str(d.invoice_date or ""),
        "status_html": _status_chip(st),
        "status_sort": DOC_STATUS_LABELS.get(st, st),
        "verdict_html": verdict_badge(v),
        "verdict_sort": VERDICTS[v]["label"] if v in VERDICTS else "~",
        "flags_html": _flags_html(d, st),
        "flags_sort": ",".join(key(f) or "" for f in (getattr(d, "risk_flags", None) or [])) or "~",
        "action_html": _action_html(d.doc_id, st),
    }


def _matches(d: Any, q: str, verdict: str, status: str) -> bool:
    v, st = key(d.verdict), key(d.status)
    if verdict == "NONE" and v:
        return False
    if verdict not in ("ALL", "NONE") and v != verdict:
        return False
    if status != "ALL" and st != status:
        return False
    hay = " ".join(str(x or "") for x in (d.doc_id, d.filename, d.vendor_name, d.invoice_number)).lower()
    return not q or q in hay


async def _verify_all(doc_ids: list[str]) -> None:
    """Verify the given invoices one after another. One run at a time, shared by all tabs."""
    _batch.update(running=True, total=len(doc_ids), index=0, done=0, task=asyncio.current_task())
    try:
        for i, doc_id in enumerate(doc_ids, 1):
            _batch.update(current=doc_id, index=i)
            async for _ in svc.verify(doc_id):
                pass
            _batch["done"] += 1
    finally:
        _batch.update(running=False, current=None, task=None)


@ui.page("/")
async def inbox_page() -> None:
    status = await svc.system_status()
    ui.add_css(GRID_CSS)
    with shell("inbox", status, await open_case_count()):
        header = ui.element("div").classes("tm-header")

        async def on_upload(e) -> None:
            name = Path(e.file.name if hasattr(e, "file") else e.name).name
            try:
                data = await e.file.read() if hasattr(e, "file") else e.content.read()
                if not name.lower().endswith(ALLOWED):
                    ui.notify(f"{name} was not added: only PDF, PNG and JPG files are accepted.", type="negative")
                    return
                if len(data) > MAX_MB * 1024 * 1024:
                    ui.notify(f"{name} was not added: it is larger than {MAX_MB} MB. Compress it or split it, then try again.",
                              type="negative")
                    return
                if not data:
                    ui.notify(f"{name} was not added: the file is empty. Choose a file that opens on your computer.",
                              type="negative")
                    return
                doc = await svc.upload_document(name, data)
            except Exception as exc:
                ui.notify(f"{name} was not added: {exc}. Try again, or choose a different file.", type="negative")
                return
            ui.notify(f"Added {name} as {doc.doc_id}.", type="positive")
            await refresh_all()

        def on_rejected(e) -> None:
            entries = e.args if isinstance(e.args, list) else [e.args]
            names = [f["file"]["name"] for f in entries
                     if isinstance(f, dict) and isinstance(f.get("file"), dict) and f["file"].get("name")]
            for name in names:
                ui.notify(f"{name} was not added: it is larger than {MAX_MB} MB. Compress it or split it, then try again.",
                          type="negative")
            if not names:
                ui.notify(f"Some files were not added: use PDF, PNG or JPG files of at most {MAX_MB} MB each.", type="negative")

        with ui.element("section").classes("tm-dropzone").props('aria-label="Upload invoices"'):
            html('<div><p style="margin:0;font-size:17px;font-weight:700">Drop invoices here to add them to the inbox</p>'
                 f'<p class="tm-small" style="margin:4px 0 0;font-size:14px">PDF, PNG or JPG, up to {MAX_MB} MB each. '
                 'You can drop several files at once. English invoices only; other languages go straight to review.</p></div>'
                 ).style("flex:1 1 320px")
            uploader = ui.upload(on_upload=on_upload, on_multi_upload=lambda _: uploader.reset(), multiple=True,
                                 auto_upload=True, max_file_size=MAX_MB * 1024 * 1024,
                                 label="Drop files here or select them with the plus button").classes("tm-uploader")
            uploader.on("rejected", on_rejected)
            verify_all_btn = ui.button("Verify all", on_click=lambda: start_all()).props("no-caps unelevated") \
                .classes("tm-btn tm-btn-primary")

        with ui.row().classes("items-center").style("gap:14px;flex-wrap:wrap") as batch_row:
            batch_label = ui.label().style("font-weight:700")
            batch_bar = ui.linear_progress(value=0, show_value=False, size="10px").props("color=dark").style("flex:1 1 200px")
            ui.button("Cancel", on_click=lambda: cancel_all()).props("no-caps outline").classes("tm-btn tm-btn-small")
        batch_row.set_visibility(False)

        @ui.refreshable
        async def summary() -> None:
            docs = await svc.list_documents()
            counts = {k: 0 for k in VERDICTS}
            for d in docs:
                if key(d.verdict) in counts:
                    counts[key(d.verdict)] += 1
            not_verified = len([d for d in docs if not d.verdict])
            cells = []
            for k, v in VERDICTS.items():
                cells.append(f'<div class="tm-card" style="padding:14px 16px;display:flex;flex-direction:column;gap:6px">'
                             f'<span style="display:inline-flex;align-items:center;gap:8px;font-size:14px;font-weight:700;color:{v["text"]}">'
                             f'<span aria-hidden="true" style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;'
                             f'border-radius:11px;border:2px solid {v["base"]};font-size:12px">{v["icon"]}</span>{v["label"]}</span>'
                             f'<span style="font-family:Zilla Slab,serif;font-size:30px;font-weight:600">{counts[k]}</span></div>')
            cells.append('<div class="tm-card" style="padding:14px 16px;display:flex;flex-direction:column;gap:6px">'
                         '<span style="font-size:14px;font-weight:700">Not verified</span>'
                         f'<span style="font-family:Zilla Slab,serif;font-size:30px;font-weight:600">{not_verified}</span></div>')
            html('<section aria-label="Verdict summary" class="tm-grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">'
                 + "".join(cells) + "</section>")
            with header:
                header.clear()
                verified = len([d for d in docs if key(d.status) == "VERIFIED"])
                html(page_header("Invoices", f"{len(docs)} documents in the inbox, {verified} verified"))

        await summary()

        with ui.element("section").classes("tm-card").props('aria-label="Documents"'):
            with ui.element("div").style("display:flex;flex-wrap:wrap;align-items:flex-end;gap:14px;padding:16px 18px;"
                                         "border-bottom:1px solid var(--tm-rule)"):
                search = ui.input("Search", placeholder="Doc id, file name, vendor or invoice number",
                                  on_change=lambda: load_grid()).props("outlined dense clearable debounce=250") \
                    .style("flex:1 1 260px")
                verdict_sel = ui.select({"ALL": "All verdicts", **{k: v["label"] for k, v in VERDICTS.items()},
                                         "NONE": "Not verified"},
                                        value="ALL", label="Verdict", on_change=lambda: load_grid()) \
                    .props("outlined dense").style("min-width:180px")
                status_sel = ui.select({"ALL": "All statuses", **DOC_STATUS_LABELS}, value="ALL", label="Status",
                                       on_change=lambda: load_grid()).props("outlined dense").style("min-width:160px")
                ui.button("Clear filters", on_click=lambda: clear_filters()).props("no-caps flat") \
                    .classes("tm-btn tm-btn-small")

            grid = ui.aggrid({
                "columnDefs": COLUMNS,
                "rowData": [],
                "defaultColDef": {"sortable": True, "resizable": True, "suppressMovable": True},
                "rowHeight": 64,
                "headerHeight": 44,
                "suppressCellFocus": True,
                "enableCellTextSelection": True,
                ":getRowId": "params => params.data.doc_id",
            }, html_columns=HTML_COLUMNS, auto_size_columns=False).classes("tm-ag")

            with ui.element("div").classes("tm-empty").style("margin:16px;flex-wrap:wrap") as empty:
                ui.label('No documents match these filters. Clear the search or choose "All verdicts" and "All statuses".')
                ui.button("Clear filters", on_click=lambda: clear_filters()).props("no-caps flat") \
                    .classes("tm-btn tm-btn-small")

        async def load_grid() -> None:
            docs = await svc.list_documents()
            q = (search.value or "").strip().lower()
            rows = [_row(d) for d in docs if _matches(d, q, verdict_sel.value, status_sel.value)]
            grid.options["rowData"] = rows
            grid.update()
            grid.set_visibility(bool(rows))
            empty.set_visibility(not rows)

        async def refresh_all() -> None:
            await load_grid()
            summary.refresh()

        def clear_filters() -> None:
            search.value, verdict_sel.value, status_sel.value = "", "ALL", "ALL"

        await load_grid()

        def signature() -> tuple:
            return (_batch["running"], _batch["current"], _batch["done"])

        seen = {"sig": signature()}

        async def start_all() -> None:
            if _batch["running"]:
                ui.notify("Verification is already running.")
                return
            todo = [d.doc_id for d in await svc.list_documents() if key(d.status) not in ("VERIFIED", "VERIFYING")]
            if not todo:
                ui.notify("Every document is already verified.")
                return
            _batch.update(running=True, total=len(todo), index=0, done=0, current=None)
            background_tasks.create(_verify_all(todo), name="verify_all")
            ui.notify(f"Verifying {len(todo)} documents one after another.")
            await tick()

        def cancel_all() -> None:
            task = _batch["task"]
            if task and not task.done():
                task.cancel()
                ui.notify("Verification cancelled. Finished invoices keep their verdicts.")

        async def tick() -> None:
            running = _batch["running"]
            batch_row.set_visibility(running)
            verify_all_btn.set_enabled(not running)
            verify_all_btn.text = "Verifying..." if running else "Verify all"
            if running:
                batch_label.text = f"Verifying {_batch['current'] or 'next invoice'}, {max(_batch['index'], 1)} of {_batch['total']}"
                batch_bar.value = _batch["done"] / max(_batch["total"], 1)
            if signature() != seen["sig"]:
                seen["sig"] = signature()
                await refresh_all()

        await tick()
        ui.timer(1.0, tick)
