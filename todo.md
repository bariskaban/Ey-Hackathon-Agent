# TODO: Invoice Verification Agent

See [idea.md](idea.md) for the full design. This file splits the work between the two of us.

**Scope:** 6 MCP servers, Dutch VAT rules, 20 test invoices. **Everything is in English:** invoices, prompts, tool outputs and the UI.

| Tag | Owner | Owns these folders |
|---|---|---|
| **[TECH]** | Technical layer: data, MCP servers, gateway, agent, eval | `data/`, `servers/`, `agent/`, `eval/`, `scripts/` |
| **[UI]** | UI/UX: NiceGUI web app, design, demo flow, pitch visuals | `ui/`, `fixtures/`, `docs/` |
| **[BOTH]** | Shared decisions and integration | `agent/schemas.py`, `agent/service.py` (the contract), `README.md` |

**Rule:** each person only edits their own folders. A change to the contract (`schemas.py` / `service.py`) needs the other person's OK, which keeps us out of merge conflicts.

**UI framework:** **NiceGUI** (pure Python, runs on FastAPI, live updates over websockets, built-in upload, AG Grid and ECharts, no Node build step).

---

## 0. Kickoff [BOTH]

- [x] Create the GitHub repo, `.gitignore` (data/db, cache, outbox, runs, `.venv`), and `README.md` with run instructions
- [x] Python 3.11+, use `uv` for the environment, one shared `pyproject.toml`
- [x] Install Ollama and pull the models: `qwen3:8b` (later `gpt-oss:20b` and the Qwen3 30B-A3B model for comparison; check the exact tags in the Ollama library)
- [x] Install Tesseract (English language pack only), and Node (only needed for `npx @modelcontextprotocol/inspector`)
- [x] **Agree the contract (section 1) together**, about an hour. TECH writes `schemas.py`; UI reviews field names and what each screen needs. (Conventions agreed, see "Agreed decisions" in section 1; the full `schemas.py` models are still open in section 1.)
- [x] Agree the verdict names, the 14 error-type names and the severity colours (they show up everywhere in the UI). (Agreed, see "Agreed decisions" in section 1; enums live in `agent/schemas.py`; the verdict enum is `VerdictStatus` so `Verdict` stays free for the full result model.)
- [x] Git workflow: short feature branches → PR → merge to `main`, and keep `main` runnable

---

## 1. The contract between backend and UI [BOTH]

The UI **only** imports `agent/service.py`. Until the backend is ready, the UI uses `ui/mock_service.py` with exactly the same functions. Switch with an env var: `INVOICE_SERVICE=mock|real`.

### Service functions (`agent/service.py`)

```python
async def list_documents() -> list[DocumentInfo]
async def upload_document(filename: str, data: bytes) -> DocumentInfo
def get_document_file(doc_id: str) -> Path                  # for the PDF/image preview
async def verify(doc_id: str) -> AsyncIterator[Event]        # streams events; the last one is the verdict
async def get_verdict(doc_id: str) -> Verdict | None

async def list_cases(status: str | None = None) -> list[ReviewCase]
async def get_case(case_id: str) -> ReviewCase
async def set_case_status(case_id: str, status: str, reviewer: str, reason: str) -> ReviewCase  # humans only, never exposed to agents
async def export_evidence_pack(case_id: str, fmt: str = "pdf") -> Path

async def get_audit_log(doc_id: str | None = None, role: str | None = None,
                        server: str | None = None) -> list[AuditEntry]
async def run_eval(model: str, runs_per_invoice: int = 3) -> AsyncIterator[EvalProgress]
async def list_eval_runs() -> list[EvalReport]
async def system_status() -> SystemStatus                    # Ollama up? which model? servers running?
```

### Event types streamed by `verify()`

