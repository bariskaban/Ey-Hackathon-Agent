"""Verdict: the result for one invoice, with evidence an auditor can follow."""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

from nicegui import run, ui

from ui.components.widgets import (check_icon, check_word, error_label, evidence_table, page_header, severity_tag,
                                   stamp, verdict_badge, yes_no)
from ui.services import open_case_count, svc
from ui.theme import CHECK_LABELS, SERVER_GROUPS, esc, eur, fmt_date, html, key, shell

FIELD_LABELS = [
    ("supplier_name", "Supplier"), ("supplier_country", "Supplier country"),
    ("supplier_vat_number", "Supplier VAT number"), ("supplier_kvk_number", "KvK number"),
    ("buyer_vat_number", "Buyer VAT number"), ("invoice_number", "Invoice number"),
    ("invoice_date", "Invoice date"), ("supply_date", "Supply date"), ("po_number", "PO reference"),
    ("total_excl_vat", "Net amount"), ("total_vat", "VAT"), ("total_incl_vat", "Total incl. VAT"),
    ("iban_last4", "IBAN"),
]
DASH, DOTS = "\u2013", "\u2022\u2022\u2022\u2022"
MONEY_FIELDS = {"total_excl_vat", "total_vat", "total_incl_vat"}
DATE_FIELDS = {"invoice_date", "supply_date"}


@ui.page("/verdict")
async def verdict_index() -> None:
    status = await svc.system_status()
    with shell("verdict", status, await open_case_count()):
        html(page_header("Verdicts", "Every verified invoice, newest first."))
        docs = [d for d in await svc.list_documents() if key(d.status) == "VERIFIED"]
        if not docs:
            html('<div class="tm-empty">No verdicts yet. Verify an invoice from the <a href="/">inbox</a>.</div>')
            return
        rows = "".join(
            f'<tr><td class="tm-num">{esc(d.doc_id)}</td><td>{esc(d.vendor_name)}</td>'
            f'<td class="tm-num tm-right">{eur(d.amount_total)}</td><td>{verdict_badge(d.verdict)}</td>'
            f'<td>{esc(error_label(d.main_finding) if d.main_finding and d.main_finding != "All checks pass" else "All checks pass")}</td>'
            f'<td class="tm-right"><a class="tm-btn tm-btn-small" href="/verdict/{esc(d.doc_id)}">Open</a></td></tr>'
            for d in reversed(docs))
        html('<section class="tm-card"><div class="tm-table-wrap"><table class="tm-table"><thead><tr><th>Document</th>'
             '<th>Vendor</th><th class="tm-right">Amount</th><th>Verdict</th><th>Main finding</th>'
             '<th><span class="tm-sr">Action</span></th></tr></thead><tbody>' + rows + "</tbody></table></div></section>")


