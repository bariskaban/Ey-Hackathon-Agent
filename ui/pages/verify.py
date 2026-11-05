"""Live verification: the main demo screen. Everything on it is driven by svc.verify() events."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from nicegui import ui

from ui.components.widgets import check_icon, check_word, page_header, role_tag, severity_tag, stamp, verdict_badge
from ui.services import open_case_count, svc
from ui.theme import CHECK_LABELS, ROLE_LABELS, SERVER_GROUPS, esc, eur, fmt_date, html, key, shell

STAGES = [("convert", "Read document"), ("extract", "Extract fields"), ("checks", "Run checks"),
          ("agent", "Agent review"), ("verdict", "Verdict"), ("report", "Report")]


@ui.page("/verify")
async def verify_index() -> None:
    """No document chosen: list the ones that can be verified."""
    status = await svc.system_status()
    with shell("verify", status, await open_case_count()):
        html(page_header("Live verification", "Choose a document to verify. Verified ones open their verdict."))
        docs = await svc.list_documents()
        rows = []
        for d in docs:
            done = key(d.status) == "VERIFIED"
            href = f"/verdict/{esc(d.doc_id)}" if done else f"/verify/{esc(d.doc_id)}?start=1"
            rows.append(f'<tr><td class="tm-num">{esc(d.doc_id)}</td><td>{esc(d.vendor_name or d.filename)}</td>'
                        f'<td class="tm-num tm-right">{eur(d.amount_total)}</td><td>{verdict_badge(d.verdict)}</td>'
                        f'<td class="tm-right"><a class="tm-btn tm-btn-small" href="{href}">'
                        f'{"Open verdict" if done else "Verify"}</a></td></tr>')
        html('<section class="tm-card"><div class="tm-table-wrap"><table class="tm-table"><thead><tr>'
             '<th>Document</th><th>Vendor</th><th class="tm-right">Amount</th><th>Verdict</th>'
             '<th><span class="tm-sr">Action</span></th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div></section>")


@ui.page("/verify/{doc_id}")
async def verify_page(doc_id: str, start: int = 0, replay: int = 0) -> None:
    status = await svc.system_status()
    docs = {d.doc_id: d for d in await svc.list_documents()}
    doc = docs.get(doc_id)

    with shell("verify", status, await open_case_count()):
        if doc is None:
            html(page_header("Document not found", f"There is no {esc(doc_id)} in the inbox.",
                             '<a href="/">Inbox</a>'))
            return

        state: dict[str, Any] = {"running": False, "task": None, "t0": 0.0, "stages": {}, "checks": {},
                                 "notes": [], "feed": [], "verdict": None, "error": None, "done_checks": 0,
                                 "failed_checks": 0}

        # ---------- header ----------
        with ui.element("div").classes("tm-header"):
            title = esc(doc.vendor_name or doc.filename)
            sub = (f"Invoice {esc(doc.invoice_number)}, <span class='tm-num'>{eur(doc.amount_total)}</span>, "
                   f"dated {fmt_date(doc.invoice_date)}") if doc.invoice_number else esc(doc.filename)
            html(page_header(f"Verifying {title}", sub,
                             f'<a href="/">Inbox</a> / <span class="tm-num">{esc(doc_id)}</span>'))
            with ui.element("div").style("display:flex;flex-wrap:wrap;align-items:center;gap:10px"):
                mode_chip = html("")
                timer_chip = html("")
                start_btn = ui.button("Start verification", on_click=lambda: begin()) \
                    .props("no-caps unelevated").classes("tm-btn tm-btn-primary")
                cancel_btn = ui.button("Cancel run", on_click=lambda: cancel()).props("no-caps flat").classes("tm-btn")

        mode_chip.set_content(
            '<span class="tm-pill"><span class="tm-dot" style="background:#1D4ED8"></span>Replay recording</span>'
            if replay else '<span class="tm-pill"><span class="tm-dot" style="background:#B91C1C"></span>Live run</span>')

        stepper = html("")
        error_box = ui.element("div")
        result_box = ui.element("div")

        with ui.element("div").classes("tm-row"):
            with ui.element("section").classes("tm-card").style("flex:3 1 520px;min-width:0") \
                    .props('aria-label="Checks"'):
                checks_head = html("")
                checks_body = html("")
            with ui.element("div").style("flex:2 1 380px;min-width:0;display:flex;flex-direction:column;gap:22px"):
                notes_box = html("")
                feed_box = html("")

        # ---------- rendering ----------
        def render() -> None:
            parts = []
            for i, (k, label) in enumerate(STAGES, 1):
                st = state["stages"].get(k)
                cls, word = {"started": ("tm-step-now", "In progress"), "done": ("tm-step-done", "Done"),
                             "failed": ("tm-step-failed", "Failed")}.get(st, ("", "Next"))
                detail = _stage_detail(k, state)
                parts.append(f'<li class="tm-step {cls}"><span class="tm-small"><span class="tm-num">{i}</span> {word}</span>'
                             f'<span style="font-size:16px;font-weight:700">{label}</span>'
                             f'<span class="tm-small">{esc(detail)}</span></li>')
            stepper.set_content('<ol class="tm-steps" aria-label="Verification stages">' + "".join(parts) + "</ol>")

            total = len(CHECK_LABELS)
            checks_head.set_content(
                '<div class="tm-card-head"><h2 class="tm-h2">Checks</h2>'
                f'<span class="tm-small" style="font-size:14px">{state["done_checks"]} of {total} done'
                f'{", " + str(state["failed_checks"]) + " failed" if state["failed_checks"] else ""}</span></div>')
            groups = []
            for server, gtitle in SERVER_GROUPS:
                items = [c for c in state["checks"].values() if c["server"] == server]
                pending = [n for n, s in _CHECK_SERVER.items() if s == server and n not in state["checks"]]
                if not items and not pending:
                    continue
                lis = []
                for c in items:
                    sev = severity_tag(c.get("severity")) if c["status"] == "fail" else ""
                    lis.append(f'<li class="tm-check">{check_icon(c["status"])}<div style="flex:1;min-width:0">'
                               f'<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">'
                               f'<span style="font-size:15px;font-weight:700">{esc(CHECK_LABELS.get(c["name"], c["name"]))}</span>'
                               f'{check_word(c["status"])}{sev}</div>'
                               f'<p style="margin:3px 0 0;font-size:14px;color:var(--tm-body2)">{esc(c["detail"])}</p></div></li>')
                running = state["running"] and state["stages"].get("checks") == "started"
                for i, n in enumerate(pending):
                    st = "running" if running and i == 0 and server == _next_server(state) else "waiting"
                    lis.append(f'<li class="tm-check">{check_icon(st)}<div style="flex:1">'
                               f'<span style="font-size:15px;font-weight:700">{esc(CHECK_LABELS[n])}</span> {check_word(st)}</div></li>')
                groups.append(f'<div style="padding:8px 20px 12px"><h3 style="margin:10px 0 4px;font-size:14px;'
                              f'font-weight:700;color:var(--tm-body2)">{gtitle} <span style="font-weight:400" class="tm-small">'
                              f'server: <span class="tm-num">{server}</span></span></h3>'
                              f'<ul style="list-style:none;margin:0;padding:0">{"".join(lis)}</ul></div>')
            checks_body.set_content("".join(groups))

            notes = "".join(f'<div class="tm-note"><span style="font-size:13px;font-weight:700;color:var(--tm-body2)">'
                            f'{ROLE_LABELS.get(n["role"], n["role"])}, {n["t"]}</span>'
                            f'<p style="margin:0;font-size:15px;line-height:1.5">{esc(n["text"])}</p></div>'
                            for n in state["notes"])
            if not any(n["role"] == "verifier" for n in state["notes"]):
                notes += ('<div class="tm-empty">The verifier writes its notes when the checks finish.</div>')
            notes_box.set_content('<section class="tm-card tm-pad" aria-label="Agent notes" style="display:flex;'
                                  'flex-direction:column;gap:12px"><h2 class="tm-h2">Agent notes</h2>' + notes + "</section>")

            feed = "".join(
                f'<li class="tm-feed-item" style="{"background:#FBEDEC" if f["refused"] else ""}">'
                f'<span class="tm-num" style="flex:0 0 42px;color:var(--tm-muted);padding-top:2px">{f["t"]}</span>'
                f'<div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:3px">'
                f'<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">{role_tag(f["role"])}'
                f'<span class="tm-num" style="font-weight:500;overflow-wrap:anywhere">{esc(f["tool"])}</span></div>'
                f'<span style="font-size:13px;color:{"#B91C1C" if f["refused"] else "var(--tm-graphite)"};'
                f'font-weight:{700 if f["refused"] else 400}">{esc(f["meta"])}</span></div></li>'
                for f in reversed(state["feed"][-14:]))
            feed_box.set_content(
                '<section class="tm-card" aria-label="Tool calls"><div class="tm-card-head"><h2 class="tm-h2">Tool calls</h2>'
                f'<a href="/audit?doc={esc(doc_id)}" style="font-size:14px;font-weight:700">Open full log</a></div>'
                + (f'<ul style="list-style:none;margin:0;padding:4px 0">{feed}</ul>' if feed
                   else '<div class="tm-empty" style="margin:16px">No tool calls yet.</div>') + "</section>")

            start_btn.set_visibility(not state["running"])
            start_btn.text = "Run again" if state["verdict"] or state["error"] else "Start verification"
            cancel_btn.set_visibility(state["running"])

        def clock() -> None:
            if state["running"]:
                secs = int(time.monotonic() - state["t0"])
                state["elapsed"] = secs
            secs = state.get("elapsed", 0)
            timer_chip.set_content(f'<span class="tm-pill">Elapsed <span class="tm-num" style="font-size:15px;'
                                   f'font-weight:500">{secs // 60:02d}:{secs % 60:02d}</span></span>')

        def elapsed_label() -> str:
            s = int(time.monotonic() - state["t0"])
            return f"{s // 60:02d}:{s % 60:02d}"

        # ---------- event handling ----------
        def handle(ev: Any) -> None:
            t = key(ev.type)
            if t == "stage":
                state["stages"][key(ev.stage)] = key(ev.status)
            elif t == "markdown_ready":
                state["read"] = f"{'OCR' if key(ev.method) == 'ocr' else 'Text PDF'}, {ev.char_count:,} characters"
            elif t == "invoice_extracted":
                low = len(ev.low_confidence_fields or [])
                state["extract"] = f"Fields read, {low} uncertain" if low else "Fields read, none uncertain"
            elif t == "check_result":
                state["checks"][ev.name] = {"name": ev.name, "server": ev.server, "status": key(ev.status),
                                            "detail": ev.detail, "severity": key(getattr(ev, "severity", None))}
                state["done_checks"] = len(state["checks"])
                state["failed_checks"] = len([c for c in state["checks"].values() if c["status"] == "fail"])
            elif t == "tool_call":
                if ev.refused:
                    meta = f"Refused: {getattr(ev, 'refusal_reason', None) or 'not on this role’s allowlist'}"
                else:
                    meta = f"{ev.rows_returned} row{'s' if ev.rows_returned != 1 else ''}, {ev.latency_ms} ms"
                state["feed"].append({"t": elapsed_label(), "role": key(ev.role), "tool": f"{ev.server}.{ev.tool}",
                                      "meta": meta, "refused": bool(ev.refused)})
            elif t == "llm_note":
                state["notes"].append({"role": key(ev.role), "text": ev.text, "t": elapsed_label()})
            elif t == "verdict":
                state["verdict"] = ev.verdict
                show_result()
            elif t == "error":
                state["error"] = f"{ev.message} (stage: {key(ev.stage)})"
                state["stages"][key(ev.stage)] = "failed"
                show_error()

        def show_result() -> None:
            v = state["verdict"]
            result_box.clear()
            with result_box:
                html(f'<section class="tm-card" style="display:flex;flex-wrap:wrap;gap:28px;align-items:center;padding:22px 26px">'
                     f'{stamp(v.verdict, doc_id, getattr(v, "created_at", None))}'
                     f'<div style="flex:1 1 380px;min-width:0"><h2 class="tm-h2">Verdict ready</h2>'
                     f'<p style="margin:6px 0 0;font-size:16px;line-height:1.55;max-width:70ch">{esc(getattr(v, "auditor_note", ""))}</p></div>'
                     f'<a class="tm-btn tm-btn-primary" href="/verdict/{esc(doc_id)}">Open verdict</a></section>')

        def show_error() -> None:
            error_box.clear()
            with error_box:
                html('<div class="tm-card" style="border-color:#DC2626;padding:16px 20px;display:flex;flex-direction:column;gap:6px">'
                     '<strong style="color:#B91C1C">Verification stopped</strong>'
                     f'<span>{esc(state["error"])}</span><span class="tm-small">Check that Ollama and the servers are '
                     'running, then use "Run again".</span></div>')

        async def run() -> None:
            state.update(running=True, t0=time.monotonic(), stages={}, checks={}, notes=[], feed=[], verdict=None,
                         error=None, done_checks=0, failed_checks=0)
            result_box.clear()
            error_box.clear()
            render()
            try:
                async for ev in svc.verify(doc_id):
                    handle(ev)
                    render()
            except asyncio.CancelledError:
                state["error"] = "The run was cancelled."
                show_error()
            except Exception as exc:  # show it, don't crash the page
                state["error"] = f"{type(exc).__name__}: {exc}"
                show_error()
            finally:
                state["running"] = False
                render()

        def begin() -> None:
            if not state["running"]:
                state["task"] = asyncio.create_task(run())

        def cancel() -> None:
            if state["task"] and not state["task"].done():
                state["task"].cancel()

        render()
        clock()
        ui.timer(1.0, clock)
        if start or key(doc.status) == "VERIFYING":
            ui.timer(0.3, begin, once=True)
        elif key(doc.status) == "VERIFIED":
            v = await svc.get_verdict(doc_id)
            if v:
                state["verdict"] = v
                show_result()
                render()


_CHECK_SERVER = {
    "prompt_injection": "host", "vendor_known": "transactions", "not_duplicate": "transactions",
    "not_already_paid": "transactions", "po_match": "transactions", "goods_received": "transactions",
    "vat_number_valid": "tax_rules", "vat_treatment": "tax_rules", "vat_rate": "tax_rules",
    "mandatory_fields": "tax_rules", "vat_math": "tax_rules", "iban_valid": "vendor_checks",
    "bank_details_match": "vendor_checks", "sanctions": "vendor_checks", "employee_overlap": "vendor_checks",
    "amount_typical": "anomaly_stats", "no_split": "anomaly_stats", "date_ok": "anomaly_stats",
}


def _next_server(state: dict) -> str | None:
    for server, _ in SERVER_GROUPS:
        if any(s == server and n not in state["checks"] for n, s in _CHECK_SERVER.items()):
            return server
    return None


def _stage_detail(stage: str, state: dict) -> str:
    st = state["stages"].get(stage)
    if stage == "convert":
        return state.get("read", "")
    if stage == "extract":
        return state.get("extract", "")
    if stage == "checks":
        return f'{state["done_checks"]} of {len(CHECK_LABELS)} done' if st else ""
    if stage == "agent":
        return "Up to 8 extra lookups" if not st else ("Notes written" if st == "done" else "Looking things up")
    if stage == "verdict" and state.get("verdict") is not None:
        from ui.theme import VERDICTS
        return VERDICTS.get(key(state["verdict"].verdict), {}).get("label", "")
    if stage == "report":
        return "Review case and evidence" if not st else ("Case opened" if getattr(state.get("verdict"), "review_case_id", None) else "Nothing to review")
    return ""
