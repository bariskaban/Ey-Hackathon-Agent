# Invoice Verification Agent (local, MCP-gated)

An AI agent that receives an invoice and checks it against a company's transaction records and **Dutch VAT (BTW) rules**. The records sit behind **MCP servers**, so the agent never sees the full database. It can only call a small set of narrow, read-only tools that hide sensitive fields and log every call.

The whole thing runs **locally** on a machine with 8GB VRAM and 32GB RAM. No cloud model is used, so client financial data never leaves the machine.

---

## 1. Goals

- **Input:** an invoice as a PDF or image, in Dutch or English.
- **Output:** a verdict with evidence:
  - `MATCHED` / `PARTIAL_MATCH` / `MISMATCH` / `NOT_FOUND` / `NEEDS_REVIEW`
  - a list of discrepancies
  - the IDs of the transactions used as evidence
  - the checks that were run
  - a confidence score and the reasoning
- **Constraint:** the agent only reaches data through MCP tools. There is no raw SQL and no bulk export.
- **Scope:** **6 MCP servers** and **20 test invoices**, each with a known correct answer.
- **Demo goal:** catch every planted error in the 20 invoices, give the same answer across repeated runs, and show the full audit trail of what the agent looked at.

---

## 2. Architecture

```
 Invoice (PDF/image) → dropped into data/inbox/, gets a doc_id
        │
        ▼
 Agents (local LLM via Ollama), each with its own tool allowlist (section 6)
   ├─ Extractor  → Markdown → Invoice JSON (Pydantic-validated)
   ├─ Verifier   → fixed checks (run by code) + LLM for ambiguity → verdict
   └─ Reporter   → review case + evidence pack
        │
        │  every tool call goes through
        ▼
 MCP Gateway: allowlist per role · query budgets · one audit log
        │
        ├─ 1. documents      MarkItDown + OCR fallback
        ├─ 2. transactions   vendors, POs, goods receipts, payments, invoices seen
        ├─ 3. tax_rules      Dutch VAT: VAT numbers, rates, reverse charge, invoice requirements
        ├─ 4. vendor_checks  IBAN, bank-detail changes, EU sanctions, employee overlap
        ├─ 5. anomaly_stats  amount profile, split invoices, date flags (scores only, never rows)
        └─ 6. review_cases   the only write access: cases + evidence packs
        │
        ▼
 NiceGUI web app: inbox, live verification, verdict, review queue, audit log, eval results
```

### Why a guided agent instead of a free agent

Small local models (~8B) are unreliable at long, open-ended tool use. So:

- **Code** runs the required checks by calling the MCP tools itself: vendor lookup, duplicate check, PO and receipt match, VAT checks, bank details, sanctions and anomaly scores.
- **The LLM** gets those results, can make a few extra MCP queries when something is ambiguous (step limit ≈ 8), and writes the final verdict as schema-constrained JSON.
- Arithmetic and rules never depend on the model.

---

## 3. Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| LLM runtime | **Ollama** (OpenAI-compatible API, tool calling, JSON-schema `format`) |
| Agent model | **Qwen3 8B, 4-bit (Q4_K_M)** (~5GB, runs entirely on the GPU; reads Dutch well) |
| Stronger, slower option | **gpt-oss-20b** or **Qwen3-30B-A3B** (MoE models, partly in system RAM) |
| Document → Markdown | **Microsoft MarkItDown** (`markitdown[pdf,docx,xlsx]`), wrapped in our own MCP server |
| OCR fallback | `pytesseract` (with the Dutch language pack) or PaddleOCR |
| MCP | `mcp` Python SDK (FastMCP servers + stdio clients); 6 servers behind one gateway |
| VAT numbers and IBANs | **`python-stdnum`** (`stdnum.nl.btw`, `stdnum.eu.vat`, `stdnum.iban`): offline format and checksum checks |
| Fuzzy matching | `rapidfuzz` (sanctions screening, duplicate invoice numbers) |
| Dutch holidays | `holidays` package (country `NL`, offline) |
| DB | SQLite |
| Validation | Pydantic |
| Synthetic data | Faker (`nl_NL` locale) + a custom generator, `reportlab` for invoice PDFs |
| Reports | `reportlab` (PDF), `openpyxl` (XLSX) |
| UI | **NiceGUI**: pure Python, runs on FastAPI, pushes live updates over websockets. It has built-in upload, AG Grid tables and ECharts charts, and needs no Node build step. |
| Dev tools | MCP Inspector (`npx @modelcontextprotocol/inspector`) |

### Local model settings

