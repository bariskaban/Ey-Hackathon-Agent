# Invoice Verification Agent (local, MCP-gated)

An AI agent that receives an invoice and checks it against a company's transaction records. The records sit behind an **MCP server**, so the agent never sees the full database. It can only call a small set of narrow, read-only tools that hide sensitive fields and log every call.

The whole thing runs **locally** on a machine with 8GB VRAM and 32GB RAM. No cloud model is used, so client financial data never leaves the machine.

---

## 1. Goals

- **Input:** an invoice as a PDF or image (later also XML or JSON).
- **Output:** a verdict with evidence:
  - `MATCHED` / `PARTIAL_MATCH` / `MISMATCH` / `NOT_FOUND` / `NEEDS_REVIEW`
  - a list of discrepancies
  - the IDs of the transactions used as evidence
  - a confidence score and the reasoning
- **Constraint:** the agent only reaches data through MCP tools. There is no raw SQL and no bulk export.
- **Demo goal:** catch the errors and fraud planted in a synthetic dataset, report accuracy for each error type, and show the full audit trail of what the agent looked at.

---

## 2. Architecture

```
 Invoice (PDF/image) → dropped into data/inbox/, gets a doc_id
        │
        ▼
 Agents (local LLM via Ollama), each with its own tool allowlist (section 5d)
   ├─ Extractor  → Markdown → Invoice JSON (Pydantic-validated)
   ├─ Verifier   → fixed checks (run by code) + LLM for ambiguity → verdict
   └─ Reporter   → review case + evidence pack
        │
        │  every tool call goes through
        ▼
 MCP Gateway: allowlist per role · query budgets · one audit log
        │
        ├─ documents       (MarkItDown + OCR)              → section 5b
        ├─ transactions    (vendors, POs, receipts, txns)  → section 5
        ├─ tax_rules       (GSTIN, HSN/SAC rates, split)   ┐
        ├─ vendor_checks   (sanctions, IFSC, overlap)      │
        ├─ anomaly_stats   (scores only, never rows)       │
        ├─ einvoice_qr     (decode QR, compare to PDF)     │
        ├─ fx_rates        (local reference rates)         │
        ├─ contracts       (local search, Chroma)          │ section 5c
        ├─ calendar        (business days, terms)          │
        ├─ bank_statements (MT940 / CAMT.053)              │
        ├─ fingerprints    (near-duplicate documents)      │
        └─ review_cases    (the only write access)         ┘
        │
        ▼
 NiceGUI web app: inbox, live verification, verdict, review queue, audit log, batch eval
```

### Why a guided agent instead of a free agent

Small local models (~8B) are unreliable at long, open-ended tool use. So:

- **Code** runs the required checks by calling the MCP tools itself: vendor lookup, duplicate check, PO match and arithmetic, plus the tax, vendor, anomaly, QR, FX, calendar, bank and fingerprint checks from section 5c.
- **The LLM** gets those results, can make a few extra MCP queries when something is ambiguous (step limit ≈ 8), and writes the final verdict as schema-constrained JSON.
- Arithmetic and rules never depend on the model.

---

