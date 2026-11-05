"""Copy the 20 test invoices into the inbox and build the manifest.

    python scripts/load_inbox.py

The inbox (data/inbox/) is where documents wait to be verified. The UI's upload does the same
thing for one file at a time.
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from servers.common.config import INVOICES_DIR
from servers.documents import manifest


def main() -> None:
    inbox = manifest.inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    sources = sorted(INVOICES_DIR.glob("INV-*.pdf"))
    if not sources:
        sys.exit("no invoices found; run data/generate.py and data/render_pdfs.py first")
    for source in sources:
        shutil.copy2(source, inbox / source.name)
    result = manifest.sync_inbox()
    scans = sum(1 for d in result["documents"].values() if not d["has_text_layer"])
    print(f"{len(result['documents'])} documents in {inbox} ({scans} without a text layer)")
    for item in result["rejected"]:
        print(f"  rejected {item['filename']}: {item['reason']}")


if __name__ == "__main__":
    main()
