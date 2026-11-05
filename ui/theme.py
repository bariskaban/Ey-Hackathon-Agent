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
# Tokens (mirror the "Design tokens" artboard)
# --------------------------------------------------------------------------
LIGHT = {
    "ground": "#ECEFE9", "sheet": "#FBFCF9", "ink": "#1A2430", "graphite": "#4A5560",
    "rule": "#D3DED0", "rule-faint": "#E2EADF", "rule-strong": "#9FB39B", "border": "#C5D3C2",
    "rail": "#17222D", "rail-text": "#C9D3CC", "highlighter": "#E4F07A", "wash": "#F7FAD8",
    "paper": "#FFFFFF", "muted": "#5A6570", "body2": "#3B4A59",
}
DARK = {
    "ground": "#10171E", "sheet": "#18222C", "ink": "#E6ECE4", "graphite": "#A9B6AE",
    "rule": "#2C3B37", "rule-faint": "#24312F", "rule-strong": "#4A6152", "border": "#33443F",
    "rail": "#0B1117", "rail-text": "#B6C2BA", "highlighter": "#D6E464", "wash": "#26301F",
    "paper": "#F4F6F2", "muted": "#9AA7A0", "body2": "#C3CDC6",
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
    "running": {"label": "Running", "icon": "\u2026", "base": "#1A2430", "text": "#1A2430", "tint": "#E4F07A"},
    "waiting": {"label": "Waiting", "icon": "", "base": "#B7C5B4", "text": "#5A6570", "tint": "transparent"},
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
    ("eval", "Eval results", "/eval", '<path d="M3 3v18h18"/><path d="M8 17V11"/><path d="M13 17V7"/><path d="M18 17v-4"/>'),
]

