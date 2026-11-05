"""Make 30 sample invoices in samples/, each designed differently, for stress testing by hand.

    python scripts/make_samples.py

Real invoices do not look alike, so these do not either: eleven document structures (a classic letter, a
sidebar, a banner, a till-roll receipt, a landscape sheet, a ruled form, a two-page letter with a remittance
slip, a dense page that puts the total first, a typewriter printout ...), each with its own drawn logo, colours,
wording, number format, date format and set of columns. Some are clean PDFs, some are scans, a fax, a phone
photo or a screenshot. One has its instructions hidden in white text.

The first seven show the same data as seven of the 20 test invoices in the inbox, in new designs. The other
23 are new invoices with their own purchase orders, receipts and payments in the database, so run
`python data/generate.py` once if your database is older than this script. None of them is in the inbox:
upload them in the interface and verify them live. `samples/answer-key.md` lists what each one should give.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))

import render_samples

from servers.common.config import INVOICES_DIR

SAMPLES_DIR = ROOT / "samples"

OUT_EXT = {"pdf": "pdf", "scan": "pdf", "scan_heavy": "pdf", "fax": "pdf", "png": "png", "photo": "jpg",
           "jpg_scan": "jpg"}
OUT_NOTE = {"pdf": "PDF with text", "scan": "scanned PDF", "scan_heavy": "poor scan (PDF)", "fax": "fax (PDF)",
            "png": "PNG screenshot", "photo": "phone photo (JPG)", "jpg_scan": "scanned JPG"}
WORDMARKS = ["bold", "caps", "serif", "light"]
ALL = ["sku", "desc", "qty", "unit", "rate", "amount"]
NO_SKU = ["desc", "qty", "unit", "rate", "amount"]
NO_RATE = ["sku", "desc", "qty", "unit", "amount"]
BARE = ["desc", "qty", "unit", "amount"]
DAY = {"iso": "%Y-%m-%d", "short": "%d %b %Y", "slash": "%d/%m/%Y", "dot": "%d.%m.%Y", "long": "%B %d, %Y",
       "dash": "%d-%m-%Y"}

# The first seven reuse the data of existing test invoices (ground-truth case names, and the supplier's trade).
EXISTING = [
    ("clean_books", "clean-domestic-books", "books_print"),
    ("clean_standard", "clean-domestic-scan", "it_services"),
    ("wrong_amount", "wrong-amount", "office"),
    ("reverse_charge_error", "reverse-charge-error", "software"),
    ("changed_bank", "changed-bank-details", "logistics"),
    ("already_paid", "already-paid", "packaging"),
    ("prompt_injection", "prompt-injection", "furniture"),
]

# One design per sample, in order: 1-7 are the existing cases, 8-30 the new ones (see SAMPLE_CASES in data/generate.py).
DESIGNS = [
    {"layout": "minimal", "palette": "slate", "logo": "ring", "labels": "b", "money": "sym", "date": "short", "font": "serif", "out": "pdf"},
    {"layout": "classic", "palette": "navy", "logo": "circle", "labels": "a", "money": "en", "date": "iso", "cols": NO_SKU, "out": "scan"},
    {"layout": "banner", "palette": "amber", "logo": "hexagon", "labels": "c", "money": "eu", "date": "slash", "out": "pdf"},
    {"layout": "sidebar", "palette": "teal", "logo": "rounded", "labels": "d", "money": "suffix", "date": "dot", "out": "pdf"},
    {"layout": "ledger", "palette": "crimson", "logo": "square", "labels": "b", "money": "en", "date": "dash", "cols": NO_RATE, "out": "png"},
    {"layout": "letter", "palette": "forest", "logo": "diamond", "labels": "a", "money": "sym", "date": "long", "out": "pdf"},
    {"layout": "stamp", "palette": "violet", "logo": "stripes", "labels": "c", "money": "en", "date": "iso", "out": "pdf"},
    {"layout": "compact", "palette": "indigo", "logo": "bars", "labels": "d", "money": "en", "date": "short", "out": "pdf"},
    {"layout": "landscape", "palette": "olive", "logo": "leaf", "labels": "b", "money": "eu", "date": "dot", "cols": NO_SKU, "out": "pdf"},
    {"layout": "receipt", "palette": "sea", "logo": "triangle", "labels": "c", "money": "sym", "date": "slash", "font": "mono", "out": "pdf"},
    {"layout": "classic", "palette": "plum", "logo": "wave", "labels": "a", "money": "suffix", "date": "slash", "out": "photo"},
    {"layout": "sidebar", "palette": "grass", "logo": "cross", "labels": "d", "money": "sym", "date": "long", "cols": NO_RATE, "out": "pdf"},
    {"layout": "typewriter", "palette": "ink", "logo": "chevrons", "labels": "a", "money": "plain", "date": "iso", "font": "mono",
         "out": "scan_heavy"},
    {"layout": "banner", "palette": "copper", "logo": "diamond", "labels": "b", "money": "en", "date": "short", "font": "serif", "out": "pdf"},
    {"layout": "ledger", "palette": "rose", "logo": "hexagon", "labels": "c", "money": "eu", "date": "dash", "out": "pdf"},
    {"layout": "minimal", "palette": "forest", "logo": "leaf", "labels": "d", "money": "suffix", "date": "slash", "cols": NO_SKU, "out": "scan"},
    {"layout": "letter", "palette": "sea", "logo": "square", "labels": "b", "money": "sym", "date": "iso", "out": "pdf"},
    {"layout": "stamp", "palette": "navy", "logo": "stripes", "labels": "a", "money": "en", "date": "short", "out": "fax"},
    {"layout": "compact", "palette": "rose", "logo": "circle", "labels": "c", "money": "plain", "date": "dot", "cols": BARE, "out": "pdf"},
    {"layout": "classic", "palette": "copper", "logo": "bars", "labels": "d", "money": "en", "date": "short", "watermark": "COPY",
         "out": "png"},
    {"layout": "sidebar", "palette": "crimson", "logo": "wave", "labels": "a", "money": "eu", "date": "slash", "out": "photo"},
    {"layout": "landscape", "palette": "violet", "logo": "ring", "labels": "b", "money": "suffix", "date": "long", "cols": NO_RATE,
         "out": "scan"},
    {"layout": "receipt", "palette": "amber", "logo": "cross", "labels": "a", "money": "en", "date": "iso", "font": "mono", "out": "pdf"},
    {"layout": "banner", "palette": "slate", "logo": "chevrons", "labels": "d", "money": "sym", "date": "dash", "out": "pdf"},
    {"layout": "typewriter", "palette": "olive", "logo": "triangle", "labels": "c", "money": "en", "date": "short", "font": "mono",
         "out": "pdf"},
    {"layout": "ledger", "palette": "teal", "logo": "diamond", "labels": "a", "money": "eu", "date": "dot", "cols": NO_SKU, "out": "png"},
    {"layout": "minimal", "palette": "indigo", "logo": "hexagon", "labels": "b", "money": "en", "date": "slash", "watermark": "ORIGINAL",
         "out": "pdf"},
    {"layout": "classic", "palette": "grass", "logo": "stripes", "labels": "d", "money": "sym", "date": "iso", "hide_notes": True,
         "out": "pdf"},
    {"layout": "compact", "palette": "plum", "logo": "leaf", "labels": "c", "money": "suffix", "date": "short", "cols": NO_RATE,
         "out": "scan"},
    {"layout": "sidebar", "palette": "ink", "logo": "square", "labels": "a", "money": "en", "date": "long", "out": "jpg_scan"},
]


def load_cases() -> list[dict]:
    truth = {g["case"]: g for g in json.loads((INVOICES_DIR / "ground_truth.json").read_text(encoding="utf-8"))}
    cases = []
    for case, slug, archetype in EXISTING:
        entry = truth[case]
        cases.append({"slug": slug, "archetype": archetype, "spec": entry["expected_invoice"],
                      "verdict": entry["expected_verdict"], "error": entry["error_type"],
                      "description": entry["description"]})
    path = INVOICES_DIR / "sample_ground_truth.json"
    if not path.exists():
        raise SystemExit("Run `python data/generate.py` first: it also writes the 23 new sample invoices.")
    for entry in json.loads(path.read_text(encoding="utf-8")):
        cases.append({"slug": entry["slug"], "archetype": entry["archetype"], "spec": entry["expected_invoice"],
                      "verdict": entry["expected_verdict"], "error": entry["error_type"],
                      "description": entry["description"]})
    return cases


def main() -> None:
    cases = load_cases()
    assert len(cases) == len(DESIGNS) == 30, (len(cases), len(DESIGNS))
    SAMPLES_DIR.mkdir(exist_ok=True)
    for old in list(SAMPLES_DIR.glob("sample-*")) + list(SAMPLES_DIR.glob("answer-key.md")):
        old.unlink()
    key = ["# Answer key for the sample invoices", "",
           "| # | File | Looks like | Expected verdict | Planted problem |", "|---|---|---|---|---|"]
    for n, (case, design) in enumerate(zip(cases, DESIGNS, strict=True), start=1):
        design = {**design, "archetype": case["archetype"], "wordmark": WORDMARKS[n % 4], "pay": n, "rc": n}
        design["date"] = DAY[design["date"]]
        name = f"sample-{n:02d}-{case['slug']}.{OUT_EXT[design['out']]}"
        render_samples.render(case["spec"], design, SAMPLES_DIR / name, seed=n)
        look = f"{design['layout']}, {design['palette']}, {OUT_NOTE[design['out']]}"
        key.append(f"| {n} | `{name}` | {look} | {case['verdict']} | {case['error']}: {case['description']} |")
        print(f"{name:52} {look:46} expect {case['verdict']}")
    (SAMPLES_DIR / "answer-key.md").write_text("\n".join(key) + "\n", encoding="utf-8")
    print(f"\n{len(cases)} samples and answer-key.md in {SAMPLES_DIR}")


if __name__ == "__main__":
    main()
