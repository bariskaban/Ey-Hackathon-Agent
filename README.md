# Vera: invoice verification agent

Vera checks a supplier invoice against a company's own records and the Dutch VAT rules, and gives one of five verdicts with the evidence behind it. It is an AI agent, but the AI never decides alone and never sees the database: the company records sit behind six narrow, read-only [MCP](https://modelcontextprotocol.io) servers, a gateway logs every call, and the verdict comes from fixed rules in code. A person makes every final decision. Everything runs on your own machine, so no financial data leaves it.

The whole workflow is in English: invoices, prompts, tool output and the interface.

## Contents

- [What Vera does](#what-vera-does)
- [How it works](#how-it-works)
- [Privacy and safety model](#privacy-and-safety-model)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Using the interface](#using-the-interface)
- [Hidden pages](#hidden-pages)
- [Configuration](#configuration)
- [Data and test invoices](#data-and-test-invoices)
- [Sample invoices for stress testing](#sample-invoices-for-stress-testing)
- [Evaluation](#evaluation)
- [Tests and code checks](#tests-and-code-checks)
- [Inspecting the MCP servers](#inspecting-the-mcp-servers)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Known limits](#known-limits)

## What Vera does

You drop in an invoice (PDF, PNG or JPG, up to 10 MB). Vera reads it, extracts the fields, runs 16 checks against the company records and the VAT rules, and shows a verdict:

| Verdict | Meaning |
|---|---|
| **Matched** | All checks pass. |
| **Partial match** | It matches, with only low-severity discrepancies. |
| **Mismatch** | At least one medium or high discrepancy. |
| **Not found** | There is no vendor, purchase order or transaction to compare against. |
| **Needs review** | The system cannot decide, so a person must. |

Every verdict comes with the discrepancies found (field, expected value, found value, severity), the evidence records the agent saw, the checks that ran, and a full audit trail. Findings are also boxed on the original document, and you can download an annotated copy.

**Errors it catches.** Fourteen kinds of error, each with a default severity:

| Severity | Errors |
|---|---|
| High | Duplicate invoice, already paid, unknown vendor, changed bank details, sanctioned vendor, prompt injection |
| Medium | Wrong amount, quantity not received, wrong VAT rate, reverse charge error, invalid VAT number, missing mandatory fields, unusual amount (includes split invoices) |
| Low | VAT arithmetic error |

Fraud-type findings (changed IBAN, sanctioned vendor, duplicate) give `Mismatch` with high severity. A prompt injection gives `Needs review`, because the extracted data cannot be trusted.

## How it works

```
 Browser (NiceGUI)  ──►  ui/real_service.py  ──►  agent/service.py     the one contract the UI uses
                                                        │
                                       agent/pipeline.py (the verification run)
                          ┌───────────────┬─────────────┴──────────┬────────────────┐
                    extract (LLM)    fixed checks (code)      agent review (LLM)   report
                          │               │                        │                │
                          └────────────► agent/gateway.py ◄────────┴────────────────┘
                                 allowlist per role · call budget · audit log
                                              │ MCP
        documents · transactions · tax_rules · vendor_checks · anomaly_stats · review_cases
```

**The six stages** (the live page shows each one):

1. **Read document.** The documents server converts the file to Markdown (MarkItDown). Scans and photos go through Tesseract OCR. Anything not in English stops here and goes to review.
2. **Extract fields.** The local model (Ollama, `qwen3:8b`) turns the Markdown into a structured invoice, constrained to a JSON schema. A field that cannot be read from a scan is flagged as low confidence.
3. **Run checks.** 16 fixed checks run in code, each calling a server tool. The verdict comes from these checks only.
4. **Agent review.** The model gets six read-only lookup tools (up to 8 steps) and writes notes. Its notes are advice and are labelled "AI note" or "AI suggests"; they cannot approve or reject anything.
5. **Verdict.** Fixed rules turn the check results into one of the five verdicts.
6. **Report.** For anything that is not `Matched`, a review case is opened with the evidence.

**The 16 checks**, grouped by the server that answers them:

| Server | Checks |
|---|---|
| host | No instructions aimed at the AI (phrase list, then a model check) |
| transactions | Vendor is known · Not a duplicate · Not already paid · Matches the purchase order · Goods received |
| tax_rules | Supplier VAT number is valid · Correct VAT treatment (domestic or reverse charge) · Correct VAT rate · Mandatory invoice fields (including the KvK number) · VAT arithmetic |
| vendor_checks | Bank details match the vendor record (the IBAN is validated too) · Not on a sanctions list · No overlap with employees |
| anomaly_stats | Amount is typical for this vendor (also catches split invoices) · Invoice date (weekends, holidays) |

**OCR repair.** On a scan, a VAT number or IBAN that fails its checksum but is one look-alike swap away from a valid one (O and 0, B and 8) is repaired and flagged rather than rejected. If a mandatory field on a scan is unreadable, the invoice goes to `Needs review` instead of being marked as missing a field. Vera never turns an unreadable field into a confident `Matched`.

**The six MCP servers** (each runs as its own process; the agent starts them itself):

| Server | Port* | Tools |
|---|---|---|
| `documents` | 8101 | `list_documents`, `convert_document`, `get_document_metadata` |
| `transactions` | 8102 | `get_vendor`, `lookup_invoice_number`, `find_transactions`, `get_purchase_order`, `vendor_payment_summary` |
| `tax_rules` | 8103 | `validate_vat_number`, `get_vat_rate`, `expected_vat_treatment`, `check_invoice_requirements`, `validate_invoice_math`; resources `policy://verification-rules` and `schema://invoice`; prompt `review_findings` |
| `vendor_checks` | 8104 | `validate_iban`, `compare_bank_details`, `screen_sanctions`, `vendor_employee_overlap` |
| `anomaly_stats` | 8105 | `vendor_amount_profile`, `split_invoice_check`, `date_flags` (aggregates only) |
| `review_cases` | 8106 | `create_review_case`, `add_case_note`, `export_evidence_pack` (the only write access; append-only) |

\* Ports are used only when you start the servers by hand over HTTP (see [Inspecting the MCP servers](#inspecting-the-mcp-servers)).

## Privacy and safety model

- **Local only.** The model runs in Ollama on your machine. The interface listens on `127.0.0.1` by default.
- **Least privilege.** Three agent roles, each with its own allowlist. A call outside the list is refused and logged.

  | Role | May use servers | Tools the model itself sees |
  |---|---|---|
  | extractor | documents | `convert_document`, `get_document_metadata` |
  | verifier | documents, transactions, tax_rules, vendor_checks, anomaly_stats | `get_vendor`, `lookup_invoice_number`, `find_transactions`, `get_purchase_order`, `vendor_payment_summary`, `convert_document` |
  | reporter | review_cases | `create_review_case`, `add_case_note`, `export_evidence_pack` |

- **Narrow tools.** Read-only database connection, parameterised queries only, at most 20 rows per call, IBANs masked to the last four digits, and aggregates suppressed when fewer than 5 records back them.
- **Budget.** At most 60 tool calls per invoice.
- **Audit log.** Every call, allowed or refused, is written to `data/audit.jsonl` and shown on the "What the AI saw" page.
- **Prompt injection.** Invoice text is wrapped as untrusted data and cannot change the verdict. It is scanned twice for text aimed at an AI: first with a list of known phrasings (instant), then, if that finds nothing, by the model itself in a separate call that may only classify and must quote the exact words (a quote that is not really in the document is ignored). Documents containing such text go to a person; neither scan can approve anything.
- **Humans decide.** Only a reviewer in the interface can approve, reject or escalate a case, and a reason is required. This is never exposed as a tool to any agent.
- **Safe file handling.** Only PDF, PNG and JPG; at most 10 MB and 10 pages. Raw file paths and URIs are never accepted; documents are referred to by short ids such as `INV-001`.
- **The original file is never changed.** Highlights are drawn on a copy.

## Requirements

- **Python 3.11 or newer** (`.python-version` pins 3.12)
- **[Ollama](https://ollama.com)**, with the `qwen3:8b` model (about 5 GB; it runs fully on a GPU with 8 GB of VRAM, and more slowly on a CPU)
- **[Tesseract OCR](https://github.com/tesseract-ocr/tesseract)** with the English (`eng`) language pack, for scanned invoices
- **Git**
- **Node.js** (optional, only for the MCP Inspector)

Vera has been run on Windows 11 with Ollama inside WSL. Linux and macOS should work the same way.

## Quick start

### 1. Get the code and create the environment

```bash
git clone https://github.com/josh1221wa/ey_hackathon_agent.git
cd ey_hackathon_agent
python -m venv .venv
```

Activate it:

```powershell
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux / Git Bash
source .venv/bin/activate
```

### 2. Install

```bash
python -m pip install --upgrade pip
python -m pip install -e . pytest ruff
```

All dependencies are declared in `pyproject.toml`.

### 3. Pull the model

```bash
ollama pull qwen3:8b
```

Ollama can run on Windows or inside WSL; Vera reaches it at `http://127.0.0.1:11434` either way (set `OLLAMA_HOST` for another address). If Ollama runs in WSL, keep a WSL terminal open: WSL stops the distro, and Ollama with it, when no session is open.

### 4. Check the install

```bash
python -c "import mcp, nicegui, pydantic, stdnum; print('ok')"
tesseract --list-langs
```

`tesseract --list-langs` must list `eng`.

### 5. Create the data

The company records and the 20 test invoices are generated, not downloaded, and the output is identical on every run:

```bash
python data/generate.py
python data/render_pdfs.py
```

- `generate.py` writes `data/db.sqlite` (about 30 vendors, purchase orders, receipts, about 2,000 past transactions and the reference tables) and the invoice specs.
- `render_pdfs.py` renders the 20 invoices as PDFs in `data/invoices/`. Five are "scanned" (image only, slightly rotated and noisy) to exercise OCR.

The inbox (`data/inbox/`) starts empty, as it would for a new user: upload your own invoices in the interface. The 20 test invoices stay in `data/invoices/` for the evaluation, which copies them into a private temporary inbox and never touches yours. To put them in the inbox anyway (for example for the [demo page](#demo-the-demo-script)), run `python scripts/load_inbox.py`.

To have invoices to upload by hand, also run:

```bash
python scripts/make_samples.py
```

It makes 30 sample invoices in `samples/` (see [Sample invoices](#sample-invoices-for-stress-testing)). They are not in the inbox, so you can upload them yourself.

### 6. Run

```bash
python -m ui.main
```

Open <http://localhost:8080>. (After `pip install -e .` the command `vera` does the same.) Vera starts its six MCP servers with the interface, so the first page already shows them as up.

## Using the interface

| Page | Address | What it is for |
|---|---|---|
| **Inbox** | `/` | Upload invoices (drag and drop, several at once). A table of every document with vendor, amount, status, verdict and risk flags; search and filter; **Verify** per row or **Verify all**. |
| **Live verification** | `/verify` and `/verify/<id>` | Watch one invoice being verified: the six stages, the 16 checks filling in, every tool call as it happens, the agent's notes, a timer, and Cancel. |
| **Verdicts** | `/verdict/<id>` | The result: verdict banner, confidence, risk flags and discrepancy table; the original invoice with the problems boxed; extracted fields (uncertain ones marked); the VAT panel, bank panel and evidence records. |
| **Review queue** | `/review` | Cases sorted by severity, with filters. Add notes, then **Approve**, **Reject** or **Escalate** (a reason is required), or export the **evidence pack** as PDF or spreadsheet. |
| **What the AI saw** | `/audit` | Every tool call, filterable by invoice, role, server and refused or not, with masked results; what each role was allowed to use against what it used. |

**Boxes on the document.** On the verdict page, each discrepancy is drawn on the original invoice as a numbered box, with a legend (expected value, found value). On PDFs with text, the position comes from the text layer; on scans and photos it comes from OCR. A finding that cannot be found on the page is listed but not boxed. **Download annotated copy** saves a PDF with the boxes and a findings page; the original is untouched.

**Light and dark.** The "Light or dark" button at the bottom of the left menu switches the theme. The header shows a "Runs 100% locally" badge and a status chip (model ready and servers up, or what is missing).

**Status is never shown by colour alone.** Every status also has an icon and a word.

## Hidden pages

Two pages are left out of the menu on purpose. They are for the team and for presenting, not for everyday users, but they are always available by address.

### `/demo`: the demo script

<http://localhost:8080/demo>

It needs the 20 test invoices in the inbox (`python scripts/load_inbox.py`). One card for each step of the demo, in order, each opening the right invoice and starting it:

| Step | Invoice | Shows |
|---|---|---|
| 1 | `INV-019` | A clean invoice from a Dutch supplier: every check passes. |
| 2 | `INV-020` | A duplicate of an invoice already approved (a fraud-type finding). `INV-010` is the changed-IBAN case. |
| 3 | `INV-004` | A German supplier charging 21% Dutch VAT instead of reverse charge. |
| 4 | `INV-016` | Food charged at 21% instead of 9%. |
| 5 | `INV-003` | An unusually large invoice, flagged from totals only. |
| 6 | `INV-018` | An invoice with instructions aimed at the AI: it goes to a person. |
| 7 | | Opens the review queue (cases and evidence pack). |
| 8 | | Opens the audit log. |

- **Play recordings instead of running live** (switch): each step replays the saved run of that invoice at its original pace (pauses over about two seconds are cut short). Nothing is run: no model, no servers, no new audit entries. Use it when the model is slow or Ollama is unavailable.
- **Keyboard:** keys `1` to `6` start a step; `R` switches between live and recording.
- You can replay any invoice directly: `/verify/<id>?start=1&replay=1`. A recording exists once an invoice has been verified.

### `/eval`: evaluation results

<http://localhost:8080/eval>

The results of the evaluation against the 20 known-answer invoices (see [Evaluation](#evaluation)): correct verdicts, planted errors caught, consistency across runs, average time per invoice, and a row for every invoice and run with the planted error and the check that caught it. Select a row to open its verdict. **Run eval** starts a new evaluation from the page (it needs Ollama and takes about 30 minutes for 3 runs of 20 invoices).

## Configuration

All settings are environment variables; none are required.

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | Port of the web interface. |
| `HOST` | `127.0.0.1` | Address the interface listens on. Leave it unless you deliberately want other machines to reach Vera, and then also set `UI_STORAGE_SECRET`. |
| `UI_RELOAD` | `0` | Set to `1` while developing, to restart on file changes. |
| `UI_STORAGE_SECRET` | `vera-local` | Signs the browser session cookie. Set your own value if others can reach the port. |
| `INVOICE_MODEL` | `qwen3:8b` | Ollama model to use. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Where Ollama is. |
| `INVOICE_NUM_CTX` | `12288` | Context size given to the model. |
| `INVOICE_MODEL_INJECTION_CHECK` | on | Set to `0` to turn off the model's second look for prompt injection (it adds about a second per document); the phrase list keeps working. |
| `INVOICE_MODEL_CAN_ESCALATE` | off | Set to `1` to let the model's notes send a clean invoice to review. Off by default: its notes are advice only. |
| `INVOICE_TESSERACT_CMD` | found on `PATH` | Full path of the Tesseract program, if it is not on `PATH`. |
| `INVOICE_DATA_DIR` | `data/` | Where the database, inbox, runs, cache, outbox and audit log live. |
| `INVOICE_DB_PATH`, `INVOICE_INBOX_DIR`, `INVOICE_CACHE_DIR`, `INVOICE_AUDIT_LOG` | under the data folder | Override a single location. |

PowerShell: `$env:PORT = "9000"; python -m ui.main`. Bash: `PORT=9000 python -m ui.main`.

**Where things are written** (all under `data/`, and none of it is committed to git): `db.sqlite` (records and review cases), `inbox/` (documents), `runs/` (the recorded events and verdict of each run), `cache/` (converted documents), `outbox/` (evidence packs and annotated copies), `audit.jsonl` (the tool-call log).

## Data and test invoices

The data is synthetic and Dutch: about 30 vendors (mostly Dutch, a few Belgian and German), purchase orders and goods receipts, about 2,000 transactions over two years, VAT rates with validity dates (21%, 9%, 0%), EU countries, Dutch bank codes and a sample of the EU sanctions list. All text is English; Faker's `nl_NL` locale is used only for company names and addresses.

There are 20 test invoices: 6 clean and 14 with one planted error each. `data/invoices/ground_truth.json` has the expected verdict and error for each, and `data/invoices/specs/` has the spec each PDF is rendered from.

| Invoices | Planted error |
|---|---|
| 002, 009, 013, 014, 015, 019 | None (clean; 002 and 014 are correct reverse charge from Germany and Belgium) |
| 001 | Wrong amount |
| 003 | Unusual amount |
| 004 | Reverse charge error |
| 005 | Unknown vendor |
| 006 | Invalid VAT number |
| 007 | Quantity not received |
| 008 | Already paid |
| 010 | Changed bank details |
| 011 | Sanctioned vendor |
| 012 | Missing mandatory fields |
| 016 | Wrong VAT rate |
| 017 | VAT arithmetic error |
| 018 | Prompt injection |
| 020 | Duplicate invoice |

## Sample invoices for stress testing

Real invoices do not look alike, so neither do these. `python scripts/make_samples.py` writes 30 invoices to `samples/` (committed, so you can use them without running the script) and an `answer-key.md` that says what each one should give. Every one is built differently:

- **Eleven document structures:** a classic letter, a sidebar, a banner header, a minimal page, a ruled form, a two-page letter with terms and a tear-off remittance slip, a landscape sheet, a dense page that puts the total first, a typewriter printout, a stamped copy and a narrow till-roll receipt.
- **A drawn logo and company details for each supplier**, with its own colours, tagline and name style.
- **Different wording:** "Invoice", "Tax invoice" or "Sales invoice"; "Invoice number", "Doc #" or "No."; "Amount due" or "Gross total"; four different sets of column names, and some invoices leave out the item-code or VAT-rate column.
- **Different formats:** `EUR 1,234.56`, `€1,234.56`, `€ 1.234,56`, `1,234.56 EUR` and plain `1234.56`; six date styles (`2026-10-21`, `21 Oct 2026`, `21/10/2026`, `21.10.2026`, `October 21, 2026`, `21-10-2026`).
- **Different kinds of file:** PDFs with text, scanned PDFs, a poor scan, a fax, a phone photo taken at an angle, a scanned JPG and clean PNG screenshots. Two carry a watermark ("COPY", "ORIGINAL").

| | Count | What |
|---|---|---|
| Clean | 10 | Should be `Matched`: domestic and reverse-charge (Germany, Belgium), mixed 9% and 21% rates, books, a freight line, several lines |
| Faulty | 20 | Every one of the 14 planted errors at least once, including a prompt injection hidden in white text and a large invoice dated on a Saturday, plus one invoice with two problems at once |

**How the agent does on them** (`qwen3:8b`, a clean rebuild): 23 of the 30 give the expected verdict. The other seven are limits of the model or of OCR, not of the checks, and none is approved wrongly except as noted:

| Samples | What happens | Outcome |
|---|---|---|
| 2, 11, 21 (a scan, a phone photo, a skewed photo) | OCR cannot read key fields | `Needs review`: sent to a person, which is the intended safe fail |
| 13 (a poor scan of a typewriter printout) | The table is garbled, so the lines do not add up | `Partial match` from the arithmetic check |
| 10 (a till-roll receipt) | The model takes the "6 x EUR 16.80" line as the description and double counts the VAT | `Partial match` from the arithmetic check |
| 12 (a sidebar layout, supplier named "NS") | The model swaps supplier and buyer | `Not found`, sent to a person |
| 14 | The model drops one line description | `Mismatch` on mandatory fields |

These samples found and led to fixes for: Dutch-style amounts (`4.304,47`) and dates (`27/10/2026`) being treated as unconfirmed, dates not converted to ISO, every address looking doubtful, an injection hidden in white text with unusual wording, and a re-sent invoice with a misread digit in its number slipping past the duplicate check.

The first seven show the same data as seven of the 20 test invoices, in new designs. The other 23 are new invoices with their own purchase orders, receipts and payments in the database, so if your database is older than this feature, run `python data/generate.py` once more (it rebuilds `data/db.sqlite` and so clears any review cases you created). The invoice data for the 23 is in `data/invoices/sample_ground_truth.json`, and the drawing code is in `data/render_samples.py`.

### A held-out check

Fixing what the samples showed makes them a training set, so their score flatters the system. `python eval/heldout.py <out.json>` builds 24 new invoices (new layouts, wording, number and date styles, error variations and injection wordings) in a throwaway folder and verifies them through the agent. Do not tune the checks on them; make a new batch when you need a fresh test. The latest result, with every miss explained, is in `eval/results/heldout.txt`: **17 of 24**. New variations of every error are caught (13 of 14), but clean invoices are flagged too often (2 of 6, partly because of model extraction errors and partly flaws in the batch itself), and the injection scan caught only 1 of 4 new wordings.

## Evaluation

The evaluation verifies every test invoice several times and compares each result with the known answer.

```bash
python eval/run_eval.py
```

It needs Ollama running. It does not use your inbox: it copies the 20 test invoices into a private temporary one for the run. By default it runs all 20 invoices 3 times each (about 30 minutes) and prints a table: expected against actual verdict, whether the planted error was caught and by which check, whether the runs agreed, and the time and tool calls per run.

Each result is `pass`, `safe_fail` or `fail`. A **safe fail** is a scan whose key field could not be read and that was therefore sent to a person instead of being approved or rejected. It is counted separately from a wrong answer.

Options: `--runs 1 --no-agent` for a quick check, `--model <name>` to try another Ollama model, or list invoice ids (`INV-004 INV-018`) to run only those. Every run is saved in `data/runs/eval/`, and an evaluation never opens review cases.

**Result with `qwen3:8b`** (3 runs per invoice, full output in `eval/results/qwen3-8b.txt`):

| | |
|---|---|
| Pass | 18 of 20 |
| Safe fail | 2 (scans whose invoice date could not be read, so they went to a person) |
| Fail | 0 |
| Same verdict in every run | 20 of 20 |
| Mean time per invoice | 29 seconds |

To measure the prompt-injection check on a set of injections and harmless look-alikes (the sets used so far are in `eval/injection_sets/`; write a new one for an honest measure):

```bash
python eval/injection_check.py eval/injection_sets/set_b.json out.json
```

To measure how well the model reads the invoices on its own:

```bash
python eval/extraction_accuracy.py
```

With `qwen3:8b`: 99.0% of fields overall, 99.8% on text PDFs and 96.3% on scans. The remaining errors are OCR misreads that the model copied faithfully.

## Tests and code checks

```bash
python -m pytest
python -m ruff check .
```

The tests build their own temporary dataset, so they never touch `data/db.sqlite`, and they use a stub in place of the language model, so they need no Ollama. They start the real MCP servers, so Tesseract must be installed. A full run takes about two minutes. They cover the gateway and its allowlists, every server tool, the extraction and the agent loop, the verification pipeline, review cases and evidence packs, the evaluation, the service the interface uses (including replay), the highlighting (including that the original file is never changed), the 30 sample invoices, and that the recorded event streams in `tests/recorded_events/` always match the event contract.

## Inspecting the MCP servers

The agent starts the servers itself and does not need this. To look at them by hand:

```bash
python scripts/start_servers.py
```

This starts all six over HTTP (ports 8101 to 8106, each at `/mcp`). Connect with the MCP Inspector (`npx @modelcontextprotocol/inspector`, transport "Streamable HTTP").

## Project layout

| Path | Purpose |
|---|---|
| `agent/` | The agent: `service.py` (the contract the interface uses), `pipeline.py` (a verification run), `checks.py` (the 16 fixed checks), `extract.py`, `llm.py` and `loop.py` (Ollama and the tool loop), `gateway.py` (allowlists, budget, audit), `roles.py`, `schemas.py` (every model and event), `evaluation.py` |
| `servers/` | The six MCP servers, plus `common/` (masking, row caps, database access, audit writer) and `registry.py` |
| `ui/` | The NiceGUI interface: `main.py`, `theme.py` and `static/vera.css` (design), `pages/`, `components/`, `real_service.py` (shapes the agent's answers for the pages), `highlights.py` (boxes on the document) |
| `data/` | Generators for the synthetic data (`generate.py`, `render_pdfs.py`, `render_samples.py`, `reference.py`, `schema.sql`) and the 20 test invoices, plus the data of the 23 extra sample invoices; the generated database, inbox and run files are git-ignored |
| `eval/` | The evaluation and extraction-accuracy scripts, and the saved results |
| `scripts/` | `load_inbox.py`, `make_samples.py` (the 30 differently designed samples), `start_servers.py` |
| `tests/` | The test suite and the recorded event streams |
| `CLAUDE.md` | Working agreements for contributors |

## Troubleshooting

| Problem | What to do |
|---|---|
| The status chip says **Ollama is not running** | Start Ollama and check `ollama list` shows `qwen3:8b`. In WSL, keep a WSL terminal open. Set `OLLAMA_HOST` if it is on another address. |
| Scans are not read, or OCR errors appear | Check `tesseract --list-langs` lists `eng`. If Tesseract is not on `PATH`, set `INVOICE_TESSERACT_CMD`. |
| The inbox is empty | That is the normal start: upload invoices, or run `python scripts/load_inbox.py` to add the 20 test invoices. |
| A sample says the purchase order or vendor is unknown | Your database is older than the samples. Run `python data/generate.py` again. |
| **Address already in use** on port 8080 | Another copy is running. Stop it, or set `PORT`. |
| A run says there is no recording | Replay needs an earlier run of that invoice. Verify it once live. |
| No boxes on a verdict | The text could not be found on the page, for example when the model misread a value. The discrepancy is still listed. |
| Verification is slow | The model runs on the CPU. Use a GPU, or use recordings in the demo. |
| Want a clean start | Stop Vera and delete `data/db.sqlite`, `data/runs/`, `data/audit.jsonl`, `data/cache/` and `data/outbox/`, then run `python data/generate.py` again. The inbox (`data/inbox/`) is also cleared if you want it empty. |

## Contributing

Read [CLAUDE.md](CLAUDE.md). In short: never commit to `main`; work on a branch named `<name>-<work>`, open a pull request and merge it; declare every new package in `pyproject.toml`; never commit secrets, `.env` files or generated data.

## Known limits

- The VAT rules cover the Netherlands (domestic, intra-EU reverse charge); other countries' rules are not modelled.
- Invoices must be in English; anything else goes straight to review.
- Scans depend on OCR quality. Unreadable key fields send the invoice to a person rather than guessing.
- The data is synthetic. Connecting Vera to a real ledger (for example SAP or Exact) means writing new servers behind the same tools; the agent and the interface would not change.
- The prompt-injection check is a phrase list plus a small local model; on 20 fresh injections it caught 18 to 19 with no false alarms on 20 harmless look-alikes and none on 67 real documents (`eval/results/injection-check.txt`). That is a small sample, so a determined attacker with a new trick may still get through; it only ever sends a document to a person.
- Evaluated on 20 invoices with one model (`qwen3:8b`). On the 30 differently designed samples it gets 23 right; the rest depend on OCR quality and on how unusual the layout is (see [Sample invoices](#sample-invoices-for-stress-testing)).
