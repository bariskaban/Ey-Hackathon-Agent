"""Small building blocks shared by the pages. All return HTML strings for theme.html().

Every status is shown with an icon AND a word, never colour alone (todo.md U0).
"""
from __future__ import annotations

from typing import Any

from ui.theme import (CHECK_STATES, ERROR_LABELS, SEVERITIES, VERDICTS, esc, eur, fmt_date, key)


def verdict_badge(verdict: Any) -> str:
    v = VERDICTS.get(key(verdict) or "")
    if not v:
        return '<span class="tm-small">Not verified yet</span>'
    return (f'<span class="tm-badge" style="border-color:{v["base"]};background:{v["tint"]};color:{v["text"]}">'
            f'<span class="tm-badge-icon" aria-hidden="true">{v["icon"]}</span>{v["label"]}</span>')


def stamp(verdict: Any, doc_id: str = "", when: Any = None, small: bool = False) -> str:
    """The rubber-stamp verdict: the one loud element in the design."""
    v = VERDICTS.get(key(verdict) or "", VERDICTS["NEEDS_REVIEW"])
    sub = "" if small else (f'<span class="tm-num" style="font-size:12px;letter-spacing:1px">'
                            f'{esc(doc_id)}, {esc(fmt_date(when)).upper()}</span>')
    return (f'<div role="img" aria-label="Verdict: {v["label"]}" class="tm-stamp{" tm-stamp-small" if small else ""}" '
            f'style="border-color:{v["base"]};color:{v["text"]};background:{v["tint"]}">'
            f'<span class="tm-stamp-word"><span aria-hidden="true">{v["icon"]}</span>{v["label"]}</span>{sub}</div>')


def severity_tag(severity: Any, text: str | None = None) -> str:
    s = SEVERITIES.get(key(severity) or "")
    if not s:
        return ""
    label = text or f'{s["label"]} severity'
    return (f'<span class="tm-tag" style="border-color:{s["base"]};color:{s["text"]}">'
            f'<span aria-hidden="true">{s["icon"]}</span>{esc(label)}</span>')


def error_label(error_type: Any) -> str:
    k = key(error_type) or ""
    return ERROR_LABELS.get(k, k.replace("_", " ").capitalize())


def check_icon(state: str) -> str:
    c = CHECK_STATES.get(state, CHECK_STATES["waiting"])
    return (f'<span class="tm-check-icon" aria-hidden="true" '
            f'style="border-color:{c["base"]};background:{c["tint"]};color:{c["text"]}">{c["icon"]}</span>')


def check_word(state: str) -> str:
    c = CHECK_STATES.get(state, CHECK_STATES["waiting"])
    return f'<span style="font-size:13px;font-weight:700;color:{c["text"]}">{c["label"]}</span>'


def evidence_table(items: Any) -> str:
    """Evidence records exactly as the agent saw them (masked). Empty string when there are none."""
    rows = "".join(
        f'<tr><td class="tm-num">{esc(e.record_id)}</td><td>{esc(e.kind)}</td><td>{fmt_date(e.date)}</td>'
        f'<td class="tm-num tm-right">{eur(e.amount, e.currency) if e.amount else chr(0x2013)}</td>'
        f'<td class="tm-num">{(chr(0x2022) * 4 + " " + esc(e.iban_last4)) if e.iban_last4 else "not shown"}</td>'
        f'<td>{esc(e.status)}</td></tr>' for e in (items or []))
    if not rows:
        return '<div class="tm-empty" style="margin:16px">No company records were used for this verdict.</div>'
    return ('<div class="tm-table-wrap"><table class="tm-table"><thead><tr><th>Record</th><th>Type</th><th>Date</th>'
            '<th class="tm-right">Amount</th><th>IBAN</th><th>Status</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def role_tag(role: Any) -> str:
    r = (key(role) or "").lower()
    return f'<span class="tm-role tm-role-{esc(r)}">{esc(r.capitalize())}</span>'


def yes_no(ok: bool | None, yes: str, no: str) -> str:
    if ok is None:
        return '<span class="tm-small">Not checked</span>'
    colour, icon, text = ("#15803D", "\u2713", yes) if ok else ("#B91C1C", "\u2715", no)
    return f'<span style="font-weight:700;color:{colour}"><span aria-hidden="true">{icon}</span> {esc(text)}</span>'


def page_header(title: str, sub: str = "", crumb: str = "") -> str:
    return (f'<div>{f"<p class=tm-crumb>{crumb}</p>" if crumb else ""}'
            f'<h1 class="tm-h1">{esc(title)}</h1>{f"<p class=tm-sub>{sub}</p>" if sub else ""}</div>')
