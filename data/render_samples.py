"""Draw invoices in many different designs, to stress test the reader.

Real invoices do not look alike: they differ in layout, logo, colours, wording, number and date formats,
which columns they show, where the totals sit, and whether they are a clean PDF, a scan, a photo or a fax.
This module takes one invoice spec (the same data the plain test invoices are made from) and draws it in
a chosen design. The data never changes, only how it looks.

    render(spec, design, path)       draw one invoice; the file type follows design["out"]

`scripts/make_samples.py` holds the 30 designs and calls this.
"""
from __future__ import annotations

import math
import random
import textwrap
from datetime import date
from decimal import Decimal
from pathlib import Path

import pypdfium2 as pdfium
import reportlab
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

# Vera (shipped with reportlab) is embedded in the PDF with a text map, so a text extractor reads the euro sign
# correctly. The built-in Helvetica, Times and Courier have no such map and the euro sign comes out as garbage.
_VERA = Path(reportlab.__file__).parent / "fonts"
for _name, _file in (("Vera", "Vera.ttf"), ("Vera-Bold", "VeraBd.ttf"), ("Vera-Italic", "VeraIt.ttf"),
                     ("Vera-BoldItalic", "VeraBI.ttf")):
    pdfmetrics.registerFont(TTFont(_name, str(_VERA / _file)))
pdfmetrics.registerFontFamily("Vera", normal="Vera", bold="Vera-Bold", italic="Vera-Italic", boldItalic="Vera-BoldItalic")

FONTS = {  # family -> (regular, bold, italic)
    "sans": ("Vera", "Vera-Bold", "Vera-Italic"),
    "serif": ("Times-Roman", "Times-Bold", "Times-Italic"),
    "mono": ("Courier", "Courier-Bold", "Courier-Oblique"),
}

PALETTES = {  # name -> (main, dark, tint, ink)
    "navy": ("#1F3A5F", "#122338", "#E6ECF4", "#1B2430"),
    "forest": ("#1E6B4F", "#0F3B2B", "#E3F1EA", "#14231D"),
    "crimson": ("#A4243B", "#6B1424", "#F6E4E8", "#2A1418"),
    "amber": ("#C77700", "#7A4900", "#FBEFD9", "#2A2110"),
    "violet": ("#5B3E96", "#35215F", "#ECE6F6", "#1F1830"),
    "teal": ("#00727A", "#00444A", "#DDF1F2", "#0F2426"),
    "slate": ("#3A4750", "#222B31", "#E8ECEE", "#1A2024"),
    "rose": ("#B5446E", "#782445", "#F7E3EB", "#2A1520"),
    "olive": ("#6B7A1F", "#3F4A0F", "#EDF1D6", "#1F2410"),
    "indigo": ("#2D3A8C", "#18205A", "#E3E6F6", "#151A33"),
    "copper": ("#A8572A", "#6A3114", "#F6E7DD", "#2A1A10"),
    "ink": ("#111111", "#000000", "#EEEEEE", "#111111"),
    "sea": ("#0F6FA8", "#083F63", "#DFEEF7", "#102330"),
    "plum": ("#7A2C6E", "#4A1542", "#F3E3F0", "#271324"),
    "grass": ("#3F8F2E", "#235A18", "#E5F3E0", "#14230F"),
}

TAGLINES = {
    "office": "Office supplies for business", "pantry": "Pantry and refreshments", "catering": "Catering and events",
    "books_print": "Print and publishing", "it_services": "IT consulting and hosting",
    "cleaning": "Professional cleaning", "logistics": "Transport and logistics", "packaging": "Packaging solutions",
    "maintenance": "Technical maintenance", "furniture": "Workplace furniture", "software": "Software and support",
    "design": "Brand and web design",
}

LABELS = {  # wording differs between companies
    "a": {"title": "Invoice", "no": "Invoice number", "date": "Invoice date", "supply": "Supply date",
          "due": "Due date", "po": "PO number", "vat": "VAT number", "kvk": "KvK number", "bill": "Bill to",
          "from": "From", "iban": "IBAN", "net": "Total excl. VAT", "tax": "Total VAT", "gross": "Total incl. VAT",
          "sku": "Item", "desc": "Description", "qty": "Qty", "unit": "Unit price", "rate": "VAT", "amount": "Amount excl. VAT",
          "vat_table": "VAT rate", "base": "Amount excl. VAT", "vat_amt": "VAT amount"},
    "b": {"title": "Tax invoice", "no": "Invoice no.", "date": "Date", "supply": "Delivery date",
          "due": "Payment due", "po": "Your order ref.", "vat": "VAT no.", "kvk": "CoC (KvK)", "bill": "Customer",
          "from": "Supplier", "iban": "Bank (IBAN)", "net": "Subtotal", "tax": "VAT", "gross": "Amount due",
          "sku": "Code", "desc": "Item", "qty": "Quantity", "unit": "Price", "rate": "Tax", "amount": "Net",
          "vat_table": "Rate", "base": "Net", "vat_amt": "Tax"},
    "c": {"title": "Sales invoice", "no": "Doc #", "date": "Issued", "supply": "Service date",
          "due": "Pay by", "po": "Order", "vat": "VAT ID", "kvk": "KvK", "bill": "Sold to",
          "from": "Issued by", "iban": "Pay to IBAN", "net": "Net total", "tax": "Tax", "gross": "Gross total",
          "sku": "Ref", "desc": "Details", "qty": "Units", "unit": "Rate", "rate": "VAT %", "amount": "Line total",
          "vat_table": "VAT %", "base": "Taxable", "vat_amt": "VAT"},
    "d": {"title": "Invoice", "no": "No.", "date": "Invoice date", "supply": "Date of supply",
          "due": "Due", "po": "Purchase order", "vat": "VAT reg.", "kvk": "Chamber of Commerce no.",
          "bill": "Invoiced to", "from": "Seller", "iban": "Account (IBAN)", "net": "Total (ex VAT)",
          "tax": "VAT total", "gross": "TOTAL DUE", "sku": "Art. no.", "desc": "Product / service",
          "qty": "Qty", "unit": "Unit", "rate": "VAT", "amount": "Total",
          "vat_table": "VAT", "base": "Base", "vat_amt": "VAT"},
}

LABELS["e"] = {"title": "Statement of charges", "no": "Ref. no.", "date": "Dated", "supply": "Delivered on",
               "due": "Settle by", "po": "Order ref.", "vat": "Tax ID", "kvk": "Registry no.", "bill": "Charged to",
               "from": "Provider", "iban": "Remit to (IBAN)", "net": "Sum before VAT", "tax": "VAT charged",
               "gross": "Total payable", "sku": "Code", "desc": "Goods / services", "qty": "No.", "unit": "Each",
               "rate": "VAT", "amount": "Sum", "vat_table": "Rate", "base": "Basis", "vat_amt": "VAT"}
LABELS["f"] = {"title": "Bill", "no": "Bill no.", "date": "Bill date", "supply": "Completed", "due": "Due by",
               "po": "PO ref", "vat": "VAT reg no.", "kvk": "KvK", "bill": "Client", "from": "Vendor",
               "iban": "IBAN", "net": "Amount before tax", "tax": "Tax", "gross": "Balance due", "sku": "SKU",
               "desc": "Description", "qty": "Qty", "unit": "Price each", "rate": "Tax %", "amount": "Line amount",
               "vat_table": "Tax %", "base": "Before tax", "vat_amt": "Tax"}