- Set **`num_ctx` explicitly** (8k–16k). Ollama's default is small, and it silently cuts off tool definitions and results.
- Use `temperature: 0` for both extraction and verification.
- Constrain outputs with Ollama's `format` set to a JSON schema, validate with Pydantic, and retry once if validation fails.
- Only one model fits in VRAM at a time, so **process in stages**: extract all invoices first, then verify them all.

---

## 4. Data (synthetic, Dutch)

### Tables

```
vendors(id, name, country, vat_number, kvk_number, address, iban, iban_changed_at, status)
purchase_orders(po_number, vendor_id, date, total_excl_vat, status)
po_lines(po_number, line_no, sku, description, qty, unit_price, vat_category)
goods_receipts(grn_id, po_number, date, sku, qty_received)
transactions(txn_id, vendor_id, date, amount_eur, reference, invoice_no, iban, status)
invoices_seen(vendor_id, invoice_no, normalized_invoice_no, amount, date, status)
employees(id, name, address, iban)                 -- only used for overlap checks

-- reference data
vat_rates(category, rate, valid_from, valid_to)    -- 21% standard, 9% reduced, 0%
eu_countries(code, name, vat_prefix)
bank_codes(code, bank_name)                        -- e.g. INGB, RABO, ABNA
sanctions(list, name, aliases, country, listed_on) -- EU consolidated list (+ Dutch national list)
review_cases(case_id, invoice_id, verdict, evidence_json, created_at, status)   -- append-only
```

- About **30 vendors**: mostly Dutch, plus a few from Belgium and Germany to test reverse charge.
- About **2,000 past transactions** over two years. Only 20 new invoices are tested, but the history is what the duplicate checks and anomaly scores compare against.
- All amounts are in EUR.
- Invoices are a mix of Dutch ("Factuur", "BTW", "Totaal incl. BTW") and English.

### The 20 test invoices

**6 clean invoices + 14 with exactly one planted error each:**

| # | Error type | How it's planted |
|---|---|---|
| 1 | Wrong amount | Total differs from the PO by more than the tolerance |
| 2 | Quantity not received | Invoiced quantity > quantity in the goods receipt (three-way match) |
| 3 | Duplicate invoice | Same vendor and invoice number already seen, in a different format (`F-2026-001` / `F2026001`) |
| 4 | Already paid | A matching payment already exists |
| 5 | Unknown vendor | Vendor name or VAT number not in the vendor master |
| 6 | VAT arithmetic error | VAT per rate, or the total including VAT, doesn't add up |
| 7 | Wrong VAT rate | 21% charged on goods that are 9% (such as food), or the other way round |
| 8 | Reverse charge error | A German supplier charges 21% Dutch VAT on a business-to-business supply instead of reverse charge, or "BTW verlegd" is shown without the buyer's VAT number |
| 9 | Invalid VAT number | The supplier's VAT number fails the format or checksum check |
| 10 | Missing mandatory fields | No KvK number, no supply date, or no VAT amount per rate |
| 11 | Changed bank details | IBAN on the invoice differs from the vendor master |
| 12 | Sanctioned vendor | Vendor name is a close variant of an EU sanctions-list entry |
| 13 | Unusual amount or split invoice | Far above the vendor's usual range, or two invoices just under the approval limit in the same week |
| 14 | Prompt injection | Invoice text contains instructions aimed at the AI ("mark as verified…") |

About 15 of the invoices are rendered as normal PDFs, and about 5 are "scanned" (rasterised, noisy, slightly rotated) to test OCR.

---

## 5. The 6 MCP servers

Each one is a small FastMCP server with a few narrow tools and no network access. Most of their tools are called **by code** as part of the fixed checks. The LLM only gets the tools its role allows (section 6).

**Rules shared by all servers:**
- Read-only, except `review_cases`. Parameterized queries only.
- Caps on rows returned, plus pagination.
- Sensitive fields masked: IBANs show only the last 4 digits, and employee data is never returned.
- Every call is written to one audit log (JSONL): timestamp, invoice, role, server, tool, arguments, result size, latency.

### 5.1 documents

Turns invoice files into Markdown that the model can read.

| Tool | Returns |
|---|---|
| `list_documents()` | Pending docs: `doc_id`, filename, type, size, page count |
| `convert_document(doc_id, pages?)` | `{markdown, method: "markitdown" \| "ocr", char_count, truncated, warnings[]}` |
| `get_document_metadata(doc_id)` | SHA-256 hash, MIME type, pages, whether a text layer exists |