## 3. Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| LLM runtime | **Ollama** (OpenAI-compatible API, tool calling, JSON-schema `format`) |
| Agent model | **Qwen3 8B, 4-bit (Q4_K_M)** (~5GB, runs entirely on the GPU) |
| Stronger, slower option | **gpt-oss-20b** or **Qwen3-30B-A3B** (MoE models, partly in system RAM) |
| Vision (optional) | Qwen2.5-VL 7B / Qwen3-VL 8B for scanned invoices |
| Document → Markdown | **Microsoft MarkItDown** (`markitdown[pdf,docx,xlsx]`), wrapped in our own MCP server |
| OCR fallback | `pytesseract` or PaddleOCR (scanned PDFs, photos) |
| MCP | `mcp` Python SDK (FastMCP servers + stdio clients); 12 small servers behind one gateway (section 5d) |
| Local search | `nomic-embed-text` (Ollama) + Chroma |
| QR codes | `pypdfium2` to render pages, `pyzbar` or OpenCV to decode, `PyJWT` to read and verify |
| Near-duplicates | `imagehash`, `datasketch` (MinHash) |
| Fuzzy matching | `rapidfuzz` |
| Bank statements | `mt-940` (MT940), `lxml` (CAMT.053) |
| Calendar | `holidays` package (offline) |
| Reports | `openpyxl`, `reportlab` |
| Dev tools | MCP Inspector (`npx @modelcontextprotocol/inspector`) |
| DB | SQLite (MVP) → Postgres |
| Validation | Pydantic |
| Synthetic data | Faker + a custom generator, `reportlab` for invoice PDFs |
| UI | **NiceGUI**: pure Python, runs on FastAPI, pushes live updates over websockets. It has built-in upload, AG Grid tables and ECharts charts, and needs no Node build step. Reflex is the alternative if we want a React-style app. |
| Eval | pytest + a custom scoring script |

### Local model settings

- Set **`num_ctx` explicitly** (8k–16k). Ollama's default is small, and it silently cuts off tool definitions and results.
- Use `temperature: 0` for both extraction and verification.
- Constrain outputs with Ollama's `format` set to a JSON schema, validate with Pydantic, and retry once if validation fails.
- Only one model fits in VRAM at a time, so **process in stages**: extract all invoices first, then verify them all. That avoids swapping models back and forth.

---

## 4. Data model (synthetic)

```
vendors(id, name, tax_id/GSTIN, address, bank_account, bank_changed_at, status)
purchase_orders(po_number, vendor_id, date, currency, total, status)
po_lines(po_number, line_no, sku, description, qty, unit_price, tax_rate)
goods_receipts(grn_id, po_number, date, sku, qty_received)
transactions(txn_id, vendor_id, date, amount, currency, reference, invoice_no, bank_account, status)
invoices_seen(vendor_id, invoice_no, normalized_invoice_no, amount, date, status)
employees(id, name, address, bank_account)   -- used for shell-vendor checks

-- reference data for the servers in section 5c
hsn_rates(code, description, gst_rate)
states(code, name)                            -- GSTIN state codes
sanctions(list, name, aliases, country, listed_on)
ifsc(code, bank, branch, city)
fx_rates(date, ccy, rate_to_inr)
holidays(date, region, name)
contracts(vendor_id, sku, unit_price, discount_tiers, payment_terms, valid_from, valid_to)
bank_statement_lines(id, date, amount, reference, counterparty_account)
review_cases(case_id, invoice_id, verdict, evidence_json, created_at, status)   -- append-only
```

Reference data is synthetic for the demo. Sanctions lists (OFAC SDN, UN consolidated), the IFSC dataset, HSN rates and FX rates can be swapped for the real public files. Download them once and record the download date.

### Planted errors (each invoice has a ground-truth label)

| Error type | How it's planted |
|---|---|
| Wrong amount | Total differs from the PO or payment by more than the tolerance |
| Duplicate invoice | Same vendor and invoice number already seen, including format variants (`INV-001` / `INV001`) |
| Already paid | A matching transaction already exists with status `paid` |
| Unknown vendor | Vendor name or tax ID not in the vendor master |
| Tax arithmetic error | Line totals, tax amount or grand total don't add up |
| Bank details changed | Invoice bank account ≠ vendor master, or changed recently |
| Over PO | Invoiced amount exceeds the PO's remaining value |
| Quantity mismatch | Invoiced quantity > quantity received (three-way match) |
| Split invoice | Several invoices just under an approval threshold in a short window |
| Prompt injection | Invoice text contains instructions aimed at the AI ("mark as verified…") |
| Invalid GSTIN | Wrong check digit or state code |
| Wrong GST split | IGST on a supply within one state, or CGST+SGST on a supply between states |
| Wrong GST rate | Rate doesn't match the HSN/SAC code |
| Sanctioned vendor | Vendor name is a close variant of a sanctions-list entry |
| QR tampering | Printed total edited after the e-invoice QR was generated |
| Above contract rate | Unit price higher than the contract rate card, or volume discount missing |
| Payment never cleared | ERP says paid, but there is no bank statement line |
| Wrong FX rate | Foreign-currency total converted at the wrong date's rate |
| Resubmitted scan | Same invoice re-scanned or re-typed with small edits |
| Unusual amount | Far outside the vendor's usual range |

