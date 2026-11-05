"""Review queue: the only place a person sets a final status on an invoice."""
from __future__ import annotations

from nicegui import ui

from ui.components.widgets import error_label, evidence_table, page_header, severity_tag, stamp
from ui.services import open_case_count, svc
from ui.theme import CASE_STATUS_LABELS, ERROR_LABELS, SEVERITIES, esc, eur, fmt_date, fmt_time, html, key, shell


@ui.page("/review")
async def review_index(status: str = "OPEN", error: str = "ALL") -> None:
    cases = await svc.list_cases()
    first = next((c for c in cases if key(c.status) == status), cases[0] if cases else None)
    await _render(first.case_id if first else None, status, error)


@ui.page("/review/{case_id}")
async def review_page(case_id: str, status: str = "ALL", error: str = "ALL") -> None:
    await _render(case_id, status, error)


async def _render(case_id: str | None, status_filter: str, error_filter: str) -> None:
    status = await svc.system_status()
    open_count = await open_case_count()
    with shell("review", status, open_count):
        all_cases = await svc.list_cases()
        waiting = len([c for c in all_cases if key(c.status) == "OPEN"])

        with ui.element("div").classes("vr-header"):
            html(page_header("Review queue", f"{len(all_cases)} cases, {waiting} waiting for a decision. "
                                             "Only people can set a final status here; the agent cannot."))
            with ui.element("div").style("display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end"):
                def go(st: str, err: str) -> None:
                    target = f"/review/{case_id}" if case_id else "/review"
                    ui.navigate.to(f"{target}?status={st}&error={err}")
                ui.select({"ALL": "All statuses", **CASE_STATUS_LABELS}, value=status_filter, label="Status",
                          on_change=lambda e: go(e.value, error_filter)).props("outlined dense").style("min-width:160px")
                types = sorted({key(c.error_type) for c in all_cases if c.error_type})
                ui.select({"ALL": "All error types", **{t: ERROR_LABELS.get(t, t) for t in types}}, value=error_filter,
                          label="Error type", on_change=lambda e: go(status_filter, e.value)) \
                    .props("outlined dense").style("min-width:220px")

        shown = [c for c in all_cases
                 if (status_filter == "ALL" or key(c.status) == status_filter)
                 and (error_filter == "ALL" or key(c.error_type) == error_filter)]

        with ui.element("div").classes("vr-row"):
            items = []
            for c in shown:
                sev = SEVERITIES.get(key(c.severity), SEVERITIES["high"])
                current = c.case_id == case_id
                items.append(
                    f'<li style="border-bottom:1px solid var(--vr-rule);box-shadow:inset 4px 0 0 {sev["base"]};'
                    f'{"background:var(--vr-accent-soft)" if current else ""}">'
                    f'<a href="/review/{esc(c.case_id)}?status={status_filter}&error={error_filter}" '
                    f'{"aria-current=page" if current else ""} style="display:flex;gap:12px;padding:12px 18px 12px 20px;'
                    f'text-decoration:none;color:var(--vr-ink)"><span aria-hidden="true" style="margin-top:2px;color:{sev["text"]}">{sev["icon"]}</span>'
                    f'<span style="flex:1;min-width:0;display:flex;flex-direction:column;gap:2px">'
                    f'<span style="display:flex;justify-content:space-between;gap:10px"><strong>{esc(error_label(c.error_type))}</strong>'
                    f'<span class="vr-small vr-nowrap">{CASE_STATUS_LABELS.get(key(c.status), c.status)}</span></span>'
                    f'<span style="font-size:13px;color:var(--vr-body2)">{esc(c.vendor_name)}, <span class="vr-num" style="font-size:12px">{esc(c.doc_id)}</span></span>'
                    f'<span style="font-size:12px;font-weight:700;color:{sev["text"]}">{sev["label"]} severity</span></span></a></li>')
            html('<section aria-label="Cases" class="vr-card" style="width:100%"><div class="vr-card-head"><h2 class="vr-h2">Cases</h2>'
                 '<span class="vr-small">Highest severity first</span></div>'
                 + (f'<ul style="list-style:none;margin:0;padding:0">{"".join(items)}</ul>' if items
                    else '<div class="vr-empty" style="margin:16px">No cases match these filters.</div>')
                 + "</section>").style("flex:2 1 380px;min-width:0;display:flex")

            with ui.element("section").style("flex:3 1 520px;min-width:0;display:flex;flex-direction:column;gap:22px") \
                    .props('aria-label="Case detail"'):
                if case_id is None:
                    html('<div class="vr-empty">No cases yet. Cases appear when a verdict is not "Matched".</div>')
                    return
                await _case_detail(case_id)