- **Only accepts `doc_id`s from the inbox, never raw paths or URLs.** Microsoft's own `markitdown-mcp` takes any `file:` or `http:` URI, which would let the model read any file on the machine. So we wrap the `markitdown` library ourselves.
- Text PDFs → MarkItDown. Scans and images → OCR with Tesseract's Dutch and English language packs (`method: "ocr"`).
- Output is capped at about 6–8k characters per call, with paging.
- Results are cached by file hash.
- The Markdown is untrusted data. The host wraps it in `<document>` delimiters before it reaches the LLM.
- MarkItDown flattens PDF tables into plain text. If line items come out garbled, use `pdfplumber` table extraction for those pages instead.

### 5.2 transactions

The company's records.

| Tool | Returns |
|---|---|
| `get_vendor(name_or_vat_number)` | Vendor record with the IBAN masked |
| `lookup_invoice_number(vendor_id, invoice_no)` | Exact and fuzzy matches against invoices already seen |
| `find_transactions(vendor_id, date_from, date_to, amount_min, amount_max)` | At most 20 rows, sensitive fields masked |
| `get_purchase_order(po_number)` | PO lines, amount invoiced so far, remaining amount, quantities received |
| `vendor_payment_summary(vendor_id, period)` | Totals and counts only, no rows |

### 5.3 tax_rules (Dutch VAT)

Fixed rules only. No model judgement.

| Tool | What it checks |
|---|---|
| `validate_vat_number(vat_number)` | Dutch VAT numbers (`NL` + 9 digits + `B` + 2 digits), checked with `stdnum.nl.btw`, which covers both the old 11-test and the mod-97 check used for sole-trader VAT IDs since 2020. Other EU numbers get a format and checksum check with `stdnum.eu.vat`. |
| `get_vat_rate(category, date)` | 21% standard, 9% reduced (food, books, medicines…), 0%. Rates live in a table with validity dates, because rates and categories change; check Belastingdienst for current changes. |
| `expected_vat_treatment(supplier_country, buyer_country, buyer_vat_number, supply_type)` | Domestic sale → Dutch VAT charged. Business-to-business supply from another EU country → reverse charge: 0% on the invoice, the text "btw verlegd" or "reverse charge", and both VAT numbers shown. |
| `check_invoice_requirements(invoice_json)` | Belastingdienst's mandatory invoice fields: supplier VAT number, buyer VAT number when reverse charge applies, invoice date, unique sequential number, names and addresses, quantity and description, supply date, amount excluding VAT per rate, VAT rate and VAT amount. Also the KvK number, which the Trade Register rules require on invoices. |
| `validate_invoice_math(invoice_json)` | Line totals, VAT per rate, total including VAT, rounding |

- **Online checks are deliberately left out.** The EU's VIES service can confirm a VAT number is active, and the KvK API can confirm a company exists. Both need the internet, so they're expansion items.

### 5.4 vendor_checks

| Tool | Returns |
|---|---|
| `validate_iban(iban)` | Valid or not (mod-97 via `stdnum.iban`), country, bank code → bank name |
| `compare_bank_details(vendor_id, iban)` | Matches / differs, and whether the vendor master's IBAN changed recently. Shows only the last 4 digits. |
| `screen_sanctions(name, country?)` | Fuzzy matches with scores against the EU consolidated sanctions list (and the Dutch national list). Only ever sends an invoice to review, never rejects it automatically. |
| `vendor_employee_overlap(vendor_id)` | Match or no match, and which field matched (address, IBAN). Employee details are never returned. |

### 5.5 anomaly_stats

Returns **scores, never rows**, with a minimum group size of about 5, so no statistic can reveal a single transaction.

| Tool | Returns |
|---|---|
| `vendor_amount_profile(vendor_id, amount)` | Typical range, z-score, percentile |
| `split_invoice_check(vendor_id, window_days, threshold)` | Count and total of invoices just under the approval limit in the window |
| `date_flags(date)` | Weekend, Dutch public holiday, or a date in the future |

**Demo point:** the agent learns what is unusual without ever seeing the payment history.

### 5.6 review_cases (the only write access)

| Tool | Returns |
|---|---|
| `create_review_case(invoice_id, verdict, evidence)` | `case_id` |
| `add_case_note(case_id, note)` | The updated case |
| `export_evidence_pack(case_id)` | A PDF or XLSX in `data/outbox/`: the invoice, the extracted fields, the checks run, the tool calls from the audit log, and the verdict |

- Append-only, with no update or delete.
- Only a human sets the final status, in the UI. The agent cannot approve or close a case.
- Every other server is read-only, so write access is confined to this one place.

---

## 6. Gateway, agent roles and the agent loop

### Gateway

