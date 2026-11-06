"""Inbox: upload invoices, see every document and its verdict, start verification."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from nicegui import ui

from ui import batch
from ui.components.widgets import error_label, page_header, severity_tag, verdict_badge
from ui.services import open_case_count, svc
from ui.theme import (
    DOC_STATUS_LABELS,
    ERROR_SEVERITY,
    VERDICTS,
    esc,
    eur,
    fmt_date,
    html,
    key,
    shell,
)

ALLOWED = (".pdf", ".png", ".jpg", ".jpeg")
MAX_MB = 10
MAX_FLAGS_SHOWN = 2
MAX_FAILURES_SHOWN = 3  # failed files named in the one summary message of an upload
UPLOAD_QUIET_SECONDS = 0.8  # an upload batch is reported once no file has arrived for this long
STATUS_ICONS = {"VERIFIED": "✓", "VERIFYING": "↻", "QUEUED": "…", "NOT_VERIFIED": "–"}
_batch = batch.state

GRID_CSS = """
.vr-ag{--ag-background-color:var(--vr-sheet);--ag-foreground-color:var(--vr-ink);--ag-header-background-color:var(--vr-sheet);
  --ag-header-foreground-color:var(--vr-graphite);--ag-border-color:var(--vr-rule);--ag-row-border-color:var(--vr-rule);
  --ag-odd-row-background-color:var(--vr-sheet);--ag-row-hover-color:var(--vr-accent-soft);--ag-font-size:14px;
  --ag-font-family:var(--vr-font);--ag-secondary-foreground-color:var(--vr-graphite);
  height:min(72vh,760px);width:100%;border:0}
.vr-ag .ag-header-cell-text{font-weight:500;font-size:13px}
.vr-ag .ag-cell{display:flex;align-items:center;line-height:1.3}
.vr-ag .ag-right-aligned-cell{justify-content:flex-end}
.vr-stack{display:flex;flex-direction:column;gap:2px;min-width:0;max-width:100%}
.vr-stack>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vr-flags{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.vr-status{display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 10px 0 6px;border:1.5px solid var(--vr-rule-strong);
  border-radius:14px;font-size:13px;font-weight:700;color:var(--vr-ink);white-space:nowrap}
.vr-status-verifying{background:var(--vr-accent-soft);border-color:var(--vr-ink)}
.vr-status-icon{width:16px;text-align:center}
.vr-uploader{flex:1 1 300px;min-width:240px;max-width:100%;background:var(--vr-sheet);color:var(--vr-ink);
  border:1px solid var(--vr-border)}
.vr-uploader .q-uploader__header{background:var(--vr-accent);color:var(--vr-on-accent)}
.vr-uploader .q-uploader__header .q-btn{color:var(--vr-on-accent)}
.vr-uploader .q-uploader__list{background:var(--vr-sheet);min-height:64px}
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
    {"headerName": "Action", "field": "action_html", "width": 110, "sortable": False, "html": True, "pinned": "right"},
    {"headerName": "Delete", "field": "delete_html", "width": 100, "sortable": False, "html": True, "pinned": "right"},
]
HTML_COLUMNS = [i for i, c in enumerate(COLUMNS) if c.pop("html", False)]


def _status_chip(st: str | None) -> str:
    st = st or "NOT_VERIFIED"
    label = DOC_STATUS_LABELS.get(st, st)
    return (f'<span class="vr-status{" vr-status-verifying" if st == "VERIFYING" else ""}">'
            f'<span class="vr-status-icon" aria-hidden="true">{STATUS_ICONS.get(st, "")}</span>{esc(label)}</span>')