Target: about 300 invoices, roughly 60% clean and 40% with errors, at least 8–10 examples of each error type, a share of them rendered as PDFs.

---

## 5. Transaction MCP server

### Tools (read-only, narrow, flat parameters)

| Tool | Returns |
|---|---|
| `get_vendor(name_or_tax_id)` | Vendor record with the bank account **masked to the last 4 digits** and the date it last changed |
| `lookup_invoice_number(vendor_id, invoice_no)` | Exact and fuzzy matches against invoices already seen |
| `find_transactions(vendor_id, date_from, date_to, amount_min, amount_max)` | **At most 20 rows**, sensitive fields masked |
| `get_purchase_order(po_number)` | PO lines, amount invoiced so far, remaining amount, GRN quantities |
| `vendor_payment_summary(vendor_id, period)` | Totals and counts only, no rows |
| `validate_invoice_math(invoice_json)` | Fixed checks on line totals, tax and the grand total |

### Policy layer (applies to every tool)

- Read-only DB user, parameterized queries only.
- Caps on rows returned, plus pagination.
- Field masking for bank accounts, personal data and employee data.
- **Query budget** per invoice verification. Calls over the budget are refused.
- **Audit log** (JSONL): timestamp, invoice ID, tool, arguments, number of rows returned, latency. The UI shows this live.

---

## 5b. Document conversion MCP server (MarkItDown)

A **second, separate MCP server** that turns invoice files into Markdown. The model, or the extraction step, sends a document and gets Markdown back. It is kept apart from the transaction server, so document handling and financial data stay in separate places.

### Why wrap MarkItDown instead of using `markitdown-mcp` as is

Microsoft's official `markitdown-mcp` exposes `convert_to_markdown(uri)`, which accepts **any** `file:`, `http(s):` or `data:` URI. If the model chose the URI, it could read any file on the machine (`file:///C:/Users/...`) or make the server fetch arbitrary URLs. That is unacceptable for client data. So we write a thin FastMCP server that calls the `markitdown` Python library and only accepts IDs of documents that are already in the inbox.

### Tools

| Tool | Input | Returns |
|---|---|---|
| `list_documents()` | none | Pending docs: `doc_id`, filename, type, size, page count |
| `convert_document(doc_id, pages?)` | ID from the inbox; optional page range | `{doc_id, markdown, method: "markitdown" \| "ocr", char_count, truncated, warnings[]}` |
| `get_document_metadata(doc_id)` | ID | File hash (SHA-256), MIME type, pages, whether a text layer exists |

### How it works

1. Files land in `data/inbox/`. A manifest maps `doc_id` (a UUID) to a file path and its SHA-256 hash. The model never sees file paths.
2. `convert_document`:
   - **Text PDFs, DOCX, XLSX, HTML** → `MarkItDown().convert(path)` → Markdown.
   - **Scanned PDFs and images (PNG, JPG)** → MarkItDown alone extracts little or no text from these, so the server falls back to OCR (Tesseract or PaddleOCR) and sets `method: "ocr"`. MarkItDown's `llm_client` option can also point at a **local Ollama vision model**, through Ollama's OpenAI-compatible endpoint, to describe images.
   - **Never** use MarkItDown's Azure Document Intelligence option, because it is a cloud service.
3. Results are cached by file hash, so converting the same file again is instant.