REVERSE_CHARGE = [
    "Reverse charge: VAT is reverse-charged to the recipient. Supplier VAT number: {s}. Customer VAT number: {b}.",
    "VAT reverse charged (intra-Community supply of services). Supplier VAT ID {s}, customer VAT ID {b}.",
    "Reverse charge applies. The customer accounts for the VAT. VAT numbers: {s} (supplier) and {b} (customer).",
]
PAY_LINES = [
    "Please pay {total} before {due} to IBAN {iban}, quoting the invoice number.",
    "Payment of {total} is due by {due}. Bank account (IBAN): {iban}. Reference: {no}.",
    "Kindly transfer {total} to {iban} no later than {due}, stating {no}.",
]

PAGE_W, PAGE_H = A4


# --------------------------------------------------------------------------- the data, as one design shows it


class View:
    """The invoice spec plus everything that depends on the design: wording, formats, colours, fonts."""

    def __init__(self, spec: dict, d: dict):
        self.sp, self.d = spec, d
        self.L = LABELS[d.get("labels", "a")]
        self.main, self.dark, self.tint, self.ink = (HexColor(c) for c in PALETTES[d["palette"]])
        self.reg, self.bold, self.ital = FONTS[d.get("font", "sans")]
        self.cols = d.get("cols", ["sku", "desc", "qty", "unit", "rate", "amount"])
        built_in = d.get("font", "sans") != "sans" or d.get("layout") in ("typewriter", "receipt")
        if built_in and d.get("money") in ("sym", "eu", "space"):  # built-in PDF fonts cannot carry the euro sign
            self.d = d = {**d, "money": "en"}
        self.s, self.b = spec["supplier"], spec["buyer"]
        self.archetype = d.get("archetype", "office")

    def money(self, value) -> str:
        amount = Decimal(str(value))
        text = f"{amount:,.2f}"
        kind = self.d.get("money", "en")
        if kind == "en":
            return f"EUR {text}"
        if kind == "sym":
            return f"€{text}"
        if kind == "eu":
            return "€ " + text.replace(",", "\0").replace(".", ",").replace("\0", ".")
        if kind == "suffix":
            return f"{text} EUR"
        if kind == "space":  # 1 234,56 followed by the euro sign
            return text.replace(",", " ").replace(".", ",") + " €"
        if kind == "apos":  # a Swiss-style thousands mark
            return "EUR " + text.replace(",", chr(39))
        return f"{amount:.2f}"  # plain

    def day(self, iso: str | None) -> str:
        if not iso:
            return ""
        parsed = date.fromisoformat(iso)
        return parsed.strftime(self.d.get("date", "%Y-%m-%d"))

    @staticmethod
    def qty(value) -> str:
        number = Decimal(str(value))
        return f"{number:f}"

    @staticmethod
    def rate(value) -> str:
        return f"{Decimal(str(value)).normalize():f}%"

    def address(self, who: dict) -> list[str]:
        return who["address"].split(", ", 1)

    def party(self, who: dict, with_iban: bool = False) -> list[str]:
        rows = [*self.address(who)]
        if who.get("vat_number"):
            rows.append(f"{self.L['vat']}: {who['vat_number']}")
        if who.get("kvk_number"):
            rows.append(f"{self.L['kvk']}: {who['kvk_number']}")
        if with_iban and who.get("iban"):
            rows.append(f"{self.L['iban']}: {who['iban']}")
        return rows

    def meta(self) -> list[tuple[str, str]]:
        sp, L = self.sp, self.L
        rows = [(L["no"], sp["invoice_number"]), (L["date"], self.day(sp["invoice_date"]))]
        if sp.get("supply_date"):
            rows.append((L["supply"], self.day(sp["supply_date"])))
        rows.append((L["due"], self.day(sp["due_date"])))
        if sp.get("po_number"):
            rows.append((L["po"], sp["po_number"]))
        return rows

    def pay_line(self) -> str:
        template = PAY_LINES[self.d.get("pay", 0) % len(PAY_LINES)]
        return template.format(total=self.money(self.sp["total_incl_vat"]), due=self.day(self.sp["due_date"]),
                               iban=self.s["iban"], no=self.sp["invoice_number"])

    def reverse_charge_text(self) -> str | None:
        if not self.sp.get("reverse_charge"):
            return None
        template = REVERSE_CHARGE[self.d.get("rc", 0) % len(REVERSE_CHARGE)]
        return template.format(s=self.s["vat_number"], b=self.b["vat_number"])

    def totals(self) -> list[tuple[str, str]]:
        sp, L = self.sp, self.L
        return [(L["net"], self.money(sp["total_excl_vat"])), (L["tax"], self.money(sp["total_vat"])),
                (L["gross"], self.money(sp["total_incl_vat"]))]

    def initials(self) -> str:
        words = [w for w in self.s["name"].replace("&", " ").split() if w[0].isalpha()]
        return "".join(w[0].upper() for w in words[:2]) or "X"

    def style(self, size=9, color=None, bold=False, align=0, leading=None) -> ParagraphStyle:
        return ParagraphStyle("p", fontName=self.bold if bold else self.reg, fontSize=size,
                              leading=leading or size * 1.3, textColor=color or self.ink, alignment=align)

    def para(self, text, **kw) -> Paragraph:
        return Paragraph(text, self.style(**kw))


# --------------------------------------------------------------------------- small drawing helpers


def text(c, x, y, s, font, size, color, align="l", space=0.0):
    c.setFont(font, size)
    c.setFillColor(color)
    if space:
        width = sum(c.stringWidth(ch, font, size) + space for ch in s) - space
        x = x - width if align == "r" else x - width / 2 if align == "c" else x
        for ch in s:
            c.drawString(x, y, ch)
            x += c.stringWidth(ch, font, size) + space
        return
    {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[align](x, y, s)


def flow(c, f, x, y_top, w) -> float:
    """Draw a flowable with its top-left at (x, y_top). Returns its height."""
    _, h = f.wrap(w, 10000)
    f.drawOn(c, x, y_top - h)
    return h


def lines_table(v: View, width: float, *, header_bg=None, header_fg=None, zebra=False, grid=False, size=9,
                rules=True) -> Table:
    L = v.L
    weights = {"sku": 0.13, "desc": 0.38, "qty": 0.11, "unit": 0.15, "rate": 0.09, "amount": 0.15}
    total = sum(weights[k] for k in v.cols)
    widths = [width * weights[k] / total for k in v.cols]
    head_color = (header_fg or white) if header_bg is not None else v.main
    head = [v.para(L[k], size=size - 0.5, color=head_color, bold=True, align=2 if k in ("qty", "unit", "rate", "amount") else 0)
            for k in v.cols]
    body = []
    for line in v.sp["lines"]:
        cells = {"sku": line.get("sku") or "", "desc": v.para(line["description"], size=size),
                 "qty": v.qty(line["quantity"]), "unit": v.money(line["unit_price"]),
                 "rate": v.rate(line["vat_rate"]), "amount": v.money(line["line_total"])}
        body.append([cells[k] for k in v.cols])
    table = Table([head, *body], colWidths=widths, repeatRows=1)
    commands = [("FONTNAME", (0, 0), (-1, 0), v.bold), ("FONTNAME", (0, 1), (-1, -1), v.reg),
                ("FONTSIZE", (0, 0), (-1, -1), size), ("TEXTCOLOR", (0, 1), (-1, -1), v.ink),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
    for index, key in enumerate(v.cols):
        if key in ("qty", "unit", "rate", "amount"):
            commands.append(("ALIGN", (index, 0), (index, -1), "RIGHT"))
    if header_bg is not None:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), header_bg))
    else:
        commands.append(("LINEBELOW", (0, 0), (-1, 0), 1, v.main))
    if rules:
        commands.append(("LINEBELOW", (0, 1), (-1, -1), 0.25, HexColor("#BBBBBB")))
    if zebra:
        commands.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, v.tint]))
    if grid:
        commands.append(("GRID", (0, 0), (-1, -1), 0.6, v.ink))
    table.setStyle(TableStyle(commands))
    return table