def _flags_html(d: Any, st: str | None) -> str:
    if st != "VERIFIED":
        return '<span class="vr-small">Not checked yet</span>'
    flags = [key(f) for f in (getattr(d, "risk_flags", None) or [])]
    if not flags:
        return '<span class="vr-small"><span aria-hidden="true">✓</span> No flags</span>'
    tags = [severity_tag(ERROR_SEVERITY.get(f or "", "high"), error_label(f)) for f in flags[:MAX_FLAGS_SHOWN]]
    if len(flags) > MAX_FLAGS_SHOWN:
        tags.append(f'<span class="vr-small">+{len(flags) - MAX_FLAGS_SHOWN} more</span>')
    return '<div class="vr-flags">' + "".join(tags) + "</div>"


def _action_html(doc_id: str, st: str | None) -> str:
    d = esc(doc_id)
    if st == "VERIFIED":
        return f'<a class="vr-btn vr-btn-small" href="/verdict/{d}" aria-label="Open verdict for {d}">Open</a>'
    if st == "VERIFYING":
        return f'<a class="vr-btn vr-btn-small" href="/verify/{d}" aria-label="Watch verification of {d}">Watch</a>'
    if st == "QUEUED":
        return f'<span class="vr-small" aria-label="{d} is waiting for Verify all">Waiting</span>'
    return f'<a class="vr-btn vr-btn-small" href="/verify/{d}?start=1" aria-label="Verify {d}">Verify</a>'