### Guardrails

- Allowed file types only: pdf, png, jpg, jpeg, tiff, docx, xlsx, html. There is a maximum file size and a maximum page count.
- Output is capped at about 6–8k characters for each call, with a `truncated` flag and `pages` for paging through the rest. Without the cap, one large PDF would overflow the local model's context.
- The returned Markdown is **untrusted data**. The host wraps it in `<document>` delimiters before it reaches the LLM, which is a prompt-injection defence.
- Every conversion is written to the same audit log: `doc_id`, method, hash, character count, latency.
- MarkItDown flattens PDF tables into plain text. If line items come out garbled, use `pdfplumber` table extraction for those pages instead.

### Where it is used

- **Extraction step:** the orchestrator calls `convert_document` and feeds the Markdown to the LLM, which returns `Invoice` JSON.
- **Inside the agent loop:** the agent can also call it directly, for example to re-read a specific page, or to open a supporting attachment such as a delivery note or credit note that came with the invoice.

---

## 5c. More MCP servers (all offline)

Each one is a small FastMCP server with two or three narrow tools, its own local data and no network access. Most of them are called **by code** as part of the fixed checks. The LLM only gets the tools its role allows (section 5d).

**Top picks if time is short:** `tax_rules`, `anomaly_stats`, `review_cases`, `einvoice_qr`.

### tax_rules (GST) ★
- **Tools:**
  - `validate_gstin(gstin)` → valid or not, state code, embedded PAN, reason
  - `get_gst_rate(hsn_or_sac)` → rate and description
  - `expected_tax_split(supplier_gstin, buyer_gstin, taxable_amount, rate)` → expected IGST, or CGST+SGST, amounts
- **How:** a GSTIN is 15 characters: a 2-digit state code, a 10-character PAN, an entity number, `Z`, and a check digit. Same state for supplier and buyer → CGST+SGST. Different states → IGST.
- **Data:** HSN/SAC rate table and state-code table (CSV).
- **Catches:** invalid GSTINs, wrong rate for the item, wrong tax split, tax arithmetic errors.

### vendor_checks
- **Tools:**
  - `screen_sanctions(name, country?)` → fuzzy matches with scores (`rapidfuzz`)
  - `validate_ifsc(code)` → bank and branch, so the bank name on the invoice can be compared
  - `vendor_employee_overlap(vendor_id)` → match or no match, and which field matched (address, bank account, phone). **Employee details are never returned.**
- **Data:** sanctions lists (OFAC SDN, UN consolidated) and an IFSC dataset, downloaded once.
- **Catches:** sanctioned vendors, made-up bank branches, shell companies.
- Sanctions matches only ever send an invoice to review. They never reject it automatically.

### anomaly_stats ★
- **Tools:**
  - `vendor_amount_profile(vendor_id, amount)` → typical range, z-score, percentile
  - `benford_score(vendor_id)` → first-digit deviation and a conformity label, or "insufficient data" below about 50 invoices
  - `split_invoice_check(vendor_id, window_days, threshold)` → count and total of invoices just under the threshold
- **Rule:** returns aggregates only, with a minimum group size of about 5, so no statistic can reveal a single transaction.
- **Catches:** unusual amounts, made-up amounts, invoices split to stay under approval limits.
- **Demo point:** the agent learns what is unusual without ever seeing the payment history.

### einvoice_qr ★
- **Tools:**
  - `decode_einvoice_qr(doc_id)` → claims from the QR: supplier and buyer GSTIN, document number and date, total value, IRN, item count, main HSN code
  - `compare_qr_to_invoice(doc_id, invoice_json)` → differences, field by field
- **How:** render the page with `pypdfium2`, decode with `pyzbar` or OpenCV, and read the signed JWT payload with `PyJWT`.
  - Checking the signature needs the official portal's public key. Without it, report "signature not verified" and still compare the fields.
  - The synthetic data generator signs QRs with our own test key, so the demo can show both a valid and a tampered invoice.
  - Use `pypdfium2` rather than PyMuPDF, which is AGPL-licensed.