@ui.page("/verdict/{doc_id}")
async def verdict_page(doc_id: str) -> None:
    status = await svc.system_status()
    with shell("verdict", status, await open_case_count()):
        v = await svc.get_verdict(doc_id)
        if v is None:
            html(page_header("No verdict yet", f"{esc(doc_id)} has not been verified.", '<a href="/">Inbox</a>'))
            html(f'<a class="tm-btn tm-btn-primary" href="/verify/{esc(doc_id)}?start=1" style="align-self:flex-start">'
                 'Verify it now</a>')
            return
        inv = v.invoice
        failed = {d.field for d in (v.discrepancies or [])}

        # ---------- header ----------
        actions = (f'<div style="display:flex;flex-wrap:wrap;gap:10px">'
                   f'<a class="tm-btn" href="/audit?doc={esc(doc_id)}">See what the AI saw</a>'
                   + (f'<a class="tm-btn tm-btn-primary" href="/review/{esc(v.review_case_id)}">Open review case</a>'
                      if v.review_case_id else "") + "</div>")
        html(f'<div class="tm-header">'
             + page_header(f"{inv.supplier_name}, invoice {inv.invoice_number or ''}".strip(", "),
                           f"Verified in {v.seconds} seconds with {v.tool_calls} tool calls on "
                           f"<span class='tm-num'>{esc(v.model)}</span>",
                           f'<a href="/">Inbox</a> / <span class="tm-num">{esc(doc_id)}</span>')
             + actions + "</div>")

        # ---------- verdict ----------
        flags = "".join(severity_tag(_severity_of(v, f), f"{error_label(f)}, {_severity_of(v, f)}")
                        for f in (v.risk_flags or [])) or '<span class="tm-small">None</span>'
        conf = float(v.confidence or 0)
        reason = (f'<p style="margin:0;font-weight:700;color:#1D4ED8">Why a person must look: {esc(v.reason)}</p>'
                  if getattr(v, "reason", None) else "")
        html(f'<section aria-label="Verdict" class="tm-card" style="display:flex;flex-wrap:wrap;gap:32px;align-items:center;padding:26px 30px">'
             f'{stamp(v.verdict, doc_id, v.created_at)}'
             f'<div style="flex:1 1 420px;min-width:0;display:flex;flex-direction:column;gap:10px">'
             f'<h2 class="tm-h2">What the agent found</h2>{reason}'
             f'<p style="margin:0;font-size:17px;line-height:1.6;max-width:68ch">{esc(v.auditor_note)}</p>'
             f'<p class="tm-small" style="margin:0">Written by the verifier agent. The verdict itself comes from the fixed checks, not from this text.</p></div>'
             f'<div style="flex:0 1 230px;display:flex;flex-direction:column;gap:14px"><div>'
             f'<div style="display:flex;justify-content:space-between;font-size:14px"><strong>Confidence</strong>'
             f'<span class="tm-num" style="font-size:14px">{conf:.2f}</span></div>'
             f'<div class="tm-bar" style="margin-top:6px" aria-hidden="true"><div style="width:{conf * 100:.0f}%"></div></div>'
             f'<p class="tm-small" style="margin:6px 0 0;font-size:12px">From the check results: '
             f'{len([c for c in v.checks if key(c.status) != "skipped"])} of {len(v.checks)} checks ran with complete data.</p></div>'
             f'<div style="display:flex;flex-direction:column;gap:6px;align-items:flex-start"><strong style="font-size:14px">Risk flags</strong>{flags}</div>'
             f'</div></section>')

        # ---------- invoice preview + extracted fields ----------
        with ui.element("div").classes("tm-row"):
            with ui.element("section").classes("tm-card tm-pad").style("flex:1 1 460px;min-width:0;display:flex;flex-direction:column;gap:12px") \
                    .props('aria-label="Invoice"'):
                path = svc.get_document_file(doc_id)
                has_file = bool(path and path.exists())
                pages = await run.io_bound(_pdf_pages, path) if has_file and path.suffix.lower() == ".pdf" else []
                is_image = has_file and path.suffix.lower() in (".png", ".jpg", ".jpeg")
                original = bool(pages) or is_image
                html('<div style="display:flex;justify-content:space-between;align-items:baseline;gap:10px">'
                     '<h2 class="tm-h2">Invoice</h2><span class="tm-small">'
                     f'{"The original document" if original else "Drawn from the extracted fields"}</span></div>')
                if pages:
                    for n, src in enumerate(pages, 1):
                        html(f'<img src="{src}" alt="Invoice page {n} of {len(pages)}" '
                             'style="width:100%;border:1px solid var(--tm-rule);background:#FFFFFF">')
                elif is_image:
                    ui.image(str(path)).style("width:100%;border:1px solid var(--tm-rule)")
                else:
                    html(_invoice_sheet(inv, v, failed))
                    html('<p class="tm-small" style="margin:0"><mark style="background:#E4F07A;padding:0 4px;outline:2px solid #DC2626">'
                         'Marked</mark> values are where a check failed.</p>')

            low = set(v.low_confidence_fields or [])
            rows = []
            for f, label in FIELD_LABELS:
                raw = getattr(inv, f, None)
                val = eur(raw) if f in MONEY_FIELDS else fmt_date(raw) if f in DATE_FIELDS else (
                    f"\u2022\u2022\u2022\u2022 {raw}" if f == "iban_last4" and raw else raw)
                val_html = (f'<span class="tm-num">{esc(val)}</span>' if f not in ("supplier_name", "supplier_country")
                            else esc(val)) if val not in (None, "") else '<span class="tm-small">Not on the invoice</span>'
                flag = ('<span class="tm-tag" style="margin-left:8px;border-style:dashed;border-color:#1D4ED8;color:#1D4ED8">'
                        'Uncertain, please confirm</span>' if f in low else "")
                rows.append(f'<tr><th scope="row" style="font-weight:500;color:var(--tm-graphite);width:38%">{label}</th>'
                            f'<td>{val_html}{flag}</td></tr>')
            html(f'<section aria-label="Extracted fields" class="tm-card" style="flex:1 1 420px;min-width:0">'
                 f'<div class="tm-card-head"><h2 class="tm-h2">Extracted fields</h2>'
                 f'<span class="tm-small">{len(low)} uncertain</span></div>'
                 f'<div class="tm-table-wrap"><table class="tm-table"><tbody>{"".join(rows)}</tbody></table></div></section>')

        # ---------- discrepancies ----------
        if v.discrepancies:
            rows = "".join(
                f'<tr><td style="font-weight:700">{esc(error_label(d.type))}</td><td>{esc(d.field)}</td>'
                f'<td class="tm-num">{esc(d.expected)}</td><td class="tm-num">{esc(d.found)}</td>'
                f'<td>{severity_tag(d.severity, (key(d.severity) or "").capitalize())}</td>'
                f'<td class="tm-num">{esc(getattr(d, "check", "") or "")}</td></tr>' for d in v.discrepancies)
            html('<section aria-label="Discrepancies" class="tm-card"><div class="tm-card-head"><h2 class="tm-h2">Discrepancies</h2></div>'
                 '<div class="tm-table-wrap"><table class="tm-table"><thead><tr><th>Type</th><th>Field</th><th>Expected</th>'
                 f'<th>Found on invoice</th><th>Severity</th><th>Caught by</th></tr></thead><tbody>{rows}</tbody></table></div></section>')

        # ---------- VAT + bank ----------
        with ui.element("div").classes("tm-row").style("align-items:stretch"):
            html(_vat_panel(v)).style("flex:3 1 480px;min-width:0;display:flex")
            html(_bank_panel(v)).style("flex:2 1 320px;min-width:0;display:flex")

        # ---------- all checks ----------
        groups = []
        for server, title in SERVER_GROUPS:
            items = [c for c in v.checks if c.server == server]
            if not items:
                continue
            lis = "".join(f'<li class="tm-check">{check_icon(key(c.status))}<div style="flex:1;min-width:0">'
                          f'<strong>{esc(CHECK_LABELS.get(c.name, c.name))}</strong> {check_word(key(c.status))}'
                          f'<p style="margin:3px 0 0;font-size:14px;color:var(--tm-body2)">{esc(c.detail)}</p></div></li>'
                          for c in items)
            groups.append(f'<div style="flex:1 1 300px;min-width:0"><h3 style="margin:6px 0 2px;font-size:14px">{title}</h3>'
                          f'<ul style="list-style:none;margin:0;padding:0">{lis}</ul></div>')
        html('<section aria-label="All checks" class="tm-card"><div class="tm-card-head"><h2 class="tm-h2">Every check</h2>'
             f'<span class="tm-small">{len([c for c in v.checks if key(c.status) == "pass"])} of {len(v.checks)} passed</span></div>'
             f'<div style="display:flex;flex-wrap:wrap;gap:8px 28px;padding:8px 20px 16px">{"".join(groups)}</div></section>')

        # ---------- evidence ----------
        html('<section aria-label="Evidence" class="tm-card"><div class="tm-card-head"><h2 class="tm-h2">Evidence the agent used</h2>'
             '<span class="tm-small">Shown exactly as the agent saw it, sensitive fields masked</span></div>'
             + evidence_table(getattr(v, "evidence", None)) + "</section>")