LOGO_SVG = ('<svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true">'
            '<rect x="1.5" y="1.5" width="27" height="27" rx="4" fill="none" stroke="var(--tm-highlighter)" stroke-width="2.5"/>'
            '<path d="M8 15.5l4.5 4.5L22 9.5" fill="none" stroke="var(--tm-highlighter)" stroke-width="3" '
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
    return "".join(f"--tm-{k}:{v};" for k, v in tokens.items())


CSS = f"""
@font-face{{font-family:'Atkinson Hyperlegible Next';font-weight:400;font-display:swap;src:url('/static/fonts/atkinson-hyperlegible-next-latin-400-normal.woff2') format('woff2')}}
@font-face{{font-family:'Atkinson Hyperlegible Next';font-weight:500;font-display:swap;src:url('/static/fonts/atkinson-hyperlegible-next-latin-500-normal.woff2') format('woff2')}}
@font-face{{font-family:'Atkinson Hyperlegible Next';font-weight:700;font-display:swap;src:url('/static/fonts/atkinson-hyperlegible-next-latin-700-normal.woff2') format('woff2')}}
@font-face{{font-family:'Atkinson Hyperlegible Mono';font-weight:400;font-display:swap;src:url('/static/fonts/atkinson-hyperlegible-mono-latin-400-normal.woff2') format('woff2')}}
@font-face{{font-family:'Atkinson Hyperlegible Mono';font-weight:500;font-display:swap;src:url('/static/fonts/atkinson-hyperlegible-mono-latin-500-normal.woff2') format('woff2')}}
@font-face{{font-family:'Zilla Slab';font-weight:500;font-display:swap;src:url('/static/fonts/zilla-slab-latin-500-normal.woff2') format('woff2')}}
@font-face{{font-family:'Zilla Slab';font-weight:600;font-display:swap;src:url('/static/fonts/zilla-slab-latin-600-normal.woff2') format('woff2')}}
@font-face{{font-family:'Zilla Slab';font-weight:700;font-display:swap;src:url('/static/fonts/zilla-slab-latin-700-normal.woff2') format('woff2')}}

:root{{{_vars(LIGHT)}}}
body.body--dark{{{_vars(DARK)}}}
body{{margin:0;background:var(--tm-ground);color:var(--tm-ink);
  font-family:'Atkinson Hyperlegible Next',system-ui,sans-serif;font-size:15px;line-height:1.45}}
.nicegui-content{{padding:0 !important;gap:0 !important}}
a{{color:var(--tm-ink)}} a:hover{{color:var(--tm-body2)}}
:focus-visible{{outline:3px solid var(--tm-ink) !important;outline-offset:2px}}

.tm-app{{display:flex;flex-wrap:wrap;min-height:100vh;width:100%;background:var(--tm-ground)}}
.tm-rail{{flex:1 1 232px;background:var(--tm-rail);padding:24px 16px;display:flex;flex-direction:column;gap:28px;box-sizing:border-box}}
.tm-brand{{display:flex;align-items:center;gap:10px;text-decoration:none;color:#F4F7F2;padding:0 8px}}
.tm-brand span{{font-family:'Zilla Slab',serif;font-size:25px;font-weight:700;letter-spacing:-0.2px}}
.tm-nav{{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;flex-direction:column;gap:4px}}
.tm-nav-link{{display:flex;align-items:center;gap:12px;min-height:44px;padding:0 12px;border-radius:6px;
  color:var(--tm-rail-text);text-decoration:none;font-size:15px}}
.tm-nav-link:hover{{background:#22303C;color:#FFFFFF}}
.tm-nav-link[aria-current="page"]{{background:var(--tm-highlighter);color:#17222D;font-weight:700}}
.tm-nav-count{{margin-left:auto;font-size:12px;font-weight:700;background:#F4F7F2;color:#17222D;border-radius:10px;padding:1px 8px}}
.tm-rail-note{{margin-top:auto;padding:14px 12px;border:1px solid #2E3D49;border-radius:6px;font-size:13px;line-height:1.5;color:#AEBAB2}}
.tm-main{{flex:999 1 560px;min-width:0;padding:28px 40px 48px;box-sizing:border-box;display:flex;flex-direction:column;gap:22px}}
@media (max-width:700px){{.tm-main{{padding:20px 16px 40px}} .tm-nav{{flex-direction:row}}}}

.tm-header{{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px}}
.tm-h1{{margin:0;font-family:'Zilla Slab',serif;font-size:36px;font-weight:600;letter-spacing:-0.3px;line-height:1.15}}
.tm-h2{{margin:0;font-family:'Zilla Slab',serif;font-size:22px;font-weight:600}}
.tm-h3{{margin:0;font-family:'Zilla Slab',serif;font-size:20px;font-weight:600}}
.tm-sub{{margin:4px 0 0;font-size:15px;color:var(--tm-graphite)}}
.tm-small{{font-size:13px;color:var(--tm-graphite)}}
.tm-crumb{{margin:0 0 4px;font-size:14px;color:var(--tm-graphite)}}
.tm-num{{font-family:'Atkinson Hyperlegible Mono',monospace;font-size:13px}}
.tm-card{{background:var(--tm-sheet);border:1px solid var(--tm-border);border-radius:8px}}
.tm-card-head{{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:10px;
  padding:16px 18px;border-bottom:2px solid var(--tm-rule-strong)}}
.tm-pad{{padding:18px 20px}}
.tm-row{{display:flex;flex-wrap:wrap;gap:22px;align-items:flex-start}}
.tm-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}}

.tm-pill{{display:inline-flex;align-items:center;gap:8px;height:36px;padding:0 14px;background:var(--tm-sheet);
  border:1px solid var(--tm-border);border-radius:18px;font-size:14px}}
.tm-pill-strong{{border:2px solid var(--tm-ink);background:transparent;font-weight:700}}
.tm-dot{{width:9px;height:9px;border-radius:5px;display:inline-block}}

.tm-btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:44px;padding:0 20px;
  border:2px solid var(--tm-ink);border-radius:6px;font:inherit;font-size:15px;font-weight:700;cursor:pointer;
  text-decoration:none;background:var(--tm-sheet);color:var(--tm-ink);white-space:nowrap}}
.tm-btn:hover{{background:var(--tm-wash)}}
.tm-btn-primary{{background:var(--tm-ink);color:var(--tm-ground)}}
.tm-btn-primary:hover{{background:var(--tm-body2);color:var(--tm-ground)}}
.tm-btn-small{{min-height:34px;padding:0 14px;font-size:13px;border-width:1.5px}}
.tm-btn[disabled]{{opacity:.45;cursor:not-allowed}}

.tm-badge{{display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 10px 0 6px;border:1.5px solid;
  border-radius:4px;font-size:13px;font-weight:700;white-space:nowrap}}
.tm-badge-icon{{width:18px;text-align:center}}
.tm-tag{{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;padding:2px 8px;
  border-radius:4px;border:1.5px solid;white-space:nowrap}}
.tm-tag-wrap{{white-space:normal;align-items:flex-start;text-align:left;line-height:1.35;max-width:100%}}
.tm-role{{font-size:12px;font-weight:700;padding:1px 7px;border-radius:4px;border:1.5px solid var(--tm-ink);white-space:nowrap}}
.tm-role-extractor{{background:var(--tm-sheet);color:var(--tm-ink)}}
.tm-role-verifier{{background:var(--tm-ink);color:var(--tm-ground)}}
.tm-role-reporter{{background:var(--tm-highlighter);color:#17222D}}

.tm-stamp{{display:inline-flex;flex-direction:column;align-items:center;gap:2px;transform:rotate(-3deg);
  border:4px double;border-radius:6px;padding:12px 22px 10px}}
.tm-stamp-word{{display:flex;align-items:center;gap:10px;font-family:'Zilla Slab',serif;font-size:44px;
  font-weight:700;letter-spacing:3px;line-height:1;text-transform:uppercase}}
.tm-stamp-small .tm-stamp-word{{font-size:22px;letter-spacing:2px}}
.tm-stamp-small{{padding:6px 14px;border-width:3px}}

.tm-table-wrap{{overflow-x:auto}}
.tm-table{{width:100%;border-collapse:collapse}}
.tm-table th{{text-align:left;font-weight:500;font-size:13px;color:var(--tm-graphite);padding:10px 12px;
  border-bottom:2px solid var(--tm-rule-strong);white-space:nowrap}}
.tm-table td{{padding:9px 12px;border-bottom:1px solid var(--tm-rule);font-size:14px;vertical-align:middle}}
.tm-table td+td,.tm-table th+th{{border-left:1px solid var(--tm-rule-faint)}}
.tm-table tr.tm-hl td{{background:var(--tm-wash)}}
.tm-table tr.tm-bad td{{background:#FBEDEC}}
body.body--dark .tm-table tr.tm-bad td{{background:#3A1F1F}}
.tm-right{{text-align:right !important}}
.tm-nowrap{{white-space:nowrap}}

.tm-field{{display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--tm-graphite)}}
.tm-input{{height:42px;padding:0 10px;border:1px solid var(--tm-rule-strong);border-radius:6px;
  background:var(--tm-paper);font:inherit;font-size:15px;color:#1A2430}}
textarea.tm-input{{height:auto;padding:10px 12px}}

.tm-bar{{height:10px;border-radius:5px;background:var(--tm-rule);overflow:hidden}}
.tm-bar>div{{height:10px;background:var(--tm-ink)}}
.tm-steps{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  background:var(--tm-sheet);border:1px solid var(--tm-border);border-radius:8px;overflow:hidden}}
.tm-step{{padding:14px 16px;display:flex;flex-direction:column;gap:4px;border-right:1px solid var(--tm-rule);
  border-bottom:4px solid var(--tm-rule)}}
.tm-step-done{{border-bottom-color:var(--tm-ink)}}
.tm-step-now{{background:var(--tm-wash);border-bottom-color:#C9D63A}}
.tm-step-failed{{background:#FBEDEC;border-bottom-color:#DC2626}}
.tm-check{{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--tm-rule-faint)}}
.tm-check-icon{{flex:0 0 26px;height:26px;display:inline-flex;align-items:center;justify-content:center;
  border-radius:13px;border:2px solid;font-size:13px;font-weight:700}}
.tm-feed-item{{display:flex;gap:12px;padding:10px 20px;border-bottom:1px solid var(--tm-rule-faint)}}
.tm-note{{display:flex;flex-direction:column;gap:4px;padding:12px 14px;background:var(--tm-ground);border-radius:6px}}
.tm-empty{{display:flex;align-items:center;gap:10px;padding:14px;border:1.5px dashed var(--tm-rule-strong);
  border-radius:6px;font-size:14px;color:var(--tm-graphite)}}
.tm-dropzone{{display:flex;flex-wrap:wrap;align-items:center;gap:24px;padding:22px 26px;background:var(--tm-sheet);
  border:2px dashed var(--tm-rule-strong);border-radius:8px}}
.tm-invoice{{background:#FFFFFF;color:#222;border:1px solid var(--tm-rule);padding:28px 30px;font-size:13px;line-height:1.5}}
.tm-invoice mark{{background:#E4F07A;padding:1px 4px;outline:2px solid #DC2626}}
.tm-pre{{margin:0;padding:14px 16px;background:#17222D;color:#E6ECE4;border-radius:6px;font-size:13px;
  line-height:1.6;overflow-x:auto;font-family:'Atkinson Hyperlegible Mono',monospace;white-space:pre-wrap}}
kbd{{font-family:'Atkinson Hyperlegible Mono',monospace;font-size:13px;padding:2px 8px;border:1.5px solid var(--tm-ink);
  border-bottom-width:3px;border-radius:4px;background:var(--tm-paper);color:#1A2430}}
.tm-sr{{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}}
dl{{margin:0}} dd{{margin:2px 0 0}}

/* Quasar inputs (ui.select, ui.input) restyled to match */
.q-field--outlined .q-field__control{{border-radius:6px;background:var(--tm-paper)}}
.q-field--outlined .q-field__control:before{{border-color:var(--tm-rule-strong)}}
.q-field__native,.q-field__label,.q-item{{font-family:'Atkinson Hyperlegible Next',system-ui,sans-serif}}
.q-field__native{{color:#1A2430}}
.q-uploader{{box-shadow:none}}
.q-notification{{font-family:'Atkinson Hyperlegible Next',system-ui,sans-serif;font-size:15px}}
"""


def setup() -> None:
    """Register static files once, at import time of main.py."""
    app.add_static_files("/static", str(STATIC))


def _head() -> None:
    ui.add_head_html(f"<style>{CSS}</style>")
    ui.colors(primary="#1A2430", secondary="#4A5560", accent="#E4F07A", positive="#15803D",
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


@contextmanager
def shell(active: str, status: Any, open_cases: int | None = None) -> Iterator[ui.element]:
    """The app frame every page uses. Yields the main content column."""
    _head()
    with ui.element("div").classes("tm-app"):
        with ui.element("nav").classes("tm-rail").props('aria-label="Main"'):
            html(f'<a class="tm-brand" href="/">{LOGO_SVG}<span>Tickmark</span></a>')
            items = []
            for k, label, path, icon in NAV:
                current = ' aria-current="page"' if k == active else ""
                count = (f'<span class="tm-nav-count">{open_cases}</span>'
                         if k == "review" and open_cases else "")
                items.append(f'<li><a class="tm-nav-link" href="{path}"{current}>{svg_icon(icon)}'
                             f'<span>{label}</span>{count}</a></li>')
            html('<ul class="tm-nav">' + "".join(items) + "</ul>")
            dark = ui.dark_mode()
            ui.button("Switch light and dark", on_click=dark.toggle).props("flat no-caps").classes(
                "tm-nav-link").style("justify-content:flex-start;width:100%")
            html('<div class="tm-rail-note">Client data stays on this machine. '
                 'Every tool call the agent makes is logged.</div>')
        with ui.element("main").classes("tm-main") as main:
            yield main