| `type` | Payload | UI uses it for |
|---|---|---|
| `stage` | `stage` (convert / extract / checks / agent / verdict / report), `status` (started / done / failed) | Progress stepper |
| `markdown_ready` | `method` (markitdown / ocr), `char_count`, `truncated` | "Document read" step |
| `invoice_extracted` | `invoice`, `low_confidence_fields[]` | Extracted-fields panel |
| `check_result` | `name`, `server`, `status` (pass / fail / skipped), `detail`, `severity` | Live checklist |
| `tool_call` | `role`, `server`, `tool`, `args`, `rows_returned`, `latency_ms`, `refused` | Live audit feed |
| `llm_note` | `role`, `text` (short summary of the reasoning) | Agent reasoning panel |
| `verdict` | `verdict` (the full `Verdict`) | Verdict page |
| `error` | `message`, `stage` | Error state |

### Agreed decisions

**Conventions**
- `agent/schemas.py` is the single source of truth; fixtures and `mock_service.py` are written from it.
- Every enum is a `str` Enum, serialised as its name.
- Money is `Decimal` plus a currency code, serialised as a string, never a float.
- Timestamps are ISO-8601 UTC. `confidence` is 0.0 to 1.0.
- Every event has a `type` discriminator, plus `doc_id`, `seq` and `ts`.
- `doc_id` is a short readable ID (`INV-001`); the SHA-256 stays internal to the documents server.
- A discrepancy's `type` is the `ErrorType` enum (not a free string). `checks[]` in `Verdict` carry `severity`. `NEEDS_REVIEW` carries a `reason`.

**Verdicts**

| Verdict | Meaning | Colour |
|---|---|---|
| `MATCHED` | All checks pass | green `#16A34A` ✔ |
| `PARTIAL_MATCH` | Matches, with only low-severity discrepancies | amber `#D97706` ◐ |
| `MISMATCH` | At least one medium or high discrepancy | red `#DC2626` ✖ |
| `NOT_FOUND` | No vendor, PO or transaction to compare against | grey `#6B7280` ? |
| `NEEDS_REVIEW` | The system can't decide, so a human must | blue `#2563EB` 👁 |

**Error types (14) and default severity**

| # | `ErrorType` | Severity |
|---|---|---|
| 1 | `WRONG_AMOUNT` | medium |
| 2 | `QUANTITY_NOT_RECEIVED` | medium |
| 3 | `DUPLICATE_INVOICE` | high |
| 4 | `ALREADY_PAID` | high |
| 5 | `UNKNOWN_VENDOR` | high |
| 6 | `VAT_ARITHMETIC_ERROR` | low |
| 7 | `WRONG_VAT_RATE` | medium |
| 8 | `REVERSE_CHARGE_ERROR` | medium |
| 9 | `INVALID_VAT_NUMBER` | medium |
| 10 | `MISSING_MANDATORY_FIELDS` | medium |
| 11 | `CHANGED_BANK_DETAILS` | high |
| 12 | `SANCTIONED_VENDOR` | high |
| 13 | `UNUSUAL_AMOUNT` (covers "split invoice" too; say which in `detail`) | medium |
| 14 | `PROMPT_INJECTION` | high |

- `ground_truth.json` uses `CLEAN` for the 6 clean invoices; it is not an `ErrorType`.
- Fraud-type findings (changed IBAN, sanctioned vendor, duplicate) give `MISMATCH` with high severity.
- `PROMPT_INJECTION` gives `NEEDS_REVIEW` with a high-severity flag, because the extracted data can't be trusted.

**Severity colours:** high red `#DC2626`, medium orange `#EA580C`, low yellow `#CA8A04`. Check status: pass green, fail red, skipped grey. Always pair colour with an icon and a text label (dark mode, colour-blind users).

- [ ] [TECH] Write `agent/schemas.py`: `DocumentInfo`, `Invoice` (with VAT lines per rate, VAT numbers, KvK number, IBAN), `Verdict`, `Event` (one model per event type), `AuditEntry`, `ReviewCase`, `EvalProgress`, `EvalReport`, `SystemStatus`
- [ ] [TECH] Write a `service.py` stub with these signatures that raises `NotImplementedError`
- [ ] [TECH] Hand-write 4 sample event streams in `fixtures/events/` (clean, duplicate with a changed IBAN, reverse-charge error, prompt injection), then replace them with real recordings later
- [ ] [UI] Write `ui/mock_service.py`: same functions, returns fixtures, and streams events with realistic delays

