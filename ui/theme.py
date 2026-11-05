"""Design tokens and the shared app shell (U0).

Everything visual lives here: colours for light and dark mode, the verdict,
severity and check-state palettes agreed at kickoff, fonts, and the layout
every page shares (the left rail and the main content column).

Fonts are bundled in ui/static/fonts so the demo works with no internet.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from nicegui import app, ui

STATIC = Path(__file__).resolve().parent / "static"

# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------
LIGHT = {
    "ground": "#F4F5FA", "sheet": "#FFFFFF", "ink": "#0F1222", "graphite": "#5A6178",
    "rule": "#E5E8F1", "rule-faint": "#EEF0F7", "rule-strong": "#CBD0E0", "border": "#E1E4EE",
    "rail": "#0A0D1A", "rail-text": "#9AA2BC", "accent": "#5B4BDB", "accent-soft": "#EFECFF",
    "on-accent": "#FFFFFF", "paper": "#FFFFFF", "muted": "#5A6178", "body2": "#363C55",
    "shadow-sm": "0 1px 2px rgba(15,18,34,.05), 0 10px 30px -16px rgba(15,18,34,.16)",
    "shadow-md": "0 2px 4px rgba(15,18,34,.05), 0 18px 44px -20px rgba(15,18,34,.26)",
}
DARK = {
    "ground": "#080B16", "sheet": "#111529", "ink": "#ECEEF8", "graphite": "#9AA2BC",
    "rule": "#222845", "rule-faint": "#191E36", "rule-strong": "#3A426A", "border": "#232947",
    "rail": "#060812", "rail-text": "#8E96B2", "accent": "#8E80FF", "accent-soft": "#1D1A45",
    "on-accent": "#0B0820", "paper": "#171C35", "muted": "#9AA2BC", "body2": "#C5CADD",
    "shadow-sm": "0 1px 2px rgba(0,0,0,.45), 0 10px 30px -16px rgba(0,0,0,.7)",
    "shadow-md": "0 2px 4px rgba(0,0,0,.45), 0 18px 44px -20px rgba(0,0,0,.8)",
}

# Agreed colours (todo.md) for borders and icons; darker "text" shades pass 4.5:1.
VERDICTS = {
    "MATCHED": {"label": "Matched", "icon": "\u2713", "base": "#16A34A", "text": "#15803D", "tint": "#ECF6EE"},
    "PARTIAL_MATCH": {"label": "Partial match", "icon": "\u25D0", "base": "#D97706", "text": "#B45309", "tint": "#FCF3E6"},
    "MISMATCH": {"label": "Mismatch", "icon": "\u2715", "base": "#DC2626", "text": "#B91C1C", "tint": "#FBEDEC"},
    "NOT_FOUND": {"label": "Not found", "icon": "?", "base": "#6B7280", "text": "#4B5563", "tint": "#F0F1F2"},
    "NEEDS_REVIEW": {"label": "Needs review", "icon": "!", "base": "#2563EB", "text": "#1D4ED8", "tint": "#EBF0FC"},
}
SEVERITIES = {
    "high": {"label": "High", "icon": "\u25B2", "base": "#DC2626", "text": "#B91C1C", "tint": "#FBEDEC"},
    "medium": {"label": "Medium", "icon": "\u25C6", "base": "#EA580C", "text": "#C2410C", "tint": "#FDF0E7"},
    "low": {"label": "Low", "icon": "\u25CF", "base": "#CA8A04", "text": "#A16207", "tint": "#FBF5E1"},
}
ERROR_SEVERITY = {
    "WRONG_AMOUNT": "medium", "QUANTITY_NOT_RECEIVED": "medium", "DUPLICATE_INVOICE": "high",
    "ALREADY_PAID": "high", "UNKNOWN_VENDOR": "high", "VAT_ARITHMETIC_ERROR": "low",
    "WRONG_VAT_RATE": "medium", "REVERSE_CHARGE_ERROR": "medium", "INVALID_VAT_NUMBER": "medium",
    "MISSING_MANDATORY_FIELDS": "medium", "CHANGED_BANK_DETAILS": "high", "SANCTIONED_VENDOR": "high",
    "UNUSUAL_AMOUNT": "medium", "PROMPT_INJECTION": "high",
}
CHECK_STATES = {
    "pass": {"label": "Pass", "icon": "\u2713", "base": "#16A34A", "text": "#15803D", "tint": "#ECF6EE"},
    "fail": {"label": "Fail", "icon": "\u2715", "base": "#DC2626", "text": "#B91C1C", "tint": "#FBEDEC"},
    "skipped": {"label": "Skipped", "icon": "\u2013", "base": "#6B7280", "text": "#4B5563", "tint": "#F0F1F2"},
    "running": {"label": "Running", "icon": "…", "base": "#5B4BDB", "text": "#4B3DC4", "tint": "#EFECFF"},
    "waiting": {"label": "Waiting", "icon": "", "base": "#C3C8D8", "text": "#5A6178", "tint": "transparent"},
}
ERROR_LABELS = {
    "WRONG_AMOUNT": "Wrong amount", "QUANTITY_NOT_RECEIVED": "Quantity not received",
    "DUPLICATE_INVOICE": "Duplicate invoice", "ALREADY_PAID": "Already paid",
    "UNKNOWN_VENDOR": "Unknown vendor", "VAT_ARITHMETIC_ERROR": "VAT arithmetic error",
    "WRONG_VAT_RATE": "Wrong VAT rate", "REVERSE_CHARGE_ERROR": "Reverse charge error",
    "INVALID_VAT_NUMBER": "Invalid VAT number", "MISSING_MANDATORY_FIELDS": "Missing mandatory fields",
    "CHANGED_BANK_DETAILS": "Changed bank details", "SANCTIONED_VENDOR": "Sanctioned vendor",
    "UNUSUAL_AMOUNT": "Unusual amount", "PROMPT_INJECTION": "Prompt injection", "CLEAN": "Clean",
}
CHECK_LABELS = {
    "prompt_injection": "No instructions aimed at the AI", "vendor_known": "Vendor is known",
    "not_duplicate": "Not a duplicate", "not_already_paid": "Not already paid",
    "po_match": "Matches purchase order", "goods_received": "Goods received",
    "vat_number_valid": "Supplier VAT number is valid", "vat_treatment": "Correct VAT treatment",
    "vat_rate": "Correct VAT rate", "mandatory_fields": "Mandatory invoice fields",
    "vat_math": "VAT arithmetic", "iban_valid": "IBAN is valid",
    "bank_details_match": "Bank details match the vendor record", "sanctions": "Not on a sanctions list",
    "employee_overlap": "No overlap with employees", "amount_typical": "Amount is typical for this vendor",
    "no_split": "No split invoices", "date_ok": "Invoice date",
}
SERVER_GROUPS = [  # (server, title) in the order the checklist shows them
    ("host", "Document safety"), ("transactions", "Company records"), ("tax_rules", "Dutch VAT rules"),
    ("vendor_checks", "Vendor checks"), ("anomaly_stats", "Unusual patterns"),
]
CASE_STATUS_LABELS = {"OPEN": "Open", "ESCALATED": "Escalated", "APPROVED": "Approved", "REJECTED": "Rejected"}
DOC_STATUS_LABELS = {"VERIFIED": "Verified", "VERIFYING": "Verifying", "QUEUED": "Queued", "NOT_VERIFIED": "Not verified"}
ROLE_LABELS = {"extractor": "Extractor", "verifier": "Verifier", "reporter": "Reporter"}

NAV = [  # (key, label, path, svg path data)
    ("inbox", "Inbox", "/", '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/>'),
    ("verify", "Live verification", "/verify", '<path d="M22 12h-4l-3 8L9 4l-3 8H2"/>'),
    ("verdict", "Verdicts", "/verdict", '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 15l2 2 4-4"/>'),
    ("review", "Review queue", "/review", '<path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9"/>'),
    ("audit", "What the AI saw", "/audit", '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>'),
]

BRAND = "Vera"
TAGLINE = "Invoice verification"

LOGO_SVG = ('<svg width="38" height="38" viewBox="0 0 38 38" aria-hidden="true"><defs>'
            '<linearGradient id="vera-g" x1="4" y1="2" x2="34" y2="36" gradientUnits="userSpaceOnUse">'
            '<stop offset="0" stop-color="#9C90FF"/><stop offset="1" stop-color="#4B3DC4"/></linearGradient></defs>'
            '<rect width="38" height="38" rx="12" fill="url(#vera-g)"/>'
            '<path d="M10.5 15l7.2 11 10-14.5" fill="none" stroke="#fff" stroke-width="3.4" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>')


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------
def key(x: Any) -> str | None:
    """Enum or string to its name, so mock strings and real str-Enums compare the same."""
    if x is None:
        return None
    if isinstance(x, Enum):
        return x.name
    return str(x)


def eur(amount: Any, currency: str | None = "EUR") -> str:
    """English-style euro formatting: €1,234.56 (todo.md U0)."""
    if amount is None or amount == "":
        return "\u2013"
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return str(amount)
    sign = "-" if value < 0 else ""
    symbol = "\u20ac" if (currency or "EUR") == "EUR" else f"{currency} "
    return f"{sign}{symbol}{abs(value):,.2f}"


def fmt_date(value: Any) -> str:
    if not value:
        return "\u2013"
    if isinstance(value, (datetime, date)):
        d = value
    else:
        try:
            d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)
    return d.strftime("%d %b %Y")


def fmt_time(value: Any) -> str:
    if not value:
        return ""
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return d.strftime("%H:%M:%S")


def esc(text: Any) -> str:
    import html
    return html.escape("" if text is None else str(text))


# --------------------------------------------------------------------------
# CSS
# --------------------------------------------------------------------------
def _vars(tokens: dict[str, str]) -> str:
    return "".join(f"--vr-{k}:{v};" for k, v in tokens.items())


STYLESHEET = STATIC / "vera.css"


def setup() -> None:
    """Register static files once, at import time of main.py."""
    app.add_static_files("/static", str(STATIC))


def _head() -> None:
    version = int(STYLESHEET.stat().st_mtime)  # a changed stylesheet is fetched again
    ui.add_head_html(f"<style>:root{{{_vars(LIGHT)}}}body.body--dark{{{_vars(DARK)}}}</style>"
                     f'<link rel="stylesheet" href="/static/vera.css?v={version}">')
    ui.colors(primary="#5B4BDB", secondary="#5A6178", accent="#8E80FF", positive="#15803D",
              negative="#B91C1C", warning="#B45309", info="#1D4ED8")


def html(content: str, tag: str = "div") -> ui.html:
    """Raw HTML for static markup. Every value inside must go through esc()."""
    return ui.html(content, sanitize=False, tag=tag)


def svg_icon(paths: str, size: int = 18) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>')


LOCK = svg_icon('<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>', 16)
TICK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#15803D" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>'
CROSS = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#B91C1C" stroke-width="3" stroke-linecap="round" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12"/></svg>'


def status_chip(status: Any) -> str:
    """The header chip: is the model up, and how many servers. Always a word as well as a colour."""
    servers_up, servers_total = getattr(status, "servers_up", None), getattr(status, "servers_total", None)
    if not getattr(status, "ollama_up", True):
        dot, text = "#B91C1C", "Ollama is not running"
    elif servers_total and servers_up != servers_total:
        dot, text = "#B45309", f"{servers_up} of {servers_total} servers up"
    else:
        model = esc(getattr(status, "model", "") or "model")
        dot, text = "#15803D", f"{model} ready" + (f", {servers_total} servers up" if servers_total else "")
    return (f'<div class="vr-topbar"><span class="vr-pill vr-pill-strong">{LOCK}Runs 100% locally</span>'
            f'<span class="vr-pill" role="status"><span class="vr-dot" style="background:{dot}"></span>{text}</span></div>')


@contextmanager
def shell(active: str, status: Any, open_cases: int | None = None) -> Iterator[ui.element]:
    """The app frame every page uses. Yields the main content column."""
    _head()
    with ui.element("div").classes("vr-app"):
        with ui.element("nav").classes("vr-rail").props('aria-label="Main"'):
            html(f'<a class="vr-brand" href="/">{LOGO_SVG}<span class="vr-brand-text">'
                 f'<span class="vr-brand-name">{BRAND}</span><span class="vr-brand-tag">{TAGLINE}</span></span></a>')
            items = []
            for k, label, path, icon in NAV:
                current = ' aria-current="page"' if k == active else ""
                count = (f'<span class="vr-nav-count">{open_cases}</span>'
                         if k == "review" and open_cases else "")
                items.append(f'<li><a class="vr-nav-link" href="{path}"{current}>{svg_icon(icon)}'
                             f'<span>{label}</span>{count}</a></li>')
            html('<div><div class="vr-nav-label">Workspace</div><ul class="vr-nav">' + "".join(items) + "</ul></div>")
            with ui.element("div").classes("vr-rail-foot"):
                dark = ui.dark_mode()
                ui.button("Light or dark", icon="contrast", on_click=dark.toggle).props("flat no-caps").classes(
                    "vr-nav-link").style("width:100%")
                html(f'<div class="vr-rail-note"><span class="vr-rail-lock">{LOCK}</span><span>'
                     '<strong>Private by design</strong>Client data stays on this machine. '
                     'Every tool call the agent makes is logged.</span></div>')
        with ui.element("main").classes("vr-main") as main:
            html(status_chip(status))
            yield main
