# TODO: Invoice Verification Agent

See [idea.md](idea.md) for the full design. This file splits the work between the two of us.

| Tag | Owner | Owns these folders |
|---|---|---|
| **[TECH]** | Technical layer: data, MCP servers, gateway, agent, eval | `data/`, `servers/`, `agent/`, `eval/`, `scripts/` |
| **[UI]** | UI/UX: NiceGUI web app, design, demo flow, pitch visuals | `ui/`, `fixtures/`, `docs/` |
| **[BOTH]** | Shared decisions and integration | `agent/schemas.py`, `agent/service.py` (the contract), `README.md` |

**Rule:** each person only edits their own folders. A change to the contract (`schemas.py` / `service.py`) needs the other person's OK, which keeps us out of merge conflicts.

**UI framework:** **NiceGUI** (pure Python, runs on FastAPI, live updates over websockets, built-in upload, AG Grid and ECharts, no Node build step).

---

## 0. Kickoff [BOTH]

- [ ] Create the GitHub repo, `.gitignore` (data/db, cache, outbox, `.venv`), and `README.md` with run instructions
- [ ] Python 3.11+, use `uv` for the environment, one shared `pyproject.toml`
- [ ] Install Ollama and pull the models: `qwen3:8b`, `nomic-embed-text` (later `gpt-oss:20b` and the Qwen3 30B-A3B model; check the exact tags in the Ollama library)
- [ ] Install Tesseract (Windows installer) and Node (only needed for `npx @modelcontextprotocol/inspector`)
- [ ] **Agree the contract (section 1) together**, about an hour. TECH writes `schemas.py`; UI reviews field names and what each screen needs.
- [ ] Agree the verdict names, error-type names and severity colours (they show up everywhere in the UI)
- [ ] Git workflow: short feature branches → PR → merge to `main`, and keep `main` runnable

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
async def answer_elicitation(request_id: str, answer: str) -> None

async def list_cases(status: str | None = None) -> list[ReviewCase]
async def get_case(case_id: str) -> ReviewCase
async def set_case_status(case_id: str, status: str, reviewer: str, reason: str) -> ReviewCase  # humans only, never exposed to agents
async def export_evidence_pack(case_id: str, fmt: str = "pdf") -> Path

async def get_audit_log(doc_id: str | None = None, role: str | None = None,
                        server: str | None = None) -> list[AuditEntry]
async def run_eval(model: str) -> AsyncIterator[EvalProgress]
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
| `elicitation` | `request_id`, `question`, `options[]` | Dialog asking the human |
| `verdict` | `verdict` (the full `Verdict`) | Verdict page |
| `error` | `message`, `stage` | Error state |

- [ ] [TECH] Write `agent/schemas.py`: `DocumentInfo`, `Invoice`, `Verdict`, `Event` (one model per event type), `AuditEntry`, `ReviewCase`, `EvalProgress`, `EvalReport`, `SystemStatus`
- [ ] [TECH] Write a `service.py` stub with these signatures that raises `NotImplementedError`
- [ ] [TECH] Hand-write 4 sample event streams in `fixtures/events/` (clean, duplicate with a changed bank account, tampered QR, prompt injection), then replace them with real recordings later
- [ ] [UI] Write `ui/mock_service.py`: same functions, returns fixtures, and streams events with realistic delays

---

## 2. TECH: technical layer

### Phase 1: Data and ground truth
- [ ] DB schema (SQLite): vendors, POs, PO lines, goods receipts, transactions, invoices seen, employees
- [ ] Reference tables: HSN rates, state codes, sanctions, IFSC, FX rates, holidays, contracts, bank statement lines, review cases
- [ ] `data/generate.py`: Faker vendors, POs and transactions that make sense together
- [ ] Plant all 20 error types (idea.md §4) with ground-truth labels, about 300 invoices, 8–10 examples of each type
- [ ] `data/render_pdfs.py`: invoice PDFs (reportlab), some of them "scanned" (rasterised, noisy, slightly rotated)
- [ ] Signed e-invoice QR codes, made with our own test key; include valid and tampered examples
- [ ] Synthetic MT940 / CAMT.053 bank statements
- [ ] Synthetic contracts and an AP policy document (Markdown/PDF) for the contracts server

### Phase 2: Transaction server and gateway
- [ ] `servers/common/`: masking, row caps, pagination, audit-log writer (JSONL)
- [ ] `servers/transactions/`: `get_vendor`, `lookup_invoice_number` (fuzzy), `find_transactions`, `get_purchase_order`, `vendor_payment_summary`, `validate_invoice_math`
- [ ] Read-only DB connection, parameterized queries only
- [ ] `agent/gateway.py`: allowlist per role, query budget per invoice, routing, audit entry for every call
- [ ] `scripts/start_servers.py`: start all servers
- [ ] Test every tool in MCP Inspector