---

## 2. TECH: technical layer

### Phase 1: Data
- [x] DB schema (SQLite): vendors (with country, VAT number, KvK number, IBAN), POs, PO lines (with VAT category), goods receipts, transactions, invoices seen, employees
- [x] Reference tables: VAT rates with validity dates (21 / 9 / 0), EU countries, bank codes (INGB, RABO, ABNA…), sanctions (EU consolidated list sample), review cases
- [x] `data/generate.py`: about 30 vendors (mostly Dutch, a few Belgian and German), about 2,000 past transactions over two years. All text in English; Faker `nl_NL` only for company names and addresses
- [x] Valid VAT numbers and IBANs for clean vendors (generate them so they pass `python-stdnum`)
- [x] **The 20 test invoices:** 6 clean + 14 with one planted error each (idea.md §4), plus `ground_truth.json`
- [x] All invoice wording in English ("Invoice", "VAT", "Reverse charge", "Total incl. VAT")
- [x] `data/render_pdfs.py`: about 15 normal PDFs and about 5 "scanned" ones (rasterised, noisy, slightly rotated)

### Phase 2: Transaction server and gateway
- [ ] `servers/common/`: masking, row caps, pagination, audit-log writer (JSONL)
- [ ] `servers/transactions/`: `get_vendor`, `lookup_invoice_number` (fuzzy), `find_transactions`, `get_purchase_order`, `vendor_payment_summary`
- [ ] Read-only DB connection, parameterized queries only
- [ ] `agent/gateway.py`: allowlist per role, query budget per invoice, routing, audit entry for every call
- [ ] `scripts/start_servers.py`: start all 6 servers
- [ ] Test every tool in MCP Inspector

### Phase 3: Documents and extraction
- [ ] `servers/documents/`: inbox manifest (`doc_id` → path + SHA-256), `list_documents`, `convert_document(doc_id, pages?)`, `get_document_metadata`
- [ ] MarkItDown conversion, OCR fallback (English) when there's no text layer, output cap with paging, cache by file hash
- [ ] Language check (`langdetect`): return `language` from `convert_document`; anything not English stops early with `NEEDS_REVIEW`
- [ ] Allowed file types, maximum size, maximum page count; raw paths and URIs are never accepted
- [ ] `agent/extract.py`: Markdown → Ollama (`format` = Invoice JSON schema) → Pydantic; retry once on failure
- [ ] Check field-level extraction accuracy on all 20 invoices, especially the scanned ones

### Phase 4: Verification core and service
- [ ] `agent/llm.py`: Ollama wrapper (`num_ctx` 8k–16k, `temperature` 0, tools, `format`)
- [ ] `agent/loop.py`: MCP ↔ Ollama tool loop, step limit about 8, invoice text wrapped as untrusted data
- [ ] `agent/checks.py`: fixed checks (vendor, duplicate, already paid, PO and receipt match)
- [ ] `agent/service.py`: real implementation of `verify()` emitting every event type in section 1
- [ ] Record each run's events to `data/runs/<doc_id>.jsonl` (powers the UI's replay mode)
- [ ] **Milestone M2:** one invoice end to end, live in the UI

### Phase 5: Rule servers
- [ ] `tax_rules`:
  - [ ] `validate_vat_number` (`stdnum.nl.btw`, `stdnum.eu.vat`)
  - [ ] `get_vat_rate(category, date)`
  - [ ] `expected_vat_treatment`: domestic vs reverse charge between EU businesses
  - [ ] `check_invoice_requirements`: Belastingdienst mandatory fields + KvK number
  - [ ] `validate_invoice_math`: lines, VAT per rate, total, rounding