- **Catches:** edited PDFs where the printed values don't match the QR.

### fx_rates
- **Tools:**
  - `get_rate(ccy_from, ccy_to, date)`
  - `convert(amount, ccy_from, ccy_to, date)`

  Both fall back to the previous business day when there is no rate for the date.
- **Data:** historical ECB or RBI reference rates (CSV).
- **Catches:** foreign-currency invoices converted at the wrong date's rate.

### contracts
- **Tools:**
  - `get_contract_terms(vendor_id)` → rate card, discount tiers, payment terms, validity dates
  - `search_policy(query, k=3)` → top passages, each with its source document and section
- **How:** contracts and the AP policy → MarkItDown → chunks → `nomic-embed-text` in Ollama → local Chroma. The embedding model is small (about 300MB) and fits alongside the chat model.
- **Option:** use Chroma's official MCP server for the search, but only expose the contracts collection.
- **Catches:** prices above the contract rate, missed volume discounts, expired contracts, breaches of the approval matrix.

### calendar
- **Tools:**
  - `is_business_day(date, region)`
  - `due_date(invoice_date, terms)`
  - `early_payment_discount(invoice_date, terms, pay_date)`
- **Data:** the `holidays` package or a holiday CSV.
- **Catches:** invoices dated on weekends or holidays, late-payment penalties, missed early-payment discounts.

### bank_statements
- **Tools:** `find_bank_line(amount, date_from, date_to, reference?)` → at most 5 matching statement lines, with accounts masked.
- **Data:** synthetic MT940 files (parsed with `mt-940`) and CAMT.053 XML (parsed with `lxml`).
- **Catches:** payments marked "paid" in the ERP that never cleared, and amounts that differ from the bank's.

### fingerprints
- **Tools:** `similar_documents(doc_id, threshold)` → IDs of similar documents, with image and text similarity scores.
- **How:** a perceptual hash of the rendered page (`imagehash`) plus a MinHash of the text (`datasketch`).
- **Catches:** the same invoice resubmitted as a new scan, or re-typed with small edits.

### review_cases (the only write access) ★
- **Tools:**
  - `create_review_case(invoice_id, verdict, evidence)` → `case_id`
  - `add_case_note(case_id, note)`
  - `export_evidence_pack(case_id)` → PDF or XLSX in `data/outbox/`

  The evidence pack contains the invoice, the extracted fields, the checks run, the tool calls from the audit log and the verdict.
- **Rules:** append-only, with no update or delete. Only a human sets the final status, in the UI; the agent cannot approve or close a case.
- **Demo point:** every other server is read-only, so write access is confined to this one place.

---

## 5d. MCP gateway and agent roles

### Gateway

For the hackathon the gateway lives in the host process. Every tool call goes through `gateway.call(role, tool, args)`, which:
1. checks the role's allowlist
2. checks and reduces the invoice's query budget
3. routes the call to the right MCP session
4. writes one audit-log entry: role, server, tool, arguments, result size, latency

Servers still mask their own sensitive fields, so a mistake in the gateway doesn't expose data.

**Later:** turn the gateway into a standalone MCP proxy server. The standalone `fastmcp` package can mount and proxy other servers, so any MCP client would get the same rules.

### Agent roles (least privilege)

| Role | Servers | Tools the LLM sees |
|---|---|---|
| Extractor | documents, einvoice_qr | `convert_document`, `get_document_metadata`, `decode_einvoice_qr` |
| Verifier | All read-only servers. Most run as fixed checks in code. | About 6: `find_transactions`, `get_purchase_order`, `get_contract_terms`, `search_policy`, `find_bank_line`, `convert_document` |
| Reporter | review_cases | `create_review_case`, `add_case_note`, `export_evidence_pack` |

