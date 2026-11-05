"""Evidence packs: one PDF or XLSX per case with the invoice, the extracted fields, the checks that
ran, every tool call from the audit log, the verdict and the case notes."""

from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Font
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from servers.common import audit
from servers.common.config import OUTBOX_DIR

STYLES = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=STYLES["Normal"], fontSize=8.5, leading=11)
H2 = ParagraphStyle("h2", parent=STYLES["Heading2"], fontSize=12, spaceBefore=8, spaceAfter=3)


def _sections(case: dict) -> dict:
    """The tables of the pack as plain rows, shared by the PDF and the spreadsheet."""
    evidence = case["evidence"]
    verdict, invoice = evidence.get("verdict") or {}, evidence.get("invoice") or {}
    calls = audit.read_entries(invoice_id=case["invoice_id"])
    supplier, buyer = invoice.get("supplier") or {}, invoice.get("buyer") or {}
    return {
        "summary": [["Case", case["case_id"]], ["Invoice", case["invoice_id"]], ["Verdict", case["verdict"]],
                    ["Status", case["status"]], ["Confidence", verdict.get("confidence")],
                    ["Created", case["created_at"]], ["Reasoning", verdict.get("reasoning") or ""],
                    ["Needs review because", "; ".join(verdict.get("needs_review_reasons") or []) or "-"],
                    ["Risk flags", "; ".join(verdict.get("risk_flags") or []) or "-"]],
        "discrepancies": [["Type", "Severity", "Field", "Expected", "Found", "Detail"]] + [
            [d["type"], d["severity"], d.get("field") or "", d.get("expected") or "", d.get("found") or "",
             d.get("detail") or ""] for d in verdict.get("discrepancies") or []],
        "checks": [["Check", "Server", "Status", "Detail"]] + [
            [c["name"], c["server"], c["status"], c.get("detail") or ""] for c in verdict.get("checks") or []],
        "invoice": [["Field", "Value"],
                    ["Invoice number", invoice.get("invoice_number")], ["Invoice date", invoice.get("invoice_date")],
                    ["Supply date", invoice.get("supply_date")], ["PO number", invoice.get("po_number")],
                    ["Supplier", supplier.get("name")], ["Supplier VAT", supplier.get("vat_number")],
                    ["Supplier KvK", supplier.get("kvk_number")], ["Supplier IBAN", supplier.get("iban")],
                    ["Buyer", buyer.get("name")], ["Total excl. VAT", invoice.get("total_excl_vat")],
                    ["Total VAT", invoice.get("total_vat")], ["Total incl. VAT", invoice.get("total_incl_vat")],
                    ["Reverse charge", invoice.get("reverse_charge")]],
        "lines": [["Item", "Description", "Qty", "Unit price", "VAT %", "Line total"]] + [
            [line.get("sku"), line.get("description"), line.get("quantity"), line.get("unit_price"),
             line.get("vat_rate"), line.get("line_total")] for line in invoice.get("lines") or []],
        "tool_calls": [["Time", "Role", "Server", "Tool", "Arguments", "Rows", "Latency ms", "Refused"]] + [
            [c["ts"], c["role"], c["server"] or "", c["tool"], str(c["args"]), c["rows_returned"], c["latency_ms"],
             "yes" if c["refused"] else ""] for c in calls],
        "notes": [["Time", "Author", "Note"]] + [[n["ts"], n["author"], n["text"]] for n in case["notes"]],
    }


def _para(value) -> Paragraph:
    return Paragraph(escape("" if value is None else str(value)), BODY)


def _table(rows: list[list], widths: list[float]) -> Table:
    table = Table([[_para(c) for c in row] for row in rows], colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return table


def _write_pdf(case: dict, path: Path) -> None:
    sections = _sections(case)
    width = (A4[0] - 30 * mm)
    story = [Paragraph(f"Evidence pack {escape(case['case_id'])}", STYLES["Title"])]
    plan = [("Summary", "summary", [0.25, 0.75]), ("Discrepancies", "discrepancies", [0.22, 0.09, 0.14, 0.14, 0.14, 0.27]),
            ("Checks run", "checks", [0.2, 0.14, 0.09, 0.57]), ("Extracted invoice", "invoice", [0.3, 0.7]),
            ("Invoice lines", "lines", [0.11, 0.37, 0.08, 0.15, 0.09, 0.2]),
            ("Tool calls (audit log)", "tool_calls", [0.15, 0.09, 0.11, 0.15, 0.3, 0.06, 0.08, 0.06]),
            ("Case notes", "notes", [0.17, 0.13, 0.7])]
    for title, key, fractions in plan:
        rows = sections[key]
        if key == "summary":
            rows = [["", ""], *rows][1:]  # no header row to style
        if key in ("discrepancies", "notes") and len(rows) == 1:
            story += [Paragraph(title, H2), Paragraph("None.", BODY)]
            continue
        story += [Paragraph(title, H2), _table(rows, [f * width for f in fractions]), Spacer(1, 3 * mm)]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                      bottomMargin=14 * mm, title=f"Evidence pack {case['case_id']}").build(story)


def _write_xlsx(case: dict, path: Path) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, key in (("Summary", "summary"), ("Discrepancies", "discrepancies"), ("Checks", "checks"),
                       ("Invoice", "invoice"), ("Lines", "lines"), ("Tool calls", "tool_calls"), ("Notes", "notes")):
        sheet = workbook.create_sheet(title)
        for row in _sections(case)[key]:
            sheet.append([("" if v is None else v if isinstance(v, int | float | str) else str(v)) for v in row])
        if key != "summary" and sheet.max_row:
            for cell in sheet[1]:
                cell.font = Font(bold=True)
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(
                60, max(10, *(len(str(c.value or "")) + 2 for c in column)))
    workbook.save(path)


def export(case: dict, fmt: str) -> Path:
    if fmt not in ("pdf", "xlsx"):
        raise ValueError("fmt must be 'pdf' or 'xlsx'")
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTBOX_DIR / f"{case['case_id']}.{fmt}"
    (_write_pdf if fmt == "pdf" else _write_xlsx)(case, path)
    return path