- [ ] `vendor_checks`: `validate_iban`, `compare_bank_details`, `screen_sanctions` (rapidfuzz), `vendor_employee_overlap`
- [ ] `anomaly_stats`: `vendor_amount_profile`, `split_invoice_check`, `date_flags` (`holidays` with country `NL`); aggregates only, minimum group size about 5
- [ ] `review_cases`: `create_review_case`, `add_case_note`, `export_evidence_pack` (PDF + XLSX); append-only
- [ ] Add their checks to `checks.py`, and emit `check_result` events for each
- [ ] `agent/roles.py`: extractor / verifier / reporter allowlists (the verifier LLM sees 6 tools)

### Phase 6: Eval
- [ ] `eval/run_eval.py`: all 20 invoices × 3 runs. Per invoice: expected vs actual verdict, error caught, which check caught it, consistency across runs, seconds, tool calls
- [ ] `run_eval()` in the service streams `EvalProgress` for the UI
- [ ] Compare models: qwen3:8b against gpt-oss:20b and Qwen3-30B-A3B; save each run as an `EvalReport`
- [ ] Fix the prompts and checks for any invoice that fails or flips between runs

### Stretch [TECH]
- [ ] MCP resources (`policy://verification-rules`, `schema://invoice`) and prompts
- [ ] Elicitation for unclear vendor-name matches (needs a `answer_elicitation()` in the contract and an `elicitation` event)
- [ ] Peppol / UBL XML invoices parsed directly in the documents server

---

## 3. UI: NiceGUI app

### U0: Design first
- [ ] Wireframes for every page (Excalidraw, Figma or paper) before writing code. Pages: Inbox, Live verification, Verdict, Review queue, Audit log, Eval results, Demo
- [ ] Design tokens in `ui/theme.py`: colours (verdict and severity colours agreed in kickoff), fonts, spacing, light and dark mode
- [ ] App shell: left nav, header with a **"Runs 100% locally"** badge and a system status chip (Ollama up, model name, servers up, from `system_status()`)
- [ ] Product name, logo and favicon
- [ ] Status is never shown by colour alone: always add an icon or label (accessibility, and readable on a projector)
- [ ] Euro formatting throughout, English style (`€1,234.56`)

### U1: Mock-first setup
- [ ] `ui/main.py` running `ui.run()` with page routing
- [ ] `ui/mock_service.py` and fixtures (see section 1), with an `INVOICE_SERVICE` env-var switch
- [ ] **Milestone M1:** every page clickable on mock data

### U2: Inbox
- [ ] Drag-and-drop upload (`ui.upload`), multiple files at once, file type and size checks with friendly errors
- [ ] Document table (AG Grid): file name, vendor, amount, date, status chip, verdict badge, risk flags
- [ ] Filters (verdict, status) and search
- [ ] "Verify" on a row, and "Verify all" (only 20 invoices, so a full run is realistic)

### U3: Live verification view (the main demo screen)
- [ ] Stage stepper: Read document → Extract → Checks → Agent → Verdict → Report
- [ ] Live checklist that fills in from `check_result` events, grouped by server (transactions, Dutch VAT, vendor checks, anomalies), each check showing pass/fail/skipped and its detail
- [ ] Live audit feed from `tool_call` events: role colour, server, tool, rows returned, latency; refused calls highlighted
- [ ] Agent reasoning panel (`llm_note` events)
- [ ] Elapsed timer, cancel button, error state with a retry button

### U4: Verdict page
- [ ] Split view: invoice preview on the left (PDF pages rendered to images), extracted fields on the right; low-confidence fields marked
- [ ] Big verdict badge, confidence meter, risk flags
- [ ] Discrepancy table: field / expected / found / severity
- [ ] **VAT panel:** VAT per rate (expected vs found), reverse charge yes/no and whether the invoice shows it correctly, VAT number validity, missing mandatory fields
- [ ] Bank panel: IBAN matches the vendor master or not (last 4 digits only), sanctions screening result
- [ ] Evidence transactions table (masked values, exactly as the agent saw them)
- [ ] "Open review case" button
- [ ] Stretch: highlight the mismatched fields directly on the invoice image

