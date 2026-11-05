"""VAT arithmetic for purchase orders, using the vat_rates reference table."""

from datetime import date

CATEGORY_TO_BAND = {
    "standard": "standard",
    "reduced_food": "reduced",
    "reduced_books": "reduced",
    "reduced_medicine": "reduced",
    "zero": "zero",
}


def vat_rate(con, category: str, on: str) -> float:
    band = CATEGORY_TO_BAND[category]
    row = con.execute(
        "SELECT rate FROM vat_rates WHERE category = ? AND valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)",
        (band, on, on)).fetchone()
    if row is None:
        raise ValueError(f"no VAT rate for {category} on {on}")
    return row["rate"]


def po_total_incl_vat(con, vendor_country: str, po_date: str, lines) -> float:
    """Total including VAT. Foreign EU suppliers invoice under reverse charge (0%)."""
    date.fromisoformat(po_date)
    nets_per_rate: dict[float, float] = {}
    for line in lines:
        net = round(line["qty"] * line["unit_price"], 2)
        rate = 0.0 if vendor_country != "NL" else vat_rate(con, line["vat_category"], po_date)
        nets_per_rate[rate] = nets_per_rate.get(rate, 0.0) + net
    # VAT is rounded once per rate, the way invoices state it
    return round(sum(base + round(base * rate / 100 + 1e-9, 2) for rate, base in nets_per_rate.items()), 2)