### Phase 3: Document server and extraction
- [ ] `servers/documents/`: inbox manifest (`doc_id` → path + SHA-256), `list_documents`, `convert_document(doc_id, pages?)`, `get_document_metadata`
- [ ] MarkItDown conversion, OCR fallback when there's no text layer, output cap with paging, cache by file hash
- [ ] Allowed file types, maximum size, maximum page count; raw paths and URIs are never accepted
- [ ] `agent/extract.py`: Markdown → Ollama (`format` = Invoice JSON schema) → Pydantic; retry once on failure
- [ ] Measure field-level extraction accuracy; compare MarkItDown with pdfplumber on line-item tables

### Phase 4: Verification core and service
- [ ] `agent/llm.py`: Ollama wrapper (`num_ctx` 8k–16k, `temperature` 0, tools, `format`)
- [ ] `agent/loop.py`: MCP ↔ Ollama tool loop, step limit about 8, invoice text wrapped as untrusted data
- [ ] `agent/checks.py`: fixed checks (vendor, duplicate, PO match, arithmetic)
- [ ] `agent/service.py`: real implementation of `verify()` emitting every event type in section 1
- [ ] Record each run's events to `data/runs/<doc_id>.jsonl` (powers the UI's replay mode)
- [ ] **Milestone M2:** one invoice end to end, live in the UI

### Phase 5: Top-pick servers ★
- [ ] `tax_rules`: `validate_gstin` (check digit, state code), `get_gst_rate`, `expected_tax_split`
- [ ] `anomaly_stats`: `vendor_amount_profile`, `benford_score`, `split_invoice_check`; aggregates only, minimum group size about 5
- [ ] `einvoice_qr`: render with pypdfium2 → decode with pyzbar/OpenCV → read the JWT claims → `compare_qr_to_invoice`
- [ ] `review_cases`: `create_review_case`, `add_case_note`, `export_evidence_pack` (PDF + XLSX); append-only
- [ ] Add their checks to `checks.py`, and emit `check_result` events for each

### Phase 6: Remaining servers
- [ ] `vendor_checks`: `screen_sanctions` (rapidfuzz), `validate_ifsc`, `vendor_employee_overlap`
- [ ] `fx_rates`: `get_rate`, `convert`
- [ ] `calendar`: `is_business_day`, `due_date`, `early_payment_discount`
- [ ] `contracts`: Chroma index (nomic-embed-text), `get_contract_terms`, `search_policy`
- [ ] `bank_statements`: `find_bank_line`
- [ ] `fingerprints`: `similar_documents` (imagehash + MinHash)

### Phase 7: Roles and more of MCP
- [ ] `agent/roles.py`: extractor / verifier / reporter allowlists (LLM sees 8 tools or fewer)
- [ ] MCP resources: `policy://verification-rules`, `policy://approval-matrix`, `schema://invoice`
- [ ] MCP prompts for extraction and verification
- [ ] Elicitation: server asks a question → `elicitation` event → `answer_elicitation()` → answer recorded in the audit log

### Phase 8: Eval
- [ ] `eval/run_eval.py`: batch run, precision and recall per error type, confusion matrix, latency, tool calls per invoice
- [ ] `run_eval()` in the service streams `EvalProgress` for the dashboard
- [ ] Compare models: qwen3:8b against gpt-oss:20b and Qwen3-30B-A3B; save each run as an `EvalReport`
- [ ] Tune prompts and checks based on the failing error types

### Stretch [TECH]
- [ ] Standalone MCP proxy gateway (fastmcp mount/proxy)
- [ ] Injection classifier for invoice text
- [ ] Refresh script for real sanctions, IFSC and FX data

---

## 3. UI: NiceGUI app

### U0: Design first
- [ ] Wireframes for every page (Excalidraw, Figma or paper) before writing code. Pages: Inbox, Live verification, Verdict, Review queue, Audit log, Eval dashboard, Demo
- [ ] Design tokens in `ui/theme.py`: colours (verdict and severity colours agreed in kickoff), fonts, spacing, light and dark mode
- [ ] App shell: left nav, header with a **"Runs 100% locally"** badge and a system status chip (Ollama up, model name, servers up, from `system_status()`)
- [ ] Product name, logo and favicon
- [ ] Status is never shown by colour alone: always add an icon or label (accessibility, and readable on a projector)

### U1: Mock-first setup
- [ ] `ui/main.py` running `ui.run()` with page routing
- [ ] `ui/mock_service.py` and fixtures (see section 1), with an `INVOICE_SERVICE` env-var switch
- [ ] **Milestone M1:** every page clickable on mock data

### U2: Inbox
- [ ] Drag-and-drop upload (`ui.upload`), multiple files at once, file type and size checks with friendly errors
- [ ] Document table (AG Grid): file name, vendor, amount, date, status chip, verdict badge, risk score
- [ ] Filters (verdict, status, vendor) and search
- [ ] "Verify" on a row, and "Verify selected" for a small batch