For the hackathon the gateway lives in the host process. Every tool call goes through `gateway.call(role, tool, args)`, which:
1. checks the role's allowlist
2. checks and reduces the invoice's query budget
3. routes the call to the right MCP session
4. writes the audit-log entry

Servers still mask their own sensitive fields, so a mistake in the gateway doesn't expose data.

### Agent roles (least privilege)

| Role | Servers | Tools the LLM sees |
|---|---|---|
| Extractor | documents | `convert_document`, `get_document_metadata` |
| Verifier | documents, transactions, tax_rules, vendor_checks, anomaly_stats. Most run as fixed checks in code. | 6: `get_vendor`, `lookup_invoice_number`, `find_transactions`, `get_purchase_order`, `vendor_payment_summary`, `convert_document` |
| Reporter | review_cases | `create_review_case`, `add_case_note`, `export_evidence_pack` |

### Agent loop (Ollama ↔ MCP)

Ollama doesn't speak MCP, so the Python host connects the two:

1. Start one MCP client session per server over stdio, call `list_tools` on each, and remember which server each tool belongs to.
2. Build each role's tool list from its allowlist and convert it to OpenAI-style `tools`.
3. Call Ollama's chat API with the system prompt, the invoice JSON, the results of the fixed checks, and the tools.
4. Send each `tool_call` through the gateway, then append the result.
5. Stop on a final answer or when the step limit is reached.
6. Make a final call with `format` set to the `Verdict` JSON schema, then validate with Pydantic.

The invoice text is **untrusted data**. It is wrapped in delimiters, the model is told it is data only, and the agent has no write tools apart from the reporter's.

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

### Stretch: more of MCP than tools

- **Resources:** `policy://verification-rules`, `schema://invoice`, read-only context the host can attach to a prompt.
- **Prompts:** the extraction and verification prompts are served as MCP prompts, so they are versioned along with the servers.
- **Elicitation:** when a check can't decide, a server asks the human through the host. Example: "Is 'Bakkerij de Vries B.V.' the same vendor as 'Bakkerij De Vries BV'?"

---

## 7. Project layout

```
ey_hackathon_agent/
├─ idea.md
├─ todo.md
├─ pyproject.toml
├─ data/
│  ├─ generate.py          # synthetic DB, history, 20 invoices + labels
│  ├─ render_pdfs.py
│  ├─ db.sqlite
│  ├─ reference/           # VAT rates, EU countries, bank codes, sanctions
│  ├─ inbox/               # documents waiting to be processed (+ manifest.json)
│  ├─ invoices/            # the 20 PDFs + ground_truth.json
│  ├─ runs/                # recorded event streams (for replay)
│  └─ outbox/              # evidence packs
├─ servers/
│  ├─ common/              # masking, row caps, audit helpers
│  ├─ documents/
│  ├─ transactions/
│  ├─ tax_rules/
│  ├─ vendor_checks/
│  ├─ anomaly_stats/
│  └─ review_cases/
├─ agent/
│  ├─ gateway.py           # allowlists per role, budgets, routing, audit log
│  ├─ roles.py
│  ├─ extract.py           # Markdown → Invoice JSON
│  ├─ checks.py            # fixed checks
│  ├─ llm.py               # Ollama client wrapper
│  ├─ loop.py              # MCP ↔ Ollama tool loop
│  ├─ service.py           # the only interface the UI calls (see todo.md)
│  └─ schemas.py           # Pydantic: Invoice, Verdict, Event, ReviewCase, EvalReport
├─ scripts/
│  └─ start_servers.py
├─ eval/
│  └─ run_eval.py
└─ ui/
   ├─ main.py              # NiceGUI entry point
   ├─ theme.py
   ├─ pages/
   ├─ components/
   └─ mock_service.py      # fake service with the same interface
```

---

## 8. Build phases

**Phase 1: Data**
- Schema, about 30 vendors, about 2,000 past transactions, reference tables.
- The 20 test invoices with ground truth, rendered as PDFs (some scanned).

**Phase 2: Transaction server and gateway**
- `transactions` server, shared masking and audit helpers, gateway, launcher script.
- Test each tool in MCP Inspector.

**Phase 3: Documents and extraction**
- `documents` server, then extraction to `Invoice` JSON.
- Check field accuracy on all 20 invoices.

**Phase 4: Verification core**
- Fixed checks, the Ollama ↔ MCP loop, the verdict, and `service.py` with streamed events.
- End to end on 3 invoices.

**Phase 5: Rule servers**
- `tax_rules`, `vendor_checks`, `anomaly_stats`, `review_cases`.

**Phase 6: Eval**
- All 20 invoices, 3 runs each.
- Compare Qwen3 8B with gpt-oss-20b and Qwen3-30B-A3B.