async def _case_detail(case_id: str) -> None:
    c = await svc.get_case(case_id)
    closed = key(c.status) != "OPEN" and key(c.status) != "ESCALATED"

    html(f'<div class="vr-card" style="padding:22px 24px;display:flex;flex-direction:column;gap:16px">'
         f'<div style="display:flex;flex-wrap:wrap;justify-content:space-between;gap:16px;align-items:flex-start"><div>'
         f'<p class="vr-small" style="margin:0;font-size:14px">Case <span class="vr-num">{esc(c.case_id)}</span>, '
         f'opened {fmt_date(c.created_at)} at {fmt_time(c.created_at)[:5]}</p>'
         f'<h2 style="margin:4px 0 0;font-family:var(--vr-display);font-size:28px;font-weight:600">'
         f'{esc(c.vendor_name)}: {esc(error_label(c.error_type).lower())}</h2></div>{stamp(c.verdict, small=True)}</div>'
         f'<p style="margin:0;font-size:16px;line-height:1.6;max-width:70ch">{esc(c.summary)}</p>'
         f'<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">'
         f'{severity_tag(c.severity, (key(c.severity) or "").capitalize() + ": " + error_label(c.error_type).lower())}'
         f'<span class="vr-tag" style="border-color:var(--vr-border);font-weight:400">Amount <span class="vr-num">{eur(c.amount, c.currency)}</span></span>'
         f'<span class="vr-tag" style="border-color:var(--vr-border);font-weight:400">{c.checks_passed} of {c.checks_total} checks passed</span>'
         f'<a href="/verdict/{esc(c.doc_id)}" style="font-size:13px;font-weight:700">Open full verdict</a></div></div>')

    verdict = await svc.get_verdict(c.doc_id)
    html('<section aria-label="Evidence" class="vr-card"><div class="vr-card-head"><h2 class="vr-h3">Evidence</h2>'
         '<span class="vr-small">Exactly as the agent saw it, sensitive fields masked</span></div>'
         + (evidence_table(getattr(verdict, "evidence", None)) if verdict else
            '<div class="vr-empty" style="margin:16px">The verdict for this invoice is not available.</div>')
         + "</section>")

    # ---------- decision ----------
    with ui.element("div").classes("vr-card").style("padding:22px 24px;display:flex;flex-direction:column;gap:14px"):
        html('<h3 class="vr-h3">Your decision</h3>')
        if closed and c.decision:
            d = c.decision
            html(f'<p style="margin:0;font-size:15px"><strong>{CASE_STATUS_LABELS.get(key(d.status), d.status)}</strong> by '
                 f'{esc(d.reviewer)} on {fmt_date(d.ts)}: {esc(d.reason)}</p>')
            return_after = True
        else:
            return_after = False
        if not return_after:
            choice = ui.radio({"APPROVED": "Approve: the invoice can be paid", "REJECTED": "Reject: do not pay this invoice",
                               "ESCALATED": "Escalate: send to a senior reviewer"}, value=None).props("inline")
            reason = ui.textarea("Reason (required)", placeholder="What did you check, and why this decision?") \
                .props("outlined autogrow").style("width:100%")
            with ui.element("div").style("display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end"):
                reviewer = ui.input("Reviewer", value="").props("outlined dense").style("flex:1 1 200px")

                async def record() -> None:
                    if not choice.value:
                        ui.notify("Choose approve, reject or escalate.", type="warning")
                        return
                    if not (reason.value or "").strip():
                        ui.notify("Add a reason. Every decision needs one for the audit trail.", type="warning")
                        return
                    if not (reviewer.value or "").strip():
                        ui.notify("Add your name as the reviewer.", type="warning")
                        return
                    try:
                        await svc.set_case_status(case_id, choice.value, reviewer.value.strip(), reason.value)
                    except Exception as exc:
                        ui.notify(f"The decision was not saved: {exc}. Check the form and try again.", type="negative")
                        return
                    ui.notify("Decision recorded.", type="positive")
                    ui.navigate.to(f"/review/{case_id}")

                ui.button("Record decision", on_click=record).props("no-caps unelevated").classes("vr-btn vr-btn-primary")

    # ---------- notes + evidence pack ----------
    with ui.element("div").classes("vr-row"):
        with ui.element("div").classes("vr-card vr-pad").style("flex:3 1 340px;min-width:0;display:flex;flex-direction:column;gap:12px"):
            html('<h3 class="vr-h3">Notes</h3>')
            notes = "".join(f'<li style="display:flex;gap:12px;padding:10px 0;border-bottom:1px solid var(--vr-rule-faint)">'
                            f'<span class="vr-num" style="flex:0 0 44px;color:var(--vr-muted)">{fmt_time(n.ts)[:5]}</span>'
                            f'<span style="display:flex;flex-direction:column;gap:2px"><strong style="font-size:13px">{esc(n.author)}</strong>'
                            f'<span style="font-size:14px;line-height:1.5">{esc(n.text)}</span></span></li>' for n in c.notes)
            html(f'<ol style="list-style:none;margin:0;padding:0">{notes}</ol>')
            note = ui.input("Add a note", placeholder="What did you check or decide?").props("outlined dense")

            async def add() -> None:
                if not (note.value or "").strip():
                    ui.notify("Write the note first.", type="warning")
                    return
                if not hasattr(svc, "add_case_note"):
                    ui.notify("Adding notes is not in the real service yet.", type="warning")
                    return
                await svc.add_case_note(case_id, note.value, "Reviewer")
                ui.navigate.to(f"/review/{case_id}")

            ui.button("Add note", on_click=add).props("no-caps flat").classes("vr-btn").style("align-self:flex-start")

        with ui.element("div").classes("vr-card vr-pad").style("flex:2 1 240px;min-width:0;display:flex;flex-direction:column;gap:12px"):
            html('<h3 class="vr-h3">Evidence pack</h3><p style="margin:0;font-size:14px;line-height:1.5;color:var(--vr-body2)">'
                 'The invoice, extracted fields, every check, every tool call and the decision, as one working paper.</p>')

            async def export(fmt: str) -> None:
                try:
                    path = await svc.export_evidence_pack(case_id, fmt)
                except Exception as exc:
                    ui.notify(f"The evidence pack was not created: {exc}. Try again.", type="negative")
                    return
                ui.download.file(path, path.name)

            ui.button("Export as PDF", on_click=lambda: export("pdf")).props("no-caps unelevated").classes("vr-btn vr-btn-primary")
            ui.button("Export as Excel", on_click=lambda: export("xlsx")).props("no-caps flat").classes("vr-btn")
