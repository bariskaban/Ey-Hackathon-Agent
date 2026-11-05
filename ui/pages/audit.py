"""What the AI saw: every tool call, per role, including refused ones. Backs the governance pitch."""
from __future__ import annotations

import json

from nicegui import ui

from ui.components.widgets import page_header, role_tag
from ui.services import open_case_count, svc
from ui.theme import ROLE_LABELS, esc, fmt_time, html, key, shell

SERVERS = ["documents", "transactions", "tax_rules", "vendor_checks", "anomaly_stats", "review_cases"]
ROLE_JOBS = {
    "extractor": ("Reads the invoice", "Turns the document into structured fields. Cannot see company records."),
    "verifier": ("Checks it", "Runs the fixed checks and up to 8 extra lookups. Read-only everywhere."),
    "reporter": ("Writes it up", "The only role that can write, and only to append-only review cases."),
}


@ui.page("/audit")
async def audit_page(doc: str = "", role: str = "", server: str = "", tool: str = "", refused: int = 0) -> None:
    status = await svc.system_status()
    with shell("audit", status, await open_case_count()):
        html(page_header("What the AI saw") +
             '<p style="margin:6px 0 0;font-size:16px;line-height:1.55;color:var(--vr-body2);max-width:76ch">'
             'The agents never touch the database. Each one can only call the narrow, read-only tools on its own '
             'list, sensitive fields come back masked, and every call is written down here, including the ones '
             'that were refused.</p>')

        docs = [d.doc_id for d in await svc.list_documents()]
        doc = doc if doc in docs else ""
        entries = await svc.get_audit_log(doc_id=doc or None, role=role or None, server=server or None)
        if tool:
            entries = [e for e in entries if e.tool == tool]
        if refused:
            entries = [e for e in entries if e.refused]

        # ---------- per-role summary ----------
        scope = await svc.get_audit_log(doc_id=doc or None)
        cards = []
        for r in getattr(status, "roles", []) or []:
            name = key(r.role)
            calls = [e for e in scope if key(e.role) == name]
            used = len({e.tool for e in calls if not e.refused})
            n_refused = len([e for e in calls if e.refused])
            budget = r.budget * (1 if doc else max(1, len({e.doc_id for e in scope})))
            pct = min(100, round(len(calls) / budget * 100)) if budget else 0
            tag, job = ROLE_JOBS.get(name, ("", ""))
            cards.append(
                f'<div class="vr-card vr-pad" style="display:flex;flex-direction:column;gap:12px">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;gap:10px">'
                f'<h2 class="vr-h3">{ROLE_LABELS.get(name, name)}</h2>{role_tag(name).replace(name.capitalize(), tag)}</div>'
                f'<p style="margin:0;font-size:14px;color:var(--vr-body2)">{job}</p>'
                f'<dl style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px">'
                f'<div><dt class="vr-small" style="font-size:12px">Tools allowed</dt><dd style="font-family:var(--vr-display);font-size:24px;font-weight:600">{len(r.tools)}</dd></div>'
                f'<div><dt class="vr-small" style="font-size:12px">Tools used</dt><dd style="font-family:var(--vr-display);font-size:24px;font-weight:600">{used}</dd></div>'
                f'<div><dt class="vr-small" style="font-size:12px">Refused</dt><dd style="font-family:var(--vr-display);font-size:24px;font-weight:600;'
                f'color:{"#B91C1C" if n_refused else "inherit"}">{n_refused}</dd></div></dl>'
                f'<div><div style="display:flex;justify-content:space-between;font-size:13px"><span>Query budget used</span>'
                f'<span class="vr-num">{len(calls)} of {budget}</span></div>'
                f'<div class="vr-bar" style="margin-top:5px;height:8px" aria-hidden="true"><div style="width:{pct}%;height:8px"></div></div></div>'
                f'<p class="vr-num" style="margin:0;font-size:12px;line-height:1.6;color:var(--vr-graphite)">{esc(", ".join(r.tools))}</p></div>')
        html('<section aria-label="Agent roles" class="vr-grid" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px">'
             + "".join(cards) + "</section>")

        # ---------- filters ----------
        def go(**changes: str | int) -> None:
            params = {"doc": doc, "role": role, "server": server, "tool": tool, "refused": refused} | changes
            query = "&".join(f"{k}={v}" for k, v in params.items() if v not in ("", 0, None))
            ui.navigate.to(f"/audit?{query}" if query else "/audit")

        with ui.element("section").classes("vr-card").props('aria-label="Tool call timeline"'):
            with ui.element("div").classes("vr-card-head").style("align-items:flex-end"):
                html(f'<h2 class="vr-h2">Tool calls {"for <span class=vr-num style=font-size:20px>" + esc(doc) + "</span>" if doc else "for every invoice"}</h2>')
                with ui.element("div").style("display:flex;flex-wrap:wrap;gap:12px;align-items:center"):
                    ui.select({"": "All invoices", **{d: d for d in docs}}, value=doc, label="Invoice",
                              on_change=lambda e: go(doc=e.value or "")).props("outlined dense").style("min-width:150px")
                    ui.select({"": "All roles", **ROLE_LABELS}, value=role, label="Role",
                              on_change=lambda e: go(role=e.value or "")).props("outlined dense").style("min-width:140px")
                    ui.select({"": "All servers", **{s: s for s in SERVERS}}, value=server, label="Server",
                              on_change=lambda e: go(server=e.value or "")).props("outlined dense").style("min-width:170px")
                    tools = sorted({e.tool for e in await svc.get_audit_log() if e.tool} | ({tool} if tool else set()))
                    ui.select({"": "All tools", **{t: t for t in tools}}, value=tool, label="Tool",
                              on_change=lambda e: go(tool=e.value or "")).props("outlined dense").style("min-width:190px")
                    ui.checkbox("Refused calls only", value=bool(refused),
                                on_change=lambda e: go(refused=1 if e.value else 0))

            if not entries:
                html('<div class="vr-empty" style="margin:16px">No tool calls match these filters.</div>')
                return

            shown = entries[-200:]
            detail = {"e": shown[0]}

            def row_html() -> str:
                rows = []
                for i, e in enumerate(shown, 1):
                    args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in vars(e.args).items()) \
                        if hasattr(e.args, "__dict__") else esc(e.args)
                    res = (f'Refused: {esc(e.refusal_reason or "not on this role’s list")}' if e.refused
                           else esc(e.result_preview or f"{e.rows_returned} rows"))
                    rows.append(f'<tr class="{"vr-bad" if e.refused else ""}"><td class="vr-num">{i}</td>'
                                f'<td class="vr-num">{fmt_time(e.ts)}</td>'
                                + ("" if doc else f'<td class="vr-num">{esc(e.doc_id)}</td>')
                                + f'<td>{role_tag(e.role)}</td><td class="vr-num vr-nowrap" style="font-weight:500">{esc(e.server)}.{esc(e.tool)}</td>'
                                f'<td class="vr-num" style="color:var(--vr-body2)">{esc(args)}</td>'
                                f'<td style="{"color:#B91C1C;font-weight:700" if e.refused else ""}">{res}</td>'
                                f'<td class="vr-num vr-right">{e.latency_ms} ms</td></tr>')
                return ('<div class="vr-table-wrap"><table class="vr-table"><thead><tr><th>Step</th><th>Time</th>'
                        + ("" if doc else "<th>Invoice</th>")
                        + '<th>Role</th><th>Tool</th><th>Arguments</th><th>What came back</th><th class="vr-right">Latency</th>'
                        '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")

            html(row_html())
            if len(entries) > len(shown):
                html(f'<p class="vr-small" style="margin:10px 18px">Showing the latest {len(shown)} of {len(entries)} calls. '
                     'Filter by invoice to see one run in full.</p>')

            @ui.refreshable
            def result_view() -> None:
                e = detail["e"]
                payload = {"tool": f"{e.server}.{e.tool}", "rows_returned": e.rows_returned,
                           "result": e.result_preview, "refused": bool(e.refused)}
                html(f'<h3 style="margin:0;font-size:15px">Result of step {shown.index(e) + 1}, exactly as the agent received it</h3>'
                     f'<pre class="vr-pre">{esc(json.dumps(payload, indent=2, ensure_ascii=False))}</pre>')

            with ui.element("div").style("padding:16px 18px;display:flex;flex-direction:column;gap:8px;"
                                         "border-top:1px solid var(--vr-rule)"):
                ui.select({i: f"Step {i + 1}: {e.server}.{e.tool}" for i, e in enumerate(shown)}, value=0,
                          label="Inspect a step",
                          on_change=lambda ev: (detail.update(e=shown[ev.value]), result_view.refresh())) \
                    .props("outlined dense").style("max-width:420px")
                result_view()
