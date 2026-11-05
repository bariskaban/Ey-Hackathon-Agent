"""Field-level extraction accuracy on the 20 test invoices.

    python eval/extraction_accuracy.py            # all 20
    python eval/extraction_accuracy.py INV-004    # selected ones
    python eval/extraction_accuracy.py --model qwen3:8b

Needs the data (data/generate.py, data/render_pdfs.py), the inbox (scripts/load_inbox.py) and
Ollama running with the model pulled. Writes data/runs/extraction_accuracy.json.

A field counts as correct when the extracted value equals the value printed on the invoice
(ground truth), ignoring case and spacing; amounts compare to the cent. Fields that are
absent on the invoice must come back as null.
"""

import argparse
import asyncio
import json
import re
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.extract import extract_invoice
from agent.gateway import Gateway
from agent.llm import LLM
from servers.common.config import INVOICES_DIR, RUNS_DIR

SCALARS = ["invoice_number", "invoice_date", "supply_date", "due_date", "po_number", "currency",
           "total_excl_vat", "total_vat", "total_incl_vat", "reverse_charge"]
PARTY_FIELDS = {"supplier": ["name", "address", "vat_number", "kvk_number", "iban"],
                "buyer": ["name", "address", "vat_number", "kvk_number"]}
LINE_FIELDS = ["sku", "description", "quantity", "unit_price", "vat_rate", "line_total"]
VAT_FIELDS = ["rate", "base", "vat"]
NUMERIC = {"total_excl_vat", "total_vat", "total_incl_vat", "quantity", "unit_price", "vat_rate",
           "line_total", "rate", "base", "vat"}


def norm_text(value) -> str | None:
    if value is None:
        return None
    return re.sub(r"[\s]+", "", str(value)).lower()


def same(field: str, expected, got) -> bool:
    if field in NUMERIC:
        if expected is None or got is None:
            return expected is None and got is None
        try:
            return abs(Decimal(str(expected)) - Decimal(str(got))) < Decimal("0.005")
        except InvalidOperation:
            return False
    if field == "description":  # models sometimes prepend the item code
        e, g = norm_text(expected), norm_text(got)
        return e == g or (e is not None and g is not None and g.endswith(e))
    return norm_text(expected) == norm_text(got)


def compare(expected: dict, got: dict) -> dict[str, bool]:
    """Return {field path: correct?} for every field that exists on the invoice."""
    results: dict[str, bool] = {}
    for f in SCALARS:
        results[f] = same(f, expected.get(f), got.get(f))
    for side, fields in PARTY_FIELDS.items():
        for f in fields:
            results[f"{side}.{f}"] = same(f, expected[side].get(f), (got.get(side) or {}).get(f))
    results["notes"] = (expected.get("notes") is None) == (got.get("notes") is None)
    exp_lines, got_lines = expected["lines"], got.get("lines") or []
    results["lines.count"] = len(exp_lines) == len(got_lines)
    for i, line in enumerate(exp_lines):
        other = got_lines[i] if i < len(got_lines) else {}
        for f in LINE_FIELDS:
            results[f"lines[{i}].{f}"] = same(f, line.get(f), other.get(f))
    exp_vat, got_vat = expected["vat_breakdown"], got.get("vat_breakdown") or []
    results["vat_breakdown.count"] = len(exp_vat) == len(got_vat)
    for i, row in enumerate(exp_vat):
        other = got_vat[i] if i < len(got_vat) else {}
        for f in VAT_FIELDS:
            results[f"vat_breakdown[{i}].{f}"] = same(f, row.get(f), other.get(f))
    return results


async def run(selected: list[str], model: str | None) -> dict:
    truth = {g["invoice_id"]: g for g in json.loads((INVOICES_DIR / "ground_truth.json").read_text("utf-8"))}
    ids = selected or sorted(truth)
    llm = LLM(model=model) if model else LLM()
    report = {"model": llm.model, "invoices": {}}
    async with Gateway(server_names=["documents"]) as gateway:
        for doc_id in ids:
            started = time.perf_counter()
            extraction = await extract_invoice(gateway, llm, doc_id)
            seconds = round(time.perf_counter() - started, 1)
            entry = {"scanned": truth[doc_id]["scanned"], "method": extraction.method, "seconds": seconds,
                     "stopped_reason": extraction.stopped_reason, "error": extraction.error,
                     "attempts": extraction.attempts,
                     "low_confidence_fields": extraction.low_confidence_fields}
            if extraction.invoice is not None:
                got = extraction.invoice.model_dump(mode="json")
                fields = compare(truth[doc_id]["expected_invoice"], got)
                wrong = sorted(k for k, ok in fields.items() if not ok)
                entry.update(correct=len(fields) - len(wrong), total=len(fields), wrong=wrong,
                             flagged_wrong=[f for f in wrong if f in extraction.low_confidence_fields])
            report["invoices"][doc_id] = entry
            ok = f"{entry['correct']}/{entry['total']}" if "correct" in entry else "FAILED"
            print(f"{doc_id} {entry['method'] or '-':9} {seconds:5.1f}s  {ok:>7}  wrong: {entry.get('wrong', entry['error'])}")
            sys.stdout.flush()
    return report


def summarise(report: dict) -> None:
    scored = {k: v for k, v in report["invoices"].items() if "correct" in v}
    if not scored:
        print("nothing scored")
        return

    def pct(rows):
        c, t = sum(r["correct"] for r in rows), sum(r["total"] for r in rows)
        return f"{c}/{t} = {100 * c / t:.1f}%" if t else "n/a"

    print("\nfield accuracy, all invoices:   ", pct(scored.values()))
    print("field accuracy, text PDFs:       ", pct([v for v in scored.values() if not v["scanned"]]))
    print("field accuracy, scanned (OCR):   ", pct([v for v in scored.values() if v["scanned"]]))
    perfect = sum(1 for v in scored.values() if v["correct"] == v["total"])
    print(f"invoices with every field right: {perfect}/{len(scored)}")
    flagged = sum(len(v["flagged_wrong"]) for v in scored.values())
    wrong = sum(len(v["wrong"]) for v in scored.values())
    print(f"wrong fields that were flagged low-confidence: {flagged}/{wrong}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("doc_ids", nargs="*")
    parser.add_argument("--model")
    args = parser.parse_args()
    report = asyncio.run(run(args.doc_ids, args.model))
    summarise(report)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / "extraction_accuracy.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