An 8B model gets confused by 30+ tools. Keep each LLM call to **8 tools or fewer**, and run everything else as fixed checks.

---

## 5e. Using more of MCP than tools

- **Resources:** read-only context the host can attach to a prompt, such as `policy://verification-rules`, `policy://approval-matrix` and `schema://invoice`.
- **Prompts:** the extraction and verification prompts are served as MCP prompts, so they are versioned along with the servers.
- **Elicitation:** when a check can't decide, the server asks the human reviewer through the host. Example: "Is 'Acme Pvt Ltd' the same vendor as 'ACME Private Limited'?" Our host has to support this, and each answer is recorded in the audit log.

---

## 5f. Ready-made servers and dev tools

- **MCP Inspector** (`npx @modelcontextprotocol/inspector`): call and debug every tool without the LLM. Use it from day one.
- **Chroma or Qdrant official MCP servers:** an optional replacement for the contract search in `contracts`.
- **Reference Filesystem server limited to `data/outbox/`:** an alternative way to write reports, if exports move out of `review_cases`.
- **Avoid general-purpose SQL MCP servers for the agents.** They undo the "no raw queries" design, so keep them for development only.
- **Launcher script:** starts all servers in one go, because 12 processes are too many to start by hand.

---

## 6. Agent loop (Ollama ↔ MCP)

Ollama doesn't speak MCP, so the Python host connects the two (~100 lines):

1. Start one MCP client session per server over stdio, call `list_tools` on each, and remember which server each tool belongs to.
2. Build each role's tool list from its allowlist (section 5d) and convert it to OpenAI-style `tools`. Every `tool_call` goes through the gateway, which routes it to the right session.
3. Call Ollama's chat API with the system prompt, the invoice JSON, the results of the fixed checks, and the tools.
4. For each `tool_call` → `session.call_tool(...)` → append the result.
5. Stop on a final answer or when the step limit is reached.
6. Make a final call with `format` set to the `Verdict` JSON schema → validate with Pydantic.

The invoice text is **untrusted data**. Wrap it in delimiters, tell the model it is data only, and give the agent no write tools.

### Output schema

```json
{
  "invoice_id": "string",
  "verdict": "MATCHED | PARTIAL_MATCH | MISMATCH | NOT_FOUND | NEEDS_REVIEW",
  "discrepancies": [{"type": "string", "field": "string", "expected": "any", "found": "any", "severity": "low|medium|high"}],
  "evidence_transaction_ids": ["string"],
  "risk_flags": ["string"],
  "checks": [{"name": "string", "server": "string", "status": "pass|fail|skipped", "detail": "string"}],
  "review_case_id": "string | null",
  "confidence": 0.0,
  "reasoning": "string"
}
```

---

## 7. Project layout

```
ey_hackathon_agent/
├─ idea.md
├─ pyproject.toml
├─ data/
│  ├─ generate.py          # synthetic DB + invoices + labels
│  ├─ render_pdfs.py
│  ├─ db.sqlite
│  ├─ inbox/               # documents waiting to be processed (+ manifest.json)
│  ├─ invoices/            # PDFs + ground_truth.json
│  ├─ reference/           # HSN rates, sanctions, IFSC, FX, holidays, contracts, bank statements
│  └─ outbox/              # evidence packs
├─ servers/
│  ├─ common/              # masking, row caps, audit helpers shared by all servers
│  ├─ documents/           # MarkItDown + OCR fallback, cache by file hash
│  ├─ transactions/
│  ├─ tax_rules/
│  ├─ vendor_checks/
│  ├─ anomaly_stats/
│  ├─ einvoice_qr/
│  ├─ fx_rates/
│  ├─ contracts/           # Chroma index lives here
│  ├─ calendar/
│  ├─ bank_statements/
│  ├─ fingerprints/
│  └─ review_cases/
├─ agent/
│  ├─ gateway.py           # allowlists per role, budgets, routing, audit log
│  ├─ roles.py             # extractor / verifier / reporter tool allowlists
│  ├─ extract.py           # Markdown (via documents server) → Invoice JSON
│  ├─ checks.py            # fixed checks
│  ├─ llm.py               # Ollama client wrapper
│  ├─ loop.py              # MCP ↔ Ollama tool loop
│  ├─ service.py           # the only interface the UI calls (see todo.md)
│  └─ schemas.py           # Pydantic: Invoice, Verdict, Event, ReviewCase, EvalReport
├─ scripts/
│  └─ start_servers.py     # launches all MCP servers
├─ eval/
│  └─ run_eval.py          # batch run + metrics per error type
└─ ui/
   ├─ main.py              # NiceGUI entry point (ui.run)
   ├─ theme.py             # colours, fonts, shared components
   ├─ pages/               # inbox, verify, verdict, review, audit, eval
   ├─ components/          # check stepper, PDF viewer, discrepancy table, audit feed
   └─ mock_service.py      # fake service with the same interface, for UI work before the backend is ready
```