### U5: Review queue
- [ ] Case list sorted by severity, with filters by status and error type
- [ ] Case detail: verdict summary, evidence, notes timeline
- [ ] Approve / Reject / Escalate, **reason required**, which calls `set_case_status()`; this is the only place a final status is set
- [ ] Add a note
- [ ] "Export evidence pack" (PDF / XLSX) → download

### U6: Audit log explorer ("what the AI saw")
- [ ] Filterable table: invoice, role, server, tool, refused yes/no
- [ ] Timeline for each invoice: every tool call in order, with the masked results
- [ ] Summary per role: tools allowed vs tools used, budget used
- [ ] This page backs the "the AI never sees the full database" pitch, so make it clear

### U7: Eval results
- [ ] Model selector + "Run eval" button + progress bar (`run_eval()` stream)
- [ ] Headline numbers: correct out of 20, errors caught out of 14, consistent across 3 runs, average seconds per invoice
- [ ] **Results grid:** 20 rows (invoices) × 3 runs, each cell a pass/fail icon, with the planted error type and the check that caught it
- [ ] Model comparison table across eval runs
- [ ] Click a row → verdict page for that invoice

### U8: Demo mode and polish
- [ ] Demo page: one button per demo-script step (idea.md §10), each loading the right invoice and starting verification
- [ ] **Replay mode:** play back a recorded `data/runs/*.jsonl` event stream at real speed, so the demo still works if the live model is slow or fails
- [ ] Empty, loading and error states on every page
- [ ] Check the layout at 1366×768 and 1920×1080 (projector sizes)
- [ ] Keyboard shortcuts for the demo (next scenario, toggle replay)
- [ ] Use the same wording for verdicts, error types and severities everywhere

### Stretch [UI]
- [ ] Vendor page (flags and history per vendor)
- [ ] Architecture page with an interactive diagram of agents → gateway → 6 servers

---

## 4. Pitch and demo [BOTH]

- [ ] [UI] Slide deck and architecture diagram (visuals); [TECH] supplies technical content and numbers
- [ ] [TECH] Final eval numbers (20 × 3 runs) and the model comparison table
- [ ] [BOTH] Demo script rehearsal (idea.md §10), at least twice, timed
- [ ] [TECH] Record replay files for every demo scenario
- [ ] [UI] Record a backup screen video of the full demo
- [ ] [BOTH] Prepare answers: "What can the AI see?", "Why local?", "How does this scale beyond 20 invoices and to SAP or Exact?", "What about prompt injection?", "What about other EU countries' VAT?", "What about invoices in Dutch?"

---

## 5. Integration milestones [BOTH]

| Milestone | TECH has | UI has | Check together |
|---|---|---|---|
| **M0: Contract** | `schemas.py`, `service.py` stub, sample event fixtures | Wireframes, theme | Field names and events cover every screen |
| **M1: Skeletons** | Data and 20 invoices generated; transaction server + gateway working in Inspector | All pages clickable on mock data | Mock events look like what the backend will send |
| **M2: First real invoice** | `verify()` working end to end for core checks | Live view + verdict page | Switch to `INVOICE_SERVICE=real`, run 1 invoice |
| **M3: Rule servers** | Tax, vendor, anomaly and review-case servers | Review queue, audit log, VAT and bank panels | Demo steps 1–8 work live |
| **M4: Eval** | 20 × 3 eval + model comparison | Eval results page + evidence-pack export | All 20 invoices run; results show in the UI |
| **M5: Freeze** | Replay recordings, bug fixes only | Demo mode, replay, polish | Two full rehearsals, backup video recorded |

---

## 6. Definition of done (for the demo)

- [ ] All demo scenarios run live from the Demo page, with replay as the fallback
- [ ] Every verdict shows its evidence, the checks run and the audit trail
- [ ] All 14 planted errors are caught, and the 6 clean invoices come back `MATCHED`
- [ ] Verdicts are the same across 3 runs
- [ ] No agent can call a tool outside its allowlist (refused calls are visible in the audit log)
- [ ] Runs on one 8GB-VRAM laptop with no internet connection