def _row(d: Any) -> dict[str, Any]:
    """One AG Grid row. Plain fields are text; *_html fields are escaped here."""
    st, v = key(d.status) or "NOT_VERIFIED", key(d.verdict)
    vendor = d.vendor_name or "Unknown until read"
    country = f'<span class="vr-small">{esc(d.vendor_country)}</span>' if d.vendor_country else ""
    try:
        amount_num = float(d.amount_total)
    except (TypeError, ValueError):
        amount_num = -1.0
    return {
        "doc_id": d.doc_id,
        "filename": d.filename,
        "doc_html": (f'<div class="vr-stack"><span class="vr-num" style="font-weight:500">{esc(d.doc_id)}</span>'
                     f'<span class="vr-small" title="{esc(d.filename)}">{esc(d.filename)}</span></div>'),
        "vendor_html": f'<div class="vr-stack"><span title="{esc(vendor)}">{esc(vendor)}</span>{country}</div>',
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
        "delete_html": (f'<button type="button" class="vr-btn vr-btn-small" aria-label="Delete {esc(d.doc_id)}">'
                        'Delete</button>'),
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


@ui.page("/")
async def inbox_page() -> None:
    status = await svc.system_status()
    ui.add_css(GRID_CSS)
    with shell("inbox", status, await open_case_count()):
        header = ui.element("div").classes("vr-header")

        # Uploading many files at once calls on_upload once per file. Each file is noted here and the
        # whole batch is reported in one message, so 30 files never mean 30 toasts or 30 page refreshes.
        report: dict[str, Any] = {"added": [], "failed": [], "last": 0.0}

        def note(kind: str, message: str) -> None:
            report[kind].append(message)
            report["last"] = time.monotonic()

        async def on_upload(e) -> None:
            name = Path(e.file.name if hasattr(e, "file") else e.name).name
            try:
                data = await e.file.read() if hasattr(e, "file") else e.content.read()
                if not name.lower().endswith(ALLOWED):
                    note("failed", f"{name} was not added: only PDF, PNG and JPG files are accepted.")
                    return
                if len(data) > MAX_MB * 1024 * 1024:
                    note("failed", f"{name} was not added: it is larger than {MAX_MB} MB. "
                                   "Compress it or split it, then try again.")
                    return
                if not data:
                    note("failed", f"{name} was not added: the file is empty. Choose a file that opens on your computer.")
                    return
                doc = await svc.upload_document(name, data)
            except Exception as exc:  # noqa: BLE001 - reported to the user, never a crash
                note("failed", f"{name} was not added: {exc}. Try again, or choose a different file.")
                return
            note("added", f"Added {name} as {doc.doc_id}.")

        async def report_uploads() -> None:
            """Tell the user how the batch went, once, after the last file has arrived."""
            if not (report["added"] or report["failed"]) or time.monotonic() - report["last"] < UPLOAD_QUIET_SECONDS:
                return
            added, failed = list(report["added"]), list(report["failed"])
            report["added"].clear()
            report["failed"].clear()
            if added:
                await refresh_all()
                ui.notify(added[0] if len(added) == 1 else f"Added {len(added)} documents to the inbox.", type="positive")
            if failed:
                if len(failed) == 1:
                    message = failed[0]
                else:
                    shown = " ".join(failed[:MAX_FAILURES_SHOWN])
                    more = f" And {len(failed) - MAX_FAILURES_SHOWN} more." if len(failed) > MAX_FAILURES_SHOWN else ""
                    message = f"{len(failed)} files were not added. {shown}{more}"
                ui.notify(message, type="negative", multi_line=True, close_button="Dismiss", timeout=15000)

        def on_rejected(e) -> None:
            entries = e.args if isinstance(e.args, list) else [e.args]
            names = [f["file"]["name"] for f in entries
                     if isinstance(f, dict) and isinstance(f.get("file"), dict) and f["file"].get("name")]
            for name in names:
                note("failed", f"{name} was not added: it is larger than {MAX_MB} MB. Compress it or split it, then try again.")
            if not names:
                note("failed", f"Some files were not added: use PDF, PNG or JPG files of at most {MAX_MB} MB each.")

        with ui.element("section").classes("vr-dropzone").props('aria-label="Upload invoices"'):
            html('<div><p style="margin:0;font-size:17px;font-weight:700">Drop invoices here to add them to the inbox</p>'
                 f'<p class="vr-small" style="margin:4px 0 0;font-size:14px">PDF, PNG or JPG, up to {MAX_MB} MB each. '
                 'You can drop several files at once. English invoices only; other languages go straight to review.</p></div>'
                 ).style("flex:1 1 320px")
            uploader = ui.upload(on_upload=on_upload, on_multi_upload=lambda _: uploader.reset(), multiple=True,
                                 auto_upload=True, max_file_size=MAX_MB * 1024 * 1024,
                                 label="Drop files here or select them with the plus button").classes("vr-uploader")
            uploader.on("rejected", on_rejected)
            verify_all_btn = ui.button("Verify all", on_click=lambda: start_all()).props("no-caps unelevated") \
                .classes("vr-btn vr-btn-primary")

        with ui.row().classes("items-center").style("gap:14px;flex-wrap:wrap") as batch_row:
            batch_label = ui.label().style("font-weight:700")
            batch_bar = ui.linear_progress(value=0, show_value=False, size="10px").props("color=dark").style("flex:1 1 200px")
            ui.button("Cancel", on_click=lambda: cancel_all()).props("no-caps outline").classes("vr-btn vr-btn-small")
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
                cells.append(f'<div class="vr-card" style="padding:14px 16px;display:flex;flex-direction:column;gap:6px">'
                             f'<span style="display:inline-flex;align-items:center;gap:8px;font-size:14px;font-weight:700;color:{v["text"]}">'
                             f'<span aria-hidden="true" style="display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;'
                             f'border-radius:11px;border:2px solid {v["base"]};font-size:12px">{v["icon"]}</span>{v["label"]}</span>'
                             f'<span style="font-family:var(--vr-display);font-size:30px;font-weight:600">{counts[k]}</span></div>')
            cells.append('<div class="vr-card" style="padding:14px 16px;display:flex;flex-direction:column;gap:6px">'
                         '<span style="font-size:14px;font-weight:700">Not verified</span>'
                         f'<span style="font-family:var(--vr-display);font-size:30px;font-weight:600">{not_verified}</span></div>')
            html('<section aria-label="Verdict summary" class="vr-grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">'
                 + "".join(cells) + "</section>")
            with header:
                header.clear()
                verified = len([d for d in docs if key(d.status) == "VERIFIED"])
                html(page_header("Invoices", f"{len(docs)} documents in the inbox, {verified} verified"))

        await summary()

        with ui.element("section").classes("vr-card").props('aria-label="Documents"'):
            with ui.element("div").style("display:flex;flex-wrap:wrap;align-items:flex-end;gap:14px;padding:16px 18px;"
                                         "border-bottom:1px solid var(--vr-rule)"):
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
                    .classes("vr-btn vr-btn-small")

            grid = ui.aggrid({
                "columnDefs": COLUMNS,
                "rowData": [],
                "defaultColDef": {"sortable": True, "resizable": True, "suppressMovable": True},
                "rowHeight": 64,
                "headerHeight": 44,
                "suppressCellFocus": True,
                "enableCellTextSelection": True,
                ":getRowId": "params => params.data.doc_id",
            }, html_columns=HTML_COLUMNS, auto_size_columns=False).classes("vr-ag")

            with ui.element("div").classes("vr-empty").style("margin:16px;flex-wrap:wrap") as empty:
                ui.label('No documents match these filters. Clear the search or choose "All verdicts" and "All statuses".')
                ui.button("Clear filters", on_click=lambda: clear_filters()).props("no-caps flat") \
                    .classes("vr-btn vr-btn-small")

        async def ask_delete(doc_id: str, filename: str) -> None:
            if _batch["running"]:
                ui.notify("Verification of the inbox is running. Cancel it or wait for it to finish, then delete.",
                          type="warning")
                return

            async def confirm() -> None:
                dialog.close()
                try:
                    await svc.delete_document(doc_id)
                except Exception as exc:  # noqa: BLE001 - reported to the user, never a crash
                    ui.notify(f"{doc_id} was not deleted: {exc}", type="negative")
                    return
                ui.notify(f"Deleted {filename}. You can upload it again.", type="positive")
                await refresh_all()

            with ui.dialog() as dialog, ui.card().classes("vr-card").style("max-width:480px;gap:12px"):
                ui.label(f"Delete {doc_id}?").style("font-size:18px;font-weight:700")
                ui.label(f"{filename} is removed from the inbox, together with its verdict and recording, so you can "
                         "upload it and verify it again. The audit log and any review case stay as a record.")                     .classes("vr-small").style("font-size:14px")
                with ui.row().style("gap:10px;justify-content:flex-end;width:100%"):
                    ui.button("Cancel", on_click=dialog.close).props("no-caps flat").classes("vr-btn")
                    ui.button("Delete", on_click=confirm).props("no-caps unelevated color=negative").classes("vr-btn")
            dialog.open()

        async def on_cell_clicked(e) -> None:
            args = e.args or {}
            data = args.get("data") or {}
            if args.get("colId") == "delete_html" and data.get("doc_id"):
                await ask_delete(data["doc_id"], data.get("filename") or data["doc_id"])

        grid.on("cellClicked", on_cell_clicked, args=["colId", "data"])

        async def load_grid() -> None:
            docs = await svc.list_documents()
            q = (search.value or "").strip().lower()
            rows = [_row(d) for d in docs if _matches(d, q, verdict_sel.value, status_sel.value)]
            grid.options["rowData"] = rows
            grid.update()
            grid.set_visibility(bool(rows))
            empty.set_visibility(not rows)

        refresh_lock = asyncio.Lock()

        async def refresh_all() -> None:
            # One refresh at a time: two overlapping refreshes of the async `summary` both clear it and
            # then both draw, which left several copies of the verdict tiles on the page.
            async with refresh_lock:
                await load_grid()
                await summary.refresh()

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
            batch.start(todo)
            ui.notify(f"Verifying {len(todo)} documents one after another.")
            await tick()

        def cancel_all() -> None:
            if batch.cancel():
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
        ui.timer(0.5, report_uploads)