---

## 8. Build phases

**Phase 1: Data and ground truth**
- Schema, generator, planted errors, labels, PDF rendering.
- Reference data: HSN rates, state codes, sanctions, IFSC, FX rates, holidays, contracts, bank statements.
- Signed e-invoice QR codes, made with our own test key.

**Phase 2: Transaction server and gateway**
- Transaction server tools, masking and row caps.
- Gateway: allowlists per role, budgets, routing, audit log. Build this early, because every server goes through it.
- Launcher script. Test each tool in MCP Inspector, without the LLM.

**Phase 3: Document server and extraction**
- Document MCP server: inbox manifest, MarkItDown conversion, OCR fallback, cache, audit log.
- `convert_document` → Markdown → LLM → `Invoice` JSON.
- Compare MarkItDown against pdfplumber on line-item tables.
- Measure field-level accuracy against the ground truth.

**Phase 4: Verification (core)**
- Fixed checks, then the Ollama ↔ MCP loop, then the verdict.
- End-to-end on 10 invoices.

**Phase 5: Top-pick servers ★**
- `tax_rules`, `anomaly_stats`, `einvoice_qr`, `review_cases` (with the evidence-pack export).
- Add their checks to `checks.py`.

**Phase 6: Remaining servers**
- `vendor_checks`, `fx_rates`, `calendar`, `contracts` (Chroma index), `bank_statements`, `fingerprints`.

**Phase 7: Roles and more of MCP**
- Split into extractor, verifier and reporter, each with its own allowlist.
- MCP resources and prompts, plus elicitation for ambiguous vendor matches.

**Phase 8: Eval and model comparison**
- Run all ~300 invoices.
- Report precision and recall per error type, latency per invoice and tool calls per invoice.
- Compare Qwen3 8B with gpt-oss-20b and Qwen3-30B-A3B.

**Phase 9: UI and demo polish**
- NiceGUI app: inbox and upload, live verification view, verdict page, review queue, audit log explorer, batch dashboard with a confusion matrix.

> If time is short, finish phases 1–5, then go straight to 8 and 9. Phases 6 and 7 become "next steps" in the pitch.

---

## 9. Demo script

1. Upload a **clean invoice** → `MATCHED`, citing the matching transaction ID.
2. Upload a **duplicate invoice with a changed bank account** → flagged as high-risk fraud, with evidence.
3. Upload an invoice whose **arithmetic doesn't add up** → caught by the fixed checks.
4. Upload an **e-invoice with an edited total** → the QR disagrees with the printed value, so the invoice is flagged as tampered.
5. Upload an invoice with **IGST charged on a supply within one state** → the tax rules catch the wrong split.
6. Upload an **unusually large invoice** → the anomaly score flags it, even though the agent never saw a single past payment.
7. Upload a **prompt-injection invoice** → the injection fails; the invoice goes to `NEEDS_REVIEW`.
8. Open the **review case** and export the **evidence pack**, the output an auditor would recognise.
9. Show the **MCP audit log** and role allowlists: each agent only saw what it was allowed to query, with fields masked.
10. Show the **batch eval**: ~300 invoices, accuracy per error type, everything run locally on one 8GB GPU.