def vat_table(v: View, width: float, size=8.5) -> Table:
    L = v.L
    rows = [[L["vat_table"], L["base"], L["vat_amt"]]]
    for item in v.sp["vat_breakdown"]:
        rows.append([v.rate(item["rate"]), v.money(item["base"]), v.money(item["vat"])])
    table = Table(rows, colWidths=[width * 0.25, width * 0.4, width * 0.35])
    table.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), size), ("FONTNAME", (0, 0), (-1, 0), v.bold),
                               ("FONTNAME", (0, 1), (-1, -1), v.reg), ("TEXTCOLOR", (0, 0), (-1, -1), v.ink),
                               ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.5, v.ink),
                               ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return table


def totals_table(v: View, width: float, size=9.5, fill=None) -> Table:
    table = Table([[a, b] for a, b in v.totals()], colWidths=[width * 0.5, width * 0.5])
    commands = [("FONTSIZE", (0, 0), (-1, -1), size), ("FONTNAME", (0, 0), (-1, -1), v.reg),
                ("FONTNAME", (0, 2), (-1, 2), v.bold), ("TEXTCOLOR", (0, 0), (-1, -1), v.ink),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("LINEABOVE", (0, 2), (-1, 2), 1, v.ink),
                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if fill is not None:
        commands += [("BACKGROUND", (0, 2), (-1, 2), fill), ("TEXTCOLOR", (0, 2), (-1, 2), white)]
    table.setStyle(TableStyle(commands))
    return table


def meta_table(v: View, width: float, size=9, label_color=None, boxed=False) -> Table:
    table = Table([[a, b] for a, b in v.meta()], colWidths=[width * 0.45, width * 0.55])
    commands = [("FONTSIZE", (0, 0), (-1, -1), size), ("FONTNAME", (0, 0), (0, -1), v.bold),
                ("FONTNAME", (1, 0), (1, -1), v.reg), ("TEXTCOLOR", (0, 0), (0, -1), label_color or v.main),
                ("TEXTCOLOR", (1, 0), (1, -1), v.ink), ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if boxed:
        commands.append(("BOX", (0, 0), (-1, -1), 0.6, v.ink))
    table.setStyle(TableStyle(commands))
    return table


def hidden_note(c, v: View, x=18 * mm, y=9 * mm):
    """Notes that only a text extractor sees: tiny and white."""
    if v.sp.get("notes") and v.d.get("hide_notes"):
        text(c, x, y, v.sp["notes"][:200], v.reg, 1.5, white)
        return True
    return False


def visible_notes(c, v: View, x, y, w, size=8) -> float:
    if v.sp.get("notes") and not v.d.get("hide_notes"):
        return flow(c, v.para(f"Remarks: {v.sp['notes']}", size=size, color=HexColor("#555555")), x, y, w)
    return 0


def watermark(c, v: View, page_w, page_h):
    word = v.d.get("watermark")
    if not word:
        return
    c.saveState()
    c.translate(page_w / 2, page_h / 2)
    c.rotate(35)
    c.setFillColor(v.main)
    c.setFillAlpha(0.07)
    c.setFont(v.bold, 92)
    c.drawCentredString(0, 0, word)
    c.restoreState()


# --------------------------------------------------------------------------- logos


def logo(c, shape: str, x: float, y: float, size: float, v: View, on_dark: bool = False):
    """A made-up company mark with its lower-left corner at (x, y)."""
    main = white if on_dark else v.main
    second = v.tint if on_dark else v.dark
    letters = v.initials()
    r = size / 2
    cx, cy = x + r, y + r
    c.saveState()
    c.setFillColor(main)
    c.setStrokeColor(main)
    if shape == "circle":
        c.circle(cx, cy, r, fill=1, stroke=0)
        text(c, cx, cy - size * 0.17, letters, v.bold, size * 0.42, v.dark if on_dark else white, "c")
    elif shape == "rounded":
        c.roundRect(x, y, size, size, size * 0.22, fill=1, stroke=0)
        text(c, cx, cy - size * 0.17, letters, v.bold, size * 0.42, v.dark if on_dark else white, "c")
    elif shape == "hexagon":
        path = c.beginPath()
        for i in range(6):
            a = math.radians(60 * i + 30)
            px, py = cx + r * math.cos(a), cy + r * math.sin(a)
            path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
        path.close()
        c.drawPath(path, fill=1, stroke=0)
        text(c, cx, cy - size * 0.15, letters[0], v.bold, size * 0.5, v.dark if on_dark else white, "c")
    elif shape == "diamond":
        path = c.beginPath()
        path.moveTo(cx, y + size)
        path.lineTo(x + size, cy)
        path.lineTo(cx, y)
        path.lineTo(x, cy)
        path.close()
        c.drawPath(path, fill=1, stroke=0)
        c.setFillColor(second)
        c.circle(cx, cy, r * 0.28, fill=1, stroke=0)
    elif shape == "ring":
        c.setLineWidth(size * 0.13)
        c.circle(cx, cy, r * 0.86, fill=0, stroke=1)
        text(c, cx, cy - size * 0.17, letters[0], v.bold, size * 0.52, main, "c")
    elif shape == "bars":
        for i, h in enumerate((0.38, 0.6, 0.82, 1.0)):
            c.setFillColor(main if i % 2 == 0 else second)
            c.rect(x + i * size * 0.26, y, size * 0.19, size * h, fill=1, stroke=0)
    elif shape == "stripes":
        for i in range(3):
            c.setFillColor((main, second, main)[i])
            path = c.beginPath()
            ox = x + i * size * 0.34
            path.moveTo(ox, y)
            path.lineTo(ox + size * 0.24, y)
            path.lineTo(ox + size * 0.5, y + size)
            path.lineTo(ox + size * 0.26, y + size)
            path.close()
            c.drawPath(path, fill=1, stroke=0)
    elif shape == "triangle":
        path = c.beginPath()
        path.moveTo(x, y)
        path.lineTo(x + size, y)
        path.lineTo(cx, y + size)
        path.close()
        c.drawPath(path, fill=1, stroke=0)
        c.setFillColor(second)
        inner = c.beginPath()
        inner.moveTo(cx - size * 0.18, y + size * 0.1)
        inner.lineTo(cx + size * 0.18, y + size * 0.1)
        inner.lineTo(cx, y + size * 0.46)
        inner.close()
        c.drawPath(inner, fill=1, stroke=0)
    elif shape == "leaf":
        path = c.beginPath()
        path.moveTo(x, y)
        path.curveTo(x, y + size * 0.9, x + size * 0.15, y + size, x + size, y + size)
        path.curveTo(x + size, y + size * 0.15, x + size * 0.9, y, x, y)
        c.drawPath(path, fill=1, stroke=0)
        c.setStrokeColor(second)
        c.setLineWidth(size * 0.05)
        c.line(x + size * 0.12, y + size * 0.12, x + size * 0.72, y + size * 0.72)
    elif shape == "wave":
        c.setLineWidth(size * 0.11)
        c.setLineCap(1)
        for i in range(3):
            path = c.beginPath()
            base = y + size * (0.2 + i * 0.3)
            path.moveTo(x, base)
            path.curveTo(x + size * 0.3, base + size * 0.22, x + size * 0.6, base - size * 0.22, x + size, base)
            c.setStrokeColor((main, second, main)[i])
            c.drawPath(path, fill=0, stroke=1)
    elif shape == "cross":
        c.roundRect(cx - size * 0.16, y, size * 0.32, size, size * 0.08, fill=1, stroke=0)
        c.roundRect(x, cy - size * 0.16, size, size * 0.32, size * 0.08, fill=1, stroke=0)
    elif shape == "chevrons":
        c.setLineWidth(size * 0.14)
        c.setLineCap(1)
        c.setLineJoin(1)
        for i in range(2):
            c.setStrokeColor((main, second)[i])
            path = c.beginPath()
            ox = x + i * size * 0.36
            path.moveTo(ox, y + size * 0.1)
            path.lineTo(ox + size * 0.34, cy)
            path.lineTo(ox, y + size * 0.9)
            c.drawPath(path, fill=0, stroke=1)
    else:  # "square"
        c.rect(x, y, size, size, fill=1, stroke=0)
        c.setFillColor(second)
        c.rect(x + size * 0.5, y, size * 0.5, size * 0.5, fill=1, stroke=0)
    c.restoreState()


def wordmark(c, v: View, x: float, y: float, size: float, *, color=None, align="l", tagline=True, tagcolor=None):
    """The company name as a logo would set it, with its tagline under it."""
    style = v.d.get("wordmark", "bold")
    name = v.s["name"]
    color = color or v.main
    if style == "caps":
        text(c, x, y, name.upper(), v.bold, size * 0.8, color, align, space=1.4)
    elif style == "serif":
        text(c, x, y, name, "Times-Bold", size * 1.1, color, align)
    elif style == "light":
        text(c, x, y, name.lower(), "Helvetica", size * 1.05, color, align, space=0.4)
    else:
        text(c, x, y, name, v.bold, size, color, align)
    if tagline:
        text(c, x, y - size * 1.05, TAGLINES.get(v.archetype, ""), v.ital, size * 0.52,
             tagcolor or HexColor("#777777"), align)


def footer_line(v: View) -> str:
    s = v.s
    bits = [s["name"], ", ".join(s["address"].split(", ")), f"{v.L['vat']} {s['vat_number']}"]
    if s.get("kvk_number"):
        bits.append(f"{v.L['kvk']} {s['kvk_number']}")
    return "  |  ".join(bits)


# --------------------------------------------------------------------------- layouts: each draws a whole document


def layout_classic(c, v: View):
    m = 18 * mm
    c.setFillColor(v.main)
    c.rect(0, PAGE_H - 7 * mm, PAGE_W, 7 * mm, fill=1, stroke=0)
    text(c, m, PAGE_H - 30 * mm, v.L["title"].upper(), v.bold, 26, v.main, space=2)
    logo(c, v.d.get("logo", "circle"), PAGE_W - m - 15 * mm, PAGE_H - 33 * mm, 15 * mm, v)
    wordmark(c, v, PAGE_W - m - 18 * mm, PAGE_H - 22 * mm, 11, align="r")
    y = PAGE_H - 46 * mm
    flow(c, v.para("<br/>".join(v.party(v.s)), size=9), m, y, 80 * mm)
    flow(c, meta_table(v, 70 * mm), PAGE_W - m - 70 * mm, y, 70 * mm)
    y -= 36 * mm
    flow(c, v.para(f"<b>{v.L['bill']}</b><br/>{v.b['name']}<br/>" + "<br/>".join(v.party(v.b)), size=9), m, y, 90 * mm)
    y -= 38 * mm
    y -= flow(c, lines_table(v, PAGE_W - 2 * m, header_bg=v.main), m, y, PAGE_W - 2 * m) + 8 * mm
    flow(c, vat_table(v, 90 * mm), m, y, 90 * mm)
    y -= flow(c, totals_table(v, 70 * mm, fill=v.main), PAGE_W - m - 70 * mm, y, 70 * mm) + 8 * mm
    y -= 4 * mm
    if (rc := v.reverse_charge_text()):
        y -= flow(c, v.para(f"<b>{rc}</b>", size=9), m, y, PAGE_W - 2 * m) + 4 * mm
    y -= flow(c, v.para(v.pay_line(), size=9), m, y, PAGE_W - 2 * m) + 3 * mm
    visible_notes(c, v, m, y, PAGE_W - 2 * m)
    text(c, PAGE_W / 2, 10 * mm, footer_line(v)[:130], v.reg, 6.8, HexColor("#777777"), "c")


def layout_sidebar(c, v: View):
    side = 64 * mm
    c.setFillColor(v.main)
    c.rect(0, 0, side, PAGE_H, fill=1, stroke=0)
    logo(c, v.d.get("logo", "rounded"), 12 * mm, PAGE_H - 34 * mm, 18 * mm, v, on_dark=True)
    y = PAGE_H - 50 * mm
    y -= flow(c, v.para(f"<b>{v.s['name']}</b>", size=11, color=white), 12 * mm, y, side - 20 * mm) + 3 * mm
    y -= flow(c, v.para("<br/>".join(v.party(v.s, with_iban=True)), size=8, color=v.tint, leading=11.5),
              12 * mm, y, side - 20 * mm) + 8 * mm
    flow(c, v.para(TAGLINES.get(v.archetype, ""), size=8, color=v.tint), 12 * mm, 24 * mm, side - 20 * mm)
    m, left = 10 * mm, side + 12 * mm
    width = PAGE_W - left - 14 * mm
    text(c, left, PAGE_H - 30 * mm, v.L["title"], v.bold, 28, v.main)
    y = PAGE_H - 42 * mm
    flow(c, meta_table(v, 78 * mm, size=9), left, y, 78 * mm)
    flow(c, v.para(f"<b>{v.L['bill']}</b><br/>{v.b['name']}<br/>" + "<br/>".join(v.party(v.b)), size=9),
         left + 84 * mm, y, width - 84 * mm)
    y -= 46 * mm
    y -= flow(c, lines_table(v, width, zebra=True, rules=False), left, y, width) + 7 * mm
    flow(c, vat_table(v, 52 * mm, size=7.8), left, y, 52 * mm)
    y -= flow(c, totals_table(v, 60 * mm), left + width - 60 * mm, y, 60 * mm) + 9 * mm
    if (rc := v.reverse_charge_text()):
        y -= flow(c, v.para(f"<i>{rc}</i>", size=8.5), left, y, width) + 4 * mm
    y -= flow(c, v.para(v.pay_line(), size=9), left, y, width) + 3 * mm
    visible_notes(c, v, left, y, width)
    del m


def layout_banner(c, v: View):
    m = 16 * mm
    c.setFillColor(v.main)
    c.rect(0, PAGE_H - 42 * mm, PAGE_W, 42 * mm, fill=1, stroke=0)
    logo(c, v.d.get("logo", "hexagon"), m, PAGE_H - 33 * mm, 22 * mm, v, on_dark=True)
    wordmark(c, v, m + 28 * mm, PAGE_H - 22 * mm, 15, color=white, tagcolor=v.tint)
    long_title = len(v.L["title"]) > 12
    text(c, PAGE_W - m, PAGE_H - 24 * mm, v.L["title"].upper(), v.bold, 12 if long_title else 20, white, "r",
         space=1.5 if long_title else 3)
    y = PAGE_H - 50 * mm
    boxes = v.meta()
    bw = (PAGE_W - 2 * m) / len(boxes)
    for i, (label, value) in enumerate(boxes):
        x = m + i * bw
        c.setFillColor(v.tint)
        c.rect(x + 1, y - 14 * mm, bw - 2, 14 * mm, fill=1, stroke=0)
        text(c, x + 4 * mm, y - 5 * mm, label.upper(), v.bold, 6.5, v.main)
        text(c, x + 4 * mm, y - 10.5 * mm, value, v.reg, 9.5, v.ink)
    y -= 24 * mm
    half = (PAGE_W - 2 * m) / 2 - 4 * mm
    flow(c, v.para(f"<b>{v.L['from']}</b><br/>{v.s['name']}<br/>" + "<br/>".join(v.party(v.s)), size=9), m, y, half)
    flow(c, v.para(f"<b>{v.L['bill']}</b><br/>{v.b['name']}<br/>" + "<br/>".join(v.party(v.b)), size=9),
         m + half + 8 * mm, y, half)
    y -= 38 * mm
    y -= flow(c, lines_table(v, PAGE_W - 2 * m, grid=False, zebra=True), m, y, PAGE_W - 2 * m) + 6 * mm
    flow(c, vat_table(v, 84 * mm), m, y, 84 * mm)
    y -= flow(c, totals_table(v, 74 * mm, fill=v.dark), PAGE_W - m - 74 * mm, y, 74 * mm) + 8 * mm
    if (rc := v.reverse_charge_text()):
        y -= flow(c, v.para(f"<b>{rc}</b>", size=8.5), m, y, PAGE_W - 2 * m) + 3 * mm
    y -= flow(c, v.para(v.pay_line(), size=9), m, y, PAGE_W - 2 * m) + 3 * mm
    visible_notes(c, v, m, y, PAGE_W - 2 * m)
    c.setFillColor(v.dark)
    c.rect(0, 0, PAGE_W, 12 * mm, fill=1, stroke=0)
    text(c, PAGE_W / 2, 4.6 * mm, f"{v.L['iban']}  {v.s['iban']}", v.reg, 8, white, "c", space=0.5)


def layout_minimal(c, v: View):
    m = 24 * mm
    text(c, m, PAGE_H - 30 * mm, v.L["title"].lower(), v.reg, 30, v.ink, space=4)
    logo(c, v.d.get("logo", "ring"), PAGE_W - m - 11 * mm, PAGE_H - 36 * mm, 11 * mm, v)
    c.setStrokeColor(v.ink)
    c.setLineWidth(0.4)
    c.line(m, PAGE_H - 40 * mm, PAGE_W - m, PAGE_H - 40 * mm)
    y = PAGE_H - 50 * mm
    flow(c, v.para(f"<b>{v.s['name']}</b><br/>" + "<br/>".join(v.party(v.s)), size=9), m, y, 80 * mm)
    flow(c, meta_table(v, 64 * mm, size=8.5, label_color=HexColor("#777777")), PAGE_W - m - 64 * mm, y, 64 * mm)
    y -= 42 * mm
    flow(c, v.para(f"{v.L['bill']}<br/><b>{v.b['name']}</b><br/>" + "<br/>".join(v.party(v.b)), size=9), m, y, 90 * mm)
    y -= 38 * mm
    y -= flow(c, lines_table(v, PAGE_W - 2 * m, size=8.8, rules=False), m, y, PAGE_W - 2 * m) + 6 * mm
    y -= flow(c, totals_table(v, 66 * mm), PAGE_W - m - 66 * mm, y, 66 * mm) + 6 * mm
    flow(c, vat_table(v, 76 * mm, size=8), m, y + 24 * mm, 76 * mm)
    y -= 4 * mm
    if (rc := v.reverse_charge_text()):
        y -= flow(c, v.para(rc, size=8.5), m, y, PAGE_W - 2 * m) + 4 * mm
    y -= flow(c, v.para(v.pay_line(), size=9), m, y, PAGE_W - 2 * m) + 3 * mm
    visible_notes(c, v, m, y, PAGE_W - 2 * m)
    wordmark(c, v, PAGE_W / 2, 18 * mm, 8, color=HexColor("#555555"), align="c", tagline=False)


def layout_ledger(c, v: View):
    """A pre-printed form: everything in ruled boxes with small captions."""
    m = 12 * mm
    w = PAGE_W - 2 * m
    c.setStrokeColor(v.ink)
    c.setLineWidth(1.2)
    c.rect(m, 12 * mm, w, PAGE_H - 24 * mm)
    c.setFillColor(v.main)
    c.rect(m, PAGE_H - 34 * mm, w, 22 * mm, fill=1, stroke=0)
    logo(c, v.d.get("logo", "square"), m + 5 * mm, PAGE_H - 31 * mm, 16 * mm, v, on_dark=True)
    wordmark(c, v, m + 26 * mm, PAGE_H - 22 * mm, 13, color=white, tagcolor=v.tint)
    text(c, m + w - 5 * mm, PAGE_H - 26 * mm, v.L["title"].upper(), v.bold, 18, white, "r")
    y = PAGE_H - 34 * mm
    c.setLineWidth(0.6)
    c.line(m + w / 2, y, m + w / 2, y - 46 * mm)
    c.line(m, y - 46 * mm, m + w, y - 46 * mm)
    text(c, m + 3 * mm, y - 4.5 * mm, v.L["from"].upper(), v.bold, 6.5, v.main)
    flow(c, v.para(f"<b>{v.s['name']}</b><br/>" + "<br/>".join(v.party(v.s, with_iban=True)), size=8.5),
         m + 3 * mm, y - 7 * mm, w / 2 - 6 * mm)
    text(c, m + w / 2 + 3 * mm, y - 4.5 * mm, v.L["bill"].upper(), v.bold, 6.5, v.main)
    flow(c, v.para(f"<b>{v.b['name']}</b><br/>" + "<br/>".join(v.party(v.b)), size=8.5),
         m + w / 2 + 3 * mm, y - 7 * mm, w / 2 - 6 * mm)
    y -= 46 * mm
    items = v.meta()
    cell = w / len(items)
    for i, (label, value) in enumerate(items):
        x = m + i * cell
        if i:
            c.line(x, y, x, y - 13 * mm)
        text(c, x + 2 * mm, y - 4.5 * mm, label.upper(), v.bold, 6, v.main)
        text(c, x + 2 * mm, y - 10 * mm, value, v.reg, 9, v.ink)
    y -= 13 * mm
    c.line(m, y, m + w, y)
    y -= 4 * mm
    y -= flow(c, lines_table(v, w - 8 * mm, grid=True, header_bg=v.tint, header_fg=v.ink, size=8.5),
              m + 4 * mm, y, w - 8 * mm) + 6 * mm
    flow(c, vat_table(v, 80 * mm), m + 4 * mm, y, 80 * mm)
    y -= flow(c, totals_table(v, 72 * mm, size=10), m + w - 76 * mm, y, 72 * mm) + 8 * mm
    if (rc := v.reverse_charge_text()):
        y -= flow(c, v.para(f"<b>{rc}</b>", size=8.5), m + 4 * mm, y, w - 8 * mm) + 3 * mm
    y -= flow(c, v.para(v.pay_line(), size=9), m + 4 * mm, y, w - 8 * mm) + 3 * mm
    visible_notes(c, v, m + 4 * mm, y, w - 8 * mm)


def layout_letter(c, v: View):
    """A business letter: letterhead, address window, subject line, a remittance slip on page two."""
    m = 22 * mm
    logo(c, v.d.get("logo", "diamond"), PAGE_W / 2 - 8 * mm, PAGE_H - 28 * mm, 16 * mm, v)
    wordmark(c, v, PAGE_W / 2, PAGE_H - 34 * mm, 12, align="c")
    c.setStrokeColor(v.main)
    c.setLineWidth(1.4)
    c.line(m, PAGE_H - 44 * mm, PAGE_W - m, PAGE_H - 44 * mm)
    c.setLineWidth(0.4)
    c.line(m, PAGE_H - 45.6 * mm, PAGE_W - m, PAGE_H - 45.6 * mm)
    y = PAGE_H - 58 * mm
    flow(c, v.para(f"<font size=6.5 color='#777777'>{v.s['name']} - {v.address(v.s)[0]}</font><br/><br/>"
                   f"{v.b['name']}<br/>" + "<br/>".join(v.address(v.b)), size=10), m, y, 90 * mm)
    flow(c, meta_table(v, 66 * mm, size=8.5), PAGE_W - m - 66 * mm, y, 66 * mm)
    y -= 48 * mm
    text(c, m, y, f"{v.L['title']} {v.sp['invoice_number']}", v.bold, 13, v.ink)
    y -= 7 * mm
    flow(c, v.para(f"{v.L['bill']}: {v.b['name']}, {v.L['vat']} {v.b['vat_number']}"
                   + (f", {v.L['kvk']} {v.b['kvk_number']}" if v.b.get("kvk_number") else ""), size=8.5), m, y, PAGE_W - 2 * m)
    y -= 9 * mm
    y -= flow(c, lines_table(v, PAGE_W - 2 * m, size=9, rules=True), m, y, PAGE_W - 2 * m) + 6 * mm
    flow(c, vat_table(v, 80 * mm), m, y, 80 * mm)
    y -= flow(c, totals_table(v, 70 * mm), PAGE_W - m - 70 * mm, y, 70 * mm) + 8 * mm
    if (rc := v.reverse_charge_text()):
        y -= flow(c, v.para(rc, size=8.5), m, y, PAGE_W - 2 * m) + 3 * mm
    y -= flow(c, v.para("Thank you for your order. " + v.pay_line() + " The terms and a remittance slip are on page 2.",
                        size=9), m, y, PAGE_W - 2 * m) + 3 * mm
    visible_notes(c, v, m, y, PAGE_W - 2 * m)
    text(c, PAGE_W / 2, 12 * mm, footer_line(v)[:125], v.reg, 6.5, HexColor("#777777"), "c")
    text(c, PAGE_W - m, 8 * mm, "Page 1 of 2", v.reg, 7, HexColor("#777777"), "r")
    c.showPage()
    text(c, m, PAGE_H - 28 * mm, "General terms and conditions of sale", v.bold, 12, v.main)
    terms = ("1. Payment is due within thirty days of the invoice date unless agreed otherwise in writing. "
             "2. Interest on late payment is charged at the statutory commercial rate from the due date. "
             "3. Goods remain the property of the supplier until paid in full. 4. Complaints must be made within "
             "eight days of delivery. 5. All disputes are governed by Dutch law. 6. Where VAT is reverse charged, "
             "the customer is responsible for accounting for it. ") * 2
    flow(c, v.para(terms, size=8, color=HexColor("#444444"), leading=11), m, PAGE_H - 36 * mm, PAGE_W - 2 * m)
    y = 112 * mm
    c.setDash(3, 3)
    c.setStrokeColor(HexColor("#888888"))
    c.line(m, y + 8 * mm, PAGE_W - m, y + 8 * mm)
    c.setDash()
    text(c, m, y, "Remittance slip", v.bold, 11, v.ink)
    flow(c, v.para(f"{v.L['no']}: <b>{v.sp['invoice_number']}</b><br/>{v.L['bill']}: {v.b['name']}<br/>"
                   f"{v.L['gross']}: <b>{v.money(v.sp['total_incl_vat'])}</b><br/>{v.L['due']}: {v.day(v.sp['due_date'])}<br/>"
                   f"{v.L['iban']}: <b>{v.s['iban']}</b>", size=10, leading=15), m, y - 5 * mm, PAGE_W - 2 * m)
    text(c, PAGE_W - m, 8 * mm, "Page 2 of 2", v.reg, 7, HexColor("#777777"), "r")


def layout_landscape(c, v: View):
    """Landscape: the parties and totals sit in a column beside a wide table."""
    W, H = landscape(A4)
    m = 14 * mm
    c.setFillColor(v.tint)
    c.rect(0, 0, 78 * mm, H, fill=1, stroke=0)
    logo(c, v.d.get("logo", "wave"), m, H - 28 * mm, 16 * mm, v)
    wordmark(c, v, m, H - 36 * mm, 10, tagline=True)
    y = H - 50 * mm
    y -= flow(c, v.para(f"<b>{v.L['from']}</b><br/>" + "<br/>".join([v.s["name"], *v.party(v.s)]), size=8), m, y, 62 * mm) + 5 * mm
    y -= flow(c, v.para(f"<b>{v.L['bill']}</b><br/>" + "<br/>".join([v.b["name"], *v.party(v.b)]), size=8), m, y, 62 * mm) + 5 * mm
    flow(c, meta_table(v, 62 * mm, size=8), m, y, 62 * mm)
    left = 78 * mm + 10 * mm
    width = W - left - m
    text(c, left, H - 24 * mm, v.L["title"].upper(), v.bold, 22, v.main, space=2)
    y = H - 34 * mm
    y -= flow(c, lines_table(v, width, header_bg=v.dark, size=8.6), left, y, width) + 7 * mm
    flow(c, vat_table(v, 76 * mm), left, y, 76 * mm)
    y -= flow(c, totals_table(v, 70 * mm, fill=v.main), left + width - 70 * mm, y, 70 * mm) + 7 * mm
    if (rc := v.reverse_charge_text()):
        y -= flow(c, v.para(f"<b>{rc}</b>", size=8), left, y, width) + 3 * mm
    y -= flow(c, v.para(v.pay_line(), size=8.5), left, y, width) + 3 * mm
    visible_notes(c, v, left, y, width)
    text(c, left, 8 * mm, footer_line(v)[:150], v.reg, 6.5, HexColor("#777777"))


def layout_compact(c, v: View):
    """Dense: the summary box comes first, the table after it, everything small."""
    m = 12 * mm
    w = PAGE_W - 2 * m
    c.setFillColor(v.dark)
    c.rect(m, PAGE_H - 24 * mm, w, 14 * mm, fill=1, stroke=0)
    logo(c, v.d.get("logo", "bars"), m + 3 * mm, PAGE_H - 22 * mm, 10 * mm, v, on_dark=True)
    text(c, m + 16 * mm, PAGE_H - 18 * mm, v.s["name"], v.bold, 12, white)
    text(c, m + w - 3 * mm, PAGE_H - 18 * mm, f"{v.L['title']}  {v.sp['invoice_number']}", v.bold, 12, white, "r")
    y = PAGE_H - 30 * mm
    box = (w - 4 * mm) / 3
    panels = [(v.L["from"], [v.s["name"], *v.party(v.s, with_iban=True)]),
              (v.L["bill"], [v.b["name"], *v.party(v.b)]),
              ("Summary", [f"{a}: {b}" for a, b in v.meta()])]
    tallest = 0
    for i, (head, rows) in enumerate(panels):
        x = m + i * (box + 2 * mm)
        c.setStrokeColor(v.main)
        c.setLineWidth(0.7)
        c.rect(x, y - 36 * mm, box, 36 * mm)
        c.setFillColor(v.tint)
        c.rect(x, y - 6 * mm, box, 6 * mm, fill=1, stroke=0)
        text(c, x + 2 * mm, y - 4.2 * mm, head.upper(), v.bold, 7, v.main)
        flow(c, v.para("<br/>".join(rows), size=7.8, leading=10), x + 2 * mm, y - 8 * mm, box - 4 * mm)
        tallest = max(tallest, 36)
    y -= tallest * mm + 4 * mm
    c.setFillColor(v.tint)
    c.rect(m, y - 10 * mm, w, 10 * mm, fill=1, stroke=0)
    text(c, m + 3 * mm, y - 6.6 * mm, v.L["gross"].upper(), v.bold, 9, v.main)
    text(c, m + w - 3 * mm, y - 6.6 * mm, v.money(v.sp["total_incl_vat"]), v.bold, 13, v.ink, "r")
    y -= 14 * mm
    y -= flow(c, lines_table(v, w, size=8, header_bg=v.main, zebra=True, rules=False), m, y, w) + 5 * mm
    flow(c, vat_table(v, 84 * mm, size=8), m, y, 84 * mm)
    y -= flow(c, totals_table(v, 70 * mm, size=9), m + w - 70 * mm, y, 70 * mm) + 6 * mm
    if (rc := v.reverse_charge_text()):
        y -= flow(c, v.para(f"<b>{rc}</b>", size=8), m, y, w) + 2 * mm
    y -= flow(c, v.para(v.pay_line(), size=8), m, y, w) + 2 * mm
    visible_notes(c, v, m, y, w, size=7.5)


def layout_typewriter(c, v: View):
    """Monospaced and plain, like a printout from an old accounting system."""
    m = 16 * mm
    y = PAGE_H - 22 * mm
    rule = "=" * 78
    c.setFont("Courier-Bold", 9)
    c.setFillColor(v.ink)
    c.drawString(m, y, rule)
    y -= 5 * mm
    text(c, m, y, v.s["name"].upper(), "Courier-Bold", 13, v.ink)
    logo(c, v.d.get("logo", "chevrons"), PAGE_W - m - 11 * mm, y - 3 * mm, 11 * mm, v)
    y -= 5 * mm
    for row in v.party(v.s, with_iban=True):
        text(c, m, y, row, "Courier", 8.5, v.ink)
        y -= 4 * mm
    c.setFont("Courier-Bold", 9)
    c.drawString(m, y, rule)
    y -= 8 * mm
    text(c, m, y, v.L["title"].upper(), "Courier-Bold", 16, v.ink, space=2)
    y -= 7 * mm
    for label, value in v.meta():
        text(c, m, y, f"{label:<18}: {value}", "Courier", 9, v.ink)
        y -= 4.2 * mm
    y -= 3 * mm
    text(c, m, y, f"{v.L['bill'].upper()}:", "Courier-Bold", 9, v.ink)
    y -= 4.2 * mm
    for row in [v.b["name"], *v.party(v.b)]:
        text(c, m, y, row, "Courier", 9, v.ink)
        y -= 4.2 * mm
    y -= 4 * mm
    y -= flow(c, lines_table(v, PAGE_W - 2 * m, size=8.5, grid=True), m, y, PAGE_W - 2 * m) + 6 * mm
    for label, value in v.totals():
        text(c, m + 96 * mm, y, label, "Courier", 9, v.ink)
        text(c, PAGE_W - m, y, value, "Courier-Bold" if label == v.L["gross"] else "Courier", 9, v.ink, "r")
        y -= 4.6 * mm
    y -= 3 * mm
    text(c, m, y, "VAT SPECIFICATION", "Courier-Bold", 8.5, v.ink)
    y -= 4.4 * mm
    for item in v.sp["vat_breakdown"]:
        text(c, m, y, f"{v.rate(item['rate']):>6}  base {v.money(item['base']):>16}  vat {v.money(item['vat']):>14}",
             "Courier", 8.5, v.ink)
        y -= 4.2 * mm
    y -= 3 * mm
    if (rc := v.reverse_charge_text()):
        y -= flow(c, v.para(rc, size=8.5), m, y, PAGE_W - 2 * m) + 3 * mm
    y -= flow(c, v.para(v.pay_line(), size=8.5), m, y, PAGE_W - 2 * m) + 3 * mm
    visible_notes(c, v, m, y, PAGE_W - 2 * m)
    c.setFont("Courier-Bold", 9)
    c.setFillColor(v.ink)
    c.drawString(m, 14 * mm, rule)


def layout_receipt(c, v: View, width, height):
    """A narrow till-roll style invoice."""
    cx = width / 2
    m = 6 * mm
    y = height - 12 * mm
    logo(c, v.d.get("logo", "circle"), cx - 7 * mm, y - 14 * mm, 14 * mm, v)
    y -= 20 * mm
    text(c, cx, y, v.s["name"], "Courier-Bold", 9.5, v.ink, "c")
    y -= 4.2 * mm
    for row in v.party(v.s):
        text(c, cx, y, row, "Courier", 7.2, v.ink, "c")
        y -= 3.4 * mm
    y -= 2 * mm

    def dashes():
        nonlocal y
        text(c, m, y, "-" * 38, "Courier", 7.2, v.ink)
        y -= 3.6 * mm

    dashes()
    text(c, cx, y, v.L["title"].upper(), "Courier-Bold", 11, v.ink, "c")
    y -= 5 * mm
    for label, value in v.meta():
        text(c, m, y, f"{label[:16]:<16} {value}", "Courier", 7.2, v.ink)
        y -= 3.4 * mm
    y -= 1 * mm
    for row in [v.L["bill"] + ":", v.b["name"], *v.party(v.b)]:
        for part in textwrap.wrap(row, 38):
            text(c, m, y, part, "Courier", 7.2, v.ink)
            y -= 3.4 * mm
    dashes()
    for line in v.sp["lines"]:
        for part in textwrap.wrap(line["description"], 38):
            text(c, m, y, part, "Courier-Bold", 7.2, v.ink)
            y -= 3.4 * mm
        sub = f"{v.qty(line['quantity'])} x {v.money(line['unit_price'])} @{v.rate(line['vat_rate'])}"
        text(c, m, y, sub, "Courier", 7.2, v.ink)
        text(c, width - m, y, v.money(line["line_total"]), "Courier", 7.2, v.ink, "r")
        y -= 4.2 * mm
    dashes()
    for label, value in v.totals():
        bold = label == v.L["gross"]
        text(c, m, y, label, "Courier-Bold" if bold else "Courier", 8 if bold else 7.2, v.ink)
        text(c, width - m, y, value, "Courier-Bold" if bold else "Courier", 8 if bold else 7.2, v.ink, "r")
        y -= 4 * mm
    dashes()
    for item in v.sp["vat_breakdown"]:
        text(c, m, y, f"VAT {v.rate(item['rate'])} on {v.money(item['base'])}", "Courier", 7, v.ink)
        text(c, width - m, y, v.money(item["vat"]), "Courier", 7, v.ink, "r")
        y -= 3.4 * mm
    y -= 2 * mm
    if (rc := v.reverse_charge_text()):
        for part in textwrap.wrap(rc, 40):
            text(c, m, y, part, "Courier", 6.6, v.ink)
            y -= 3.1 * mm
        y -= 1.5 * mm
    for part in textwrap.wrap(v.pay_line(), 40):
        text(c, m, y, part, "Courier", 6.8, v.ink)
        y -= 3.2 * mm
    if v.sp.get("notes") and not v.d.get("hide_notes"):
        for part in textwrap.wrap("Remarks: " + v.sp["notes"], 40):
            text(c, m, y, part, "Courier", 6.4, v.ink)
            y -= 3 * mm
    y -= 3 * mm
    for i in range(46):  # a barcode-like strip
        c.setFillColor(v.ink)
        c.rect(m + i * (width - 2 * m) / 46, y - 9 * mm, ((i * 7) % 3 + 1) * 0.5, 9 * mm, fill=1, stroke=0)


def receipt_height(v: View) -> float:
    extra = 4 * len(v.sp["lines"]) + (14 if v.sp.get("reverse_charge") else 0) + (12 if v.sp.get("notes") else 0)
    return (162 + extra) * mm


def layout_stamp(c, v: View):
    """Classic bones with a corner ribbon and a rubber stamp, like an invoice that has passed through a few hands."""
    layout_classic(c, v)
    c.saveState()
    c.setFillColor(v.dark)
    path = c.beginPath()
    path.moveTo(0, PAGE_H - 28 * mm)
    path.lineTo(0, PAGE_H - 7 * mm)
    path.lineTo(28 * mm, PAGE_H - 7 * mm)
    path.close()
    c.drawPath(path, fill=1, stroke=0)
    c.restoreState()
    c.saveState()
    c.translate(PAGE_W - 58 * mm, 52 * mm)
    c.rotate(-14)
    c.setStrokeColor(HexColor("#B5302B"))
    c.setFillColor(HexColor("#B5302B"))
    c.setFillAlpha(0.8)
    c.setLineWidth(2)
    c.roundRect(-24 * mm, -7 * mm, 48 * mm, 14 * mm, 2 * mm, fill=0, stroke=1)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(0, -2 * mm, "RECEIVED")
    c.restoreState()


LAYOUTS = {"classic": layout_classic, "sidebar": layout_sidebar, "banner": layout_banner, "minimal": layout_minimal,
           "ledger": layout_ledger, "letter": layout_letter, "landscape": layout_landscape,
           "compact": layout_compact, "typewriter": layout_typewriter, "stamp": layout_stamp,
           "receipt": layout_receipt}


# --------------------------------------------------------------------------- degrading the page like the real world


def solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Gaussian elimination for the 8 unknowns of a perspective transform."""
    n = len(vector)
    rows = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(n):
            if r != col:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col])]
    return [rows[i][n] / rows[i][i] for i in range(n)]


def perspective(image: Image.Image, corners: list[tuple[float, float]], size: tuple[int, int]) -> Image.Image:
    """Warp `image` so its corners land on `corners` in an output of `size` (as a phone would see a page)."""
    w, h = image.size
    source = [(0, 0), (w, 0), (w, h), (0, h)]
    matrix, vector = [], []
    for (x, y), (u, v_) in zip(corners, source):  # output (x, y) -> input (u, v)
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        vector.append(u)
        matrix.append([0, 0, 0, x, y, 1, -v_ * x, -v_ * y])
        vector.append(v_)
    return image.transform(size, Image.PERSPECTIVE, solve(matrix, vector), Image.BICUBIC, fillcolor=(0, 0, 0))


def raster(pdf_path: Path, dpi: int = 150) -> Image.Image:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return pdf[0].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        pdf.close()


def effect_scan(image: Image.Image, rng: random.Random, heavy: bool = False) -> Image.Image:
    gray = image.convert("L")
    angle = rng.choice([-1, 1]) * rng.uniform(1.4, 2.8) if heavy else rng.choice([-1, 1]) * rng.uniform(0.6, 1.8)
    gray = gray.rotate(angle, expand=True, fillcolor=236, resample=Image.BICUBIC)
    gray = gray.filter(ImageFilter.GaussianBlur(0.9 if heavy else 0.6))
    gray = ImageEnhance.Contrast(gray).enhance(0.82 if heavy else 0.92)
    noise = Image.effect_noise(gray.size, 34 if heavy else 26)
    gray = Image.blend(gray, noise, 0.12 if heavy else 0.08)
    if heavy:  # a dark edge and a toner streak, as from a worn scanner
        draw = ImageDraw.Draw(gray)
        draw.rectangle((0, 0, 9, gray.height), fill=90)
        x = rng.randrange(gray.width // 4, gray.width * 3 // 4)
        draw.line((x, 0, x + 6, gray.height), fill=205, width=2)
    return gray


def effect_photo(image: Image.Image, rng: random.Random) -> Image.Image:
    """The page on a desk, shot at an angle with uneven light."""
    w, h = image.size
    pad_x, pad_y = int(w * 0.14), int(h * 0.1)
    size = (w + 2 * pad_x, h + 2 * pad_y)
    jitter = lambda: rng.uniform(-0.035, 0.035)
    corners = [(pad_x * (1 + 0.6 * jitter() * 8), pad_y * (1 + jitter() * 6)),
               (w + pad_x * (1 + jitter() * 4), pad_y * (1 + jitter() * 8)),
               (w + pad_x * (1 + jitter() * 3), h + pad_y * (1 + jitter() * 4)),
               (pad_x * (1 + jitter() * 8), h + pad_y * (1 + jitter() * 6))]
    shot = perspective(image, corners, size)
    desk = Image.new("RGB", size, (92, 74, 58))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(corners, fill=255)
    shot = Image.composite(shot, desk, mask)
    light = Image.linear_gradient("L").resize(size).rotate(rng.choice([0, 90, 180, 270]) + rng.uniform(-30, 30))
    shot = Image.composite(shot, ImageEnhance.Brightness(shot).enhance(0.62), light)
    shot = shot.filter(ImageFilter.GaussianBlur(0.8))
    return ImageEnhance.Color(shot).enhance(0.9)


def effect_fax(image: Image.Image, rng: random.Random) -> Image.Image:
    gray = image.convert("L").resize((image.width * 2 // 3, image.height * 2 // 3), Image.LANCZOS)
    gray = gray.rotate(rng.uniform(-0.9, 0.9), expand=True, fillcolor=255, resample=Image.BICUBIC)
    gray = ImageEnhance.Contrast(gray).enhance(1.4)
    bits = gray.point(lambda p: 255 if p > 150 else 0)
    draw = ImageDraw.Draw(bits)
    for _ in range(7):  # horizontal drop-outs
        y = rng.randrange(bits.height)
        draw.line((0, y, bits.width, y), fill=255, width=rng.choice([1, 1, 2]))
    return bits.convert("L")


# --------------------------------------------------------------------------- the entry point


def draw_pdf(spec: dict, design: dict, path: Path) -> None:
    v = View(spec, design)
    layout = design["layout"]
    if layout == "receipt":
        width, height = 80 * mm, receipt_height(v)
        c = canvas.Canvas(str(path), pagesize=(width, height))
        c.setTitle(f"Invoice {spec['invoice_number']}")
        layout_receipt(c, v, width, height)
    else:
        size = landscape(A4) if layout == "landscape" else A4
        c = canvas.Canvas(str(path), pagesize=size)
        c.setTitle(f"Invoice {spec['invoice_number']}")
        watermark(c, v, *size)
        LAYOUTS[layout](c, v)
        hidden_note(c, v)
    c.showPage()
    c.save()


def render(spec: dict, design: dict, path: Path, seed: int = 0) -> Path:
    """Draw `spec` in `design` and write it to `path` (.pdf, .png or .jpg, as design["out"] says)."""
    rng = random.Random(seed)
    out = design.get("out", "pdf")
    clean = path.with_suffix(".tmp.pdf")
    draw_pdf(spec, design, clean)
    try:
        if out == "pdf":
            clean.replace(path)
            return path
        dpi = 110 if design.get("layout") == "receipt" else 150
        page = raster(clean, dpi)
        if out == "png":  # a clean screenshot
            page.save(path, "PNG", optimize=True)
        elif out == "scan":  # an image-only PDF
            effect_scan(page, rng).save(path, "PDF", resolution=dpi)
        elif out == "scan_heavy":
            effect_scan(page, rng, heavy=True).save(path, "PDF", resolution=dpi)
        elif out == "fax":
            effect_fax(page, rng).save(path, "PDF", resolution=100)
        elif out == "photo":
            effect_photo(page, rng).save(path, "JPEG", quality=72)
        elif out == "jpg_scan":
            effect_scan(page, rng).convert("RGB").save(path, "JPEG", quality=80)
        else:
            raise ValueError(f"unknown output kind {out!r}")
        return path
    finally:
        clean.unlink(missing_ok=True)
