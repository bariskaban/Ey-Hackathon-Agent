"""Copy a few of the test invoices to samples/ under descriptive names, for uploading by hand.

    python scripts/make_samples.py

The inbox already holds INV-001 to INV-020. Files in samples/ have the same content as some of them but
are not in the inbox, so a person can upload them in the interface and verify them live. An uploaded file
that is not named INV-<number> gets its own document id, so nothing here touches the 20 test invoices.
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from servers.common.config import INVOICES_DIR

SAMPLES_DIR = ROOT / "samples"
NAMES = {  # ground-truth case -> file name
    "clean_books": "sample-clean-text-pdf.pdf",
    "clean_standard": "sample-clean-scan.pdf",
    "wrong_amount": "sample-wrong-amount.pdf",
    "reverse_charge_error": "sample-reverse-charge-error.pdf",
    "changed_bank": "sample-changed-bank-details.pdf",
    "already_paid": "sample-already-paid.pdf",
    "prompt_injection": "sample-prompt-injection.pdf",
}


def main() -> None:
    truth = {g["case"]: g for g in json.loads((INVOICES_DIR / "ground_truth.json").read_text(encoding="utf-8"))}
    SAMPLES_DIR.mkdir(exist_ok=True)
    for case, name in NAMES.items():
        shutil.copy2(INVOICES_DIR / truth[case]["file"], SAMPLES_DIR / name)
        print(f"{name:34} expected {truth[case]['expected_verdict']}")
    print(f"{len(NAMES)} samples in {SAMPLES_DIR}")


if __name__ == "__main__":
    main()