---

## 10. Expansion areas (what makes this valuable to EY)

Parts of items 2–5 are now in the plan (sections 5c–5e). The items below describe how they grow beyond the hackathon.

1. **Test every invoice instead of a sample.** Check 100% of invoices instead of sampling, and export an evidence pack for each exception as an audit working paper.
2. **Fraud and anomaly detection.**
   - Fuzzy duplicates
   - Bank-detail changes just before a payment
   - Invoices split to stay under approval limits
   - Benford's-law deviations
   - Invoices dated on weekends or holidays
   - Vendor details that match employee records (shell companies)
3. **Three-way and four-way matching, plus contract compliance.**
   - Match PO, goods receipt and invoice, then add the contract as a fourth source.
   - Add a contracts MCP server with local search over rate cards to catch overbilling (spend recovery).
4. **Tax and regulatory checks.**
   - GST: GSTIN format, rate by HSN/SAC code, IGST vs CGST/SGST by place of supply, e-invoice IRN/QR checks.
   - Then VAT, withholding tax, multiple currencies (an FX-rates MCP tool), and rules per jurisdiction.
5. **Governance through the MCP layer.**
   - Role-based tool access, for example a junior reviewer sees aggregates only.
   - Strict separation between client engagements.
   - Tamper-evident audit logs.
   - Data residency, and a deployment that runs fully on-prem or air-gapped.
6. **Prompt-injection defence.** Treat invoice content as untrusted data, add an injection classifier, give the agent no write tools, and back every verdict with fixed checks.
7. **Connecting to client systems.** Put one MCP server in front of each source (SAP, Oracle, NetSuite, Dynamics, bank feeds). The agent stays the same and only the connectors change.
8. **Scaling locally.**
   - Batch queue running invoices through in stages.
   - A small model for extraction and a larger mixture-of-experts model only for escalations.
   - Cache results by invoice hash.
   - Run on a GPU server shared by an engagement team.
9. **Human review.** A reviewer queue sorted by risk, overrides with a reason, overrides fed back into the rules and the test set, and vendor risk scores over time.
10. **Other document types.** Expense claims and receipts, credit notes, bank reconciliations, intercompany reconciliations, lease and royalty statements.

---

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Small model makes bad tool calls | Guided agent, few flat tools, step limit, schema-constrained output, retry |
| Extraction errors cascade | Validate with Pydantic; low-confidence fields → `NEEDS_REVIEW` |
| Context window too small | Set `num_ctx` explicitly; return compact tool results |
| Slow inference on bigger models | Default to the 8B model; use larger models only for escalations or the eval comparison |
| Model swaps are slow | Process in stages (extract all, then verify all) |
| Prompt injection via invoice | Wrap invoice text as data, no write tools, verdicts backed by fixed checks |
| Document tool reads arbitrary files or URLs | Wrap MarkItDown; accept inbox `doc_id`s only, never raw paths or URIs |
| MarkItDown returns empty text for scans | Detect missing text layer → OCR fallback (`method: "ocr"`) |
| Large PDFs overflow context | Cap output per call; read the rest in page ranges |
| Too many tools for a small model | Allowlists per role; the LLM sees 8 tools or fewer; most servers run as fixed checks in code |
| Reference data goes out of date (sanctions, FX, HSN rates) | Include the download date in every result; add a refresh script |
| QR signature can't be checked without the official key | Report "not verified" and still compare the QR fields with the printed values |
| Fuzzy sanctions matches give false positives | Return the score and the matched entry; send to review, never reject automatically |
| Too many server processes to manage | One launcher script; MCP Inspector for debugging |
