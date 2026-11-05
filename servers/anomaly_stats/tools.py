"""Aggregate statistics only: scores and counts, never rows. Groups smaller than 5 are suppressed,
so no number can reveal a single transaction. The agent learns what is unusual without ever
seeing the payment history."""

import statistics
from datetime import date, timedelta

import holidays

from servers.common.config import AS_OF
from servers.common.db import read_only
from servers.common.limits import MIN_GROUP_SIZE

BAND_BELOW_LIMIT = 0.10  # "just under" the approval limit means within 10% below it


def _require_vendor(con, vendor_id: str) -> None:
    if con.execute("SELECT 1 FROM vendors WHERE id = ?", (vendor_id,)).fetchone() is None:
        raise ValueError(f"unknown vendor_id {vendor_id!r}")


def vendor_amount_profile(vendor_id: str, amount: float) -> dict:
    """How unusual is an amount (including VAT) for this vendor: typical range, median, z-score, percentile."""
    with read_only() as con:
        _require_vendor(con, vendor_id)
        amounts = sorted(r[0] for r in con.execute("SELECT amount_eur FROM transactions WHERE vendor_id = ?",
                                                   (vendor_id,)))
    n = len(amounts)
    if n < MIN_GROUP_SIZE:
        return {"vendor_id": vendor_id, "amount": amount, "history_count": n, "suppressed": True,
                "note": f"fewer than {MIN_GROUP_SIZE} past payments: no statistics are given"}
    mean, spread = statistics.fmean(amounts), statistics.pstdev(amounts)
    return {
        "vendor_id": vendor_id, "amount": amount, "history_count": n, "suppressed": False,
        "typical_low": round(amounts[int(0.10 * (n - 1))], 2), "typical_high": round(amounts[int(0.90 * (n - 1))], 2),
        "median": round(statistics.median(amounts), 2), "mean": round(mean, 2),
        "z_score": round((amount - mean) / spread, 2) if spread else 0.0,
        "percentile": round(100 * sum(1 for a in amounts if a <= amount) / n, 1),
        "times_median": round(amount / statistics.median(amounts), 2) if statistics.median(amounts) else None,
    }


def split_invoice_check(vendor_id: str, window_days: int, threshold: float, on_date: str | None = None) -> dict:
    """Invoices from this vendor just under an approval limit within a window before a date.

    "Just under" means from 90% of the limit up to the limit. Two or more can mean one purchase
    was split to avoid the limit. Only a count (and a total when there are two or more) is returned.
    """
    if not 1 <= window_days <= 90:
        raise ValueError("window_days must be between 1 and 90")
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    end = date.fromisoformat(on_date) if on_date else AS_OF
    start = end - timedelta(days=window_days)
    low = threshold * (1 - BAND_BELOW_LIMIT)
    with read_only() as con:
        _require_vendor(con, vendor_id)
        amounts = [r[0] for r in con.execute(
            "SELECT amount FROM invoices_seen WHERE vendor_id = ? AND date >= ? AND date <= ? "
            "AND amount >= ? AND amount < ?", (vendor_id, start.isoformat(), end.isoformat(), low, threshold))]
    count = len(amounts)
    return {"vendor_id": vendor_id, "window_days": window_days, "threshold": threshold,
            "band": [round(low, 2), threshold], "count": count,
            "total": round(sum(amounts), 2) if count >= 2 else None,
            "possible_split": count >= 2}


def date_flags(on_date: str) -> dict:
    """Is a date a weekend, a Dutch public holiday, or in the future (after the reference date)?"""
    try:
        day = date.fromisoformat(on_date)
    except ValueError:
        raise ValueError(f"date must be an ISO date (YYYY-MM-DD), got {on_date!r}") from None
    nl = holidays.country_holidays("NL", years=day.year, language="en_US")
    return {"date": day.isoformat(), "weekday": day.strftime("%A"), "weekend": day.weekday() >= 5,
            "public_holiday": nl.get(day), "future": day > AS_OF}