### U3: Live verification view (the main demo screen)
- [ ] Stage stepper: Read document → Extract → Checks → Agent → Verdict → Report
- [ ] Live checklist that fills in from `check_result` events, grouped by server (tax, vendor, anomaly, QR…), each check showing pass/fail/skipped and its detail
- [ ] Live audit feed from `tool_call` events: role colour, server, tool, rows returned, latency; refused calls highlighted
- [ ] Agent reasoning panel (`llm_note` events)
- [ ] Elicitation dialog: question, options, free-text answer → `answer_elicitation()`
- [ ] Elapsed timer, cancel button, error state with a retry button

### U4: Verdict page
- [ ] Split view: invoice preview on the left (PDF pages rendered to images), extracted fields on the right; low-confidence fields marked
- [ ] Big verdict badge, confidence meter, risk flags
- [ ] Discrepancy table: field / expected / found / severity
- [ ] Evidence transactions table (masked values, exactly as the agent saw them)
- [ ] QR comparison panel: QR value next to printed value, mismatches highlighted
- [ ] "Open review case" button
- [ ] Stretch: highlight the mismatched fields directly on the invoice image

### U5: Review queue
- [ ] Case list sorted by risk, with filters by status and error type
- [ ] Case detail: verdict summary, evidence, notes timeline
- [ ] Approve / Reject / Escalate, **reason required**, which calls `set_case_status()`; this is the only place a final status is set
- [ ] Add a note
- [ ] "Export evidence pack" (PDF / XLSX) → download

### U6: Audit log explorer ("what the AI saw")
- [ ] Filterable table: invoice, role, server, tool, refused yes/no
- [ ] Timeline for each invoice: every tool call in order, with the masked results
- [ ] Summary per role: tools allowed vs tools used, budget used
- [ ] This page backs the "the AI never sees the full database" pitch, so make it clear

### U7: Eval dashboard
- [ ] Model selector + "Run eval" button + progress bar (`run_eval()` stream)
- [ ] Headline numbers: overall accuracy, fraud recall, invoices per minute, average tool calls
- [ ] Confusion matrix (ECharts heatmap)
- [ ] Precision and recall per error type (bar chart)
- [ ] Model comparison table across eval runs
- [ ] Click into the failing invoices → verdict page

### U8: Demo mode and polish
- [ ] Demo page: one button per demo-script step (idea.md §9), each loading the right invoice and starting verification
- [ ] **Replay mode:** play back a recorded `data/runs/*.jsonl` event stream at real speed, so the demo still works if the live model is slow or fails
- [ ] Empty, loading and error states on every page
- [ ] Check the layout at 1366×768 and 1920×1080 (projector sizes)
- [ ] Keyboard shortcuts for the demo (next scenario, toggle replay)
- [ ] Use the same wording for verdicts, error types and severities everywhere

### Stretch [UI]
- [ ] Vendor risk page (risk score over time, flags per vendor)
- [ ] Settings page (model choice, query budgets per role), shown read-only to explain the governance setup
- [ ] Architecture page with an interactive diagram of agents → gateway → servers

---

## 4. Pitch and demo [BOTH]

- [ ] [UI] Slide deck and architecture diagram (visuals); [TECH] supplies technical content and numbers
- [ ] [TECH] Final eval numbers and the model comparison table
- [ ] [BOTH] Demo script rehearsal (idea.md §9), at least twice, timed
- [ ] [TECH] Record replay files for every demo scenario
- [ ] [UI] Record a backup screen video of the full demo
- [ ] [BOTH] Prepare answers: "What can the AI see?", "Why local?", "How does this scale to SAP?", "What about prompt injection?"

---

## 5. Integration milestones [BOTH]

| Milestone | TECH has | UI has | Check together |
|---|---|---|---|
| **M0: Contract** | `schemas.py`, `service.py` stub, sample event fixtures | Wireframes, theme | Field names and events cover every screen |
| **M1: Skeletons** | Data generated; transaction server + gateway working in Inspector | All pages clickable on mock data | Mock events look like what the backend will send |
| **M2: First real invoice** | `verify()` working end to end for core checks | Live view + verdict page | Switch to `INVOICE_SERVICE=real`, run 1 invoice |
| **M3: Top picks** | Tax, anomaly, QR and review-case servers | Review queue + audit log explorer | Demo steps 1–8 work live |
| **M4: Eval** | Batch eval + model comparison | Eval dashboard + evidence-pack export | Full dataset runs; numbers show in the dashboard |
| **M5: Freeze** | Replay recordings, bug fixes only | Demo mode, replay, polish | Two full rehearsals, backup video recorded |

---

## 6. Definition of done (for the demo)

- [ ] All demo scenarios run live from the Demo page, with replay as the fallback
- [ ] Every verdict shows its evidence, the checks run and the audit trail
- [ ] No agent can call a tool outside its allowlist (refused calls are visible in the audit log)
- [ ] Eval numbers are produced on the full dataset and shown in the dashboard
- [ ] Runs on one 8GB-VRAM laptop with no internet connection