**Phase 7: UI and demo polish**
- Replay recordings and rehearsal.

**Stretch:** separate agent roles, MCP resources, prompts and elicitation.

---

## 9. Evaluation (20 invoices)

Twenty invoices are too few for meaningful percentages per error type, so report results per invoice instead:

- **Per invoice:** expected verdict vs actual verdict, whether the planted error was caught, and whether the right check caught it.
- **Consistency:** run each invoice **3 times**. Local models can still vary at temperature 0, and a verdict that flips between runs is a finding in itself.
- **Speed and cost of the agent:** seconds per invoice, tool calls per invoice, how often the step limit was hit.
- **Model comparison:** the same 20 × 3 runs on each model. With only 20 invoices this takes minutes, not hours.
- **Headline for the pitch:** "X of 20 correct, consistent across 3 runs, every planted error caught, fully offline on one 8GB GPU."

---

## 10. Demo script

1. Upload a **clean Dutch invoice** → `MATCHED`, citing the matching PO and payment.
2. Upload a **duplicate invoice with a changed IBAN** → flagged as high-risk fraud, with evidence.
3. Upload an invoice from a **German supplier charging 21% Dutch VAT** → the tax rules say it should have been reverse charged.
4. Upload an invoice with **food charged at 21%** instead of 9% → wrong VAT rate.
5. Upload an **unusually large invoice dated on a Saturday** → the anomaly scores flag it, even though the agent never saw a single past payment.
6. Upload a **prompt-injection invoice** → the injection fails; the invoice goes to `NEEDS_REVIEW`.
7. Open the **review case** and export the **evidence pack**.
8. Show the **audit log**: each agent only saw what it was allowed to query, with fields masked.
9. Show the **eval results**: 20 invoices × 3 runs, all local.

---

## 11. Expansion areas (what makes this valuable to EY)

1. **Test every invoice instead of a sample.** Check 100% of a client's invoices and export an evidence pack for each exception as an audit working paper.
2. **The servers we cut for the hackathon:**
   - **contracts**: local search over rate cards to catch overbilling (spend recovery)
   - **bank_statements**: confirm payments actually cleared (MT940 / CAMT.053)
   - **fingerprints**: catch the same invoice resubmitted as a different scan
   - **fx_rates**: non-EUR invoices
   - **calendar**: payment terms, late penalties, early-payment discounts
3. **More tax coverage.**
   - The other EU countries' VAT rules.
   - Peppol / UBL e-invoices: parse the structured XML directly, with no OCR.
   - EU "VAT in the Digital Age" (ViDA): mandatory e-invoicing and digital reporting for business-to-business sales between EU countries, phased in towards 2030.
4. **Online checks, with approval.** VIES for whether a VAT number is active, and the KvK API for company details. Both run through an MCP server that only calls those specific services.
5. **Governance through the MCP layer.**
   - Role-based access, for example a junior reviewer sees aggregates only.
   - Strict separation between client engagements.
   - Tamper-evident audit logs.
   - Deployment that runs fully on-prem or air-gapped.
   - Make the gateway a standalone MCP proxy that any MCP client must go through.
6. **Fraud analytics.** Benford's-law tests, vendor risk scores over time, and network analysis of shared IBANs and addresses.
7. **Connecting to client systems.** One MCP server in front of each source (SAP, Oracle, Exact Online, AFAS, bank feeds). The agent stays the same and only the connectors change.
8. **Human review.** A reviewer queue sorted by risk, overrides with a reason, and overrides fed back into the rules and the test set.
9. **Other document types.** Expense claims and receipts, credit notes, bank reconciliations, intercompany reconciliations.

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Small model makes bad tool calls | Guided agent, few flat tools, step limit, schema-constrained output, retry |
| Extraction errors cascade | Validate with Pydantic; low-confidence fields → `NEEDS_REVIEW` |
| Dutch-language invoices extract badly | Test Dutch and English invoices separately; use the Dutch OCR language pack |
| Context window too small | Set `num_ctx` explicitly; return compact tool results |
| Prompt injection via invoice | Wrap invoice text as data, no write tools for the verifier, verdicts backed by fixed checks |
| Document tool reads arbitrary files or URLs | Accept inbox `doc_id`s only, never raw paths or URIs |
| VAT rates and rules change | Table-driven rates with validity dates; cite the rule in every result |
| Sanctions fuzzy matches give false positives | Return the score and the matched entry; send to review, never reject automatically |
| 20 invoices is a small test set | Report results per invoice plus consistency over 3 runs; don't claim percentages per error type |