def _pdf_pages(path: Path, max_pages: int = 3) -> list[str]:
    """First pages of a PDF as PNG data URIs. Empty when the renderer is missing or the file is unreadable."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    out: list[str] = []
    try:
        pdf = pdfium.PdfDocument(str(path))
        for i in range(min(len(pdf), max_pages)):
            buf = io.BytesIO()
            pdf[i].render(scale=1.4).to_pil().save(buf, format="PNG")
            out.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())
        pdf.close()
    except Exception:
        return []
    return out


def _severity_of(v: Any, flag: Any) -> str:
    for d in v.discrepancies or []:
        if key(d.type) == key(flag):
            return key(d.severity) or ""
    return "high"


def _invoice_sheet(inv: Any, v: Any, failed: set[str]) -> str:
    """Draw the invoice from its extracted fields (the mock has no original files)."""
    vat_failed = any(key(d.type) in ("REVERSE_CHARGE_ERROR", "WRONG_VAT_RATE", "VAT_ARITHMETIC_ERROR")
                     for d in v.discrepancies or [])
    iban_failed = any(key(d.type) == "CHANGED_BANK_DETAILS" for d in v.discrepancies or [])

    def mark(text: str, on: bool) -> str:
        return f"<mark>{text}</mark>" if on else text

    lines = "".join(f'<tr><td style="padding:4px 0">{esc(li.description)}</td><td class="tm-right">{li.qty}</td>'
                    f'<td class="tm-right">{eur(li.unit_price)}</td><td class="tm-right">{eur(li.amount)}</td></tr>'
                    for li in inv.lines)
    vat_rows = "".join(f'<tr><td colspan="3" class="tm-right">{mark(f"VAT {vl.rate}%", vat_failed)}</td>'
                       f'<td class="tm-right">{mark(eur(vl.vat), vat_failed)}</td></tr>' for vl in inv.vat_lines)
    rc = f'<div style="margin-top:10px;font-weight:700">{esc(inv.reverse_charge_text)}</div>' if inv.reverse_charge_text else ""
    kvk = f"<br>KvK {esc(inv.supplier_kvk_number)}" if inv.supplier_kvk_number else ""
    return (f'<div class="tm-invoice"><div style="display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap">'
            f'<div><div style="font-size:18px;font-weight:700">{esc(inv.supplier_name)}</div>{esc(inv.supplier_address or "")}'
            f'<br>VAT {esc(inv.supplier_vat_number or "")}{kvk}</div>'
            f'<div style="text-align:right"><div style="font-size:22px;font-weight:700">Invoice</div>No. {esc(inv.invoice_number or "")}'
            f'<br>Date {fmt_date(inv.invoice_date)}<br>Supply date {fmt_date(inv.supply_date)}</div></div>'
            f'<div style="margin-top:18px">Bill to: {esc(inv.buyer_name or "")}, {esc(inv.buyer_address or "")}'
            f'<br>VAT {esc(inv.buyer_vat_number or "")}{", PO " + esc(inv.po_number) if inv.po_number else ""}</div>'
            f'<table style="width:100%;margin-top:18px;border-collapse:collapse"><thead><tr>'
            f'<th style="text-align:left;border-bottom:1px solid #999;padding:4px 0">Description</th>'
            f'<th class="tm-right" style="border-bottom:1px solid #999">Qty</th><th class="tm-right" style="border-bottom:1px solid #999">Unit</th>'
            f'<th class="tm-right" style="border-bottom:1px solid #999">Amount</th></tr></thead><tbody>{lines}'
            f'<tr><td colspan="3" class="tm-right" style="padding-top:10px">Subtotal</td><td class="tm-right" style="padding-top:10px">{eur(inv.total_excl_vat)}</td></tr>'
            f'{vat_rows}<tr><td colspan="3" class="tm-right" style="font-weight:700">Total</td>'
            f'<td class="tm-right" style="font-weight:700">{eur(inv.total_incl_vat)}</td></tr></tbody></table>{rc}'
            f'<div style="margin-top:18px;color:#444">Payment within 30 days. '
            f'{mark("IBAN ending " + esc(inv.iban_last4 or "?"), iban_failed)}</div></div>')


def _vat_panel(v: Any) -> str:
    vat = getattr(v, "vat", None)
    if vat is None:
        return ('<section class="tm-card tm-pad" style="flex:1"><h2 class="tm-h2">VAT</h2>'
                '<div class="tm-empty" style="margin-top:12px">No VAT details for this invoice.</div></section>')
    rows = "".join(f'<tr><td>{esc(li.rate_label)}</td><td class="tm-num tm-right">{eur(li.net)}</td>'
                   f'<td class="tm-num tm-right">{eur(li.vat_expected)}</td><td class="tm-num tm-right">{eur(li.vat_found)}</td>'
                   f'<td>{yes_no(li.ok, "Pass", "Fail")}</td></tr>' for li in vat.lines)
    missing = ", ".join(vat.missing_fields) if vat.missing_fields else None
    return (f'<section aria-label="VAT" class="tm-card tm-pad" style="flex:1;display:flex;flex-direction:column;gap:14px">'
            f'<h2 class="tm-h2">VAT</h2><div class="tm-table-wrap"><table class="tm-table"><thead><tr><th>Rate</th>'
            f'<th class="tm-right">Net amount</th><th class="tm-right">VAT expected</th><th class="tm-right">VAT on invoice</th>'
            f'<th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>'
            f'<dl style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px 24px">'
            f'<div><dt class="tm-small">Reverse charge applies</dt><dd style="font-weight:700">'
            f'{"Yes: EU supplier, Dutch business buyer" if vat.reverse_charge_applies else "No: domestic supply"}</dd></div>'
            f'<div><dt class="tm-small">Shown correctly on invoice</dt><dd>{yes_no(vat.shown_correctly, "Yes", "No, VAT charged instead")}</dd></div>'
            f'<div><dt class="tm-small">Supplier VAT number</dt><dd>{yes_no(vat.vat_number_valid, "Valid format and checksum", "Fails the checksum")}</dd></div>'
            f'<div><dt class="tm-small">Missing mandatory fields</dt><dd>{yes_no(not missing, "None", missing or "")}</dd></div>'
            f'</dl></section>')


def _bank_panel(v: Any) -> str:
    b = getattr(v, "bank", None)
    if b is None:
        return ('<section class="tm-card tm-pad" style="flex:1"><h2 class="tm-h2">Bank and vendor</h2>'
                '<div class="tm-empty" style="margin-top:12px">No bank checks for this invoice.</div></section>')
    match = (f"Matches vendor record, ending {b.invoice_iban_last4}" if b.iban_matches
             else f"Differs: invoice ends {b.invoice_iban_last4}, record ends {b.vendor_iban_last4}")
    return (f'<section aria-label="Bank and vendor" class="tm-card tm-pad" style="flex:1;display:flex;flex-direction:column;gap:14px">'
            f'<h2 class="tm-h2">Bank and vendor</h2><dl style="display:flex;flex-direction:column;gap:12px">'
            f'<div><dt class="tm-small">IBAN on invoice</dt><dd>{yes_no(b.iban_matches, match, match)}</dd></div>'
            f'<div><dt class="tm-small">Last change to bank details in the vendor record</dt><dd style="font-weight:700">{fmt_date(b.last_changed)}</dd></div>'
            f'<div><dt class="tm-small">Sanctions screening</dt><dd>{yes_no(not b.sanctions_hit, "No close match on the EU list", "Close match on the EU list")}</dd></div>'
            f'<div><dt class="tm-small">Overlap with employee records</dt><dd>{yes_no(not b.employee_overlap, "None", "Match found")}</dd></div>'
            f'</dl></section>')
