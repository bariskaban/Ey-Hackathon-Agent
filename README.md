# Vera: invoice verification agent

Vera is a local AI agent that checks an invoice against a company's transaction records and Dutch VAT rules. The whole workflow is English-only: invoices, prompts, tool outputs and the UI. The records sit behind MCP servers, so the agent can only call narrow, read-only tools. Everything runs on your own machine, so no financial data leaves it.

See [idea.md](idea.md) for the design and [todo.md](todo.md) for the task split.

## Setup

### Prerequisites

- **Python 3.12** (the project needs 3.11 or newer; `.python-version` pins 3.12)
- **Git**
- **Tesseract OCR** with the English (`eng`) language pack, used as the OCR fallback for scanned invoices
- **Ollama**, for running the local model
- **Node.js** (optional, only for the MCP Inspector)

### 1. Clone the repository

```bash
git clone <repo-url>
cd ey_hackathon_agent
```

### 2. Create the virtual environment

```bash
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

### 3. Install the dependencies

Dependencies are declared in `pyproject.toml`. Install the project with the development tools:

```bash
python -m pip install --upgrade pip
python -m pip install -e . pytest ruff
```

### 4. Pull the local model

```bash
ollama pull qwen3:8b
```

Ollama can run on Windows or inside WSL; the code reaches it at `http://127.0.0.1:11434` either way (set `OLLAMA_HOST` to use another address, and `INVOICE_MODEL` to use another model). If Ollama runs in WSL, keep a WSL terminal open while you work: WSL shuts the distro down when no session is open, which stops Ollama with it.

### 5. Check the install

```bash
python --version
python -c "import mcp, nicegui, pydantic, stdnum; print('ok')"
tesseract --list-langs
```

`tesseract --list-langs` should list `eng`.

### 6. Generate the synthetic data

The company records and the 20 test invoices are generated, not downloaded. The output is identical on every run.

```bash
python data/generate.py
python data/render_pdfs.py
```

- `generate.py` writes `data/db.sqlite` (vendors, purchase orders, receipts, about 2,000 past transactions and reference data), plus `data/invoices/ground_truth.json` and the invoice specs.
- `render_pdfs.py` renders the 20 invoices as PDFs in `data/invoices/`. Five of them are "scanned" (image only, slightly rotated and noisy) to test OCR.

Then put the invoices in the inbox, where documents wait to be processed:

```bash
python scripts/load_inbox.py
```

To have invoices to upload by hand in the interface, make a few samples. They are copies of some test invoices under descriptive names, kept out of the inbox in `samples/`:

```bash
python scripts/make_samples.py
```

To measure how well the model reads the invoices (needs Ollama running):

```bash
python eval/extraction_accuracy.py
```

### 7. Run the tests

```bash
python -m pytest
```

The tests generate their own temporary dataset, so they never touch `data/db.sqlite`.

### 8. Run the evaluation

The evaluation verifies every test invoice several times and compares each result with the known answer. It needs Ollama running and the inbox loaded (step 6).

```bash
python eval/run_eval.py
```

- By default it runs all 20 invoices 3 times each (about 30 minutes) and prints a table: expected against actual verdict, whether the planted error was caught and by which check, whether the runs agreed, and the time and tool calls per run.
- Each result is `pass`, `safe_fail` or `fail`. A `safe_fail` is a scan whose key field could not be read and that was therefore sent to a person instead of being approved or rejected. It is counted separately from a wrong answer.
- Useful options: `--runs 1 --no-agent` for a quick check, `--model <name>` to try another Ollama model, or list invoice ids (for example `INV-004 INV-018`) to run only those.
- Every run is saved in `data/runs/eval/`. Evaluation never opens review cases.

### 9. Run the interface

```bash
python -m ui.main
```

Open <http://localhost:8080>. This uses recorded sample data and needs nothing else. To run the real agent (it needs Ollama running and the inbox loaded, steps 4 and 6):

```bash
# Windows (PowerShell)
$env:INVOICE_SERVICE = "real"; python -m ui.main

# macOS / Linux / Git Bash
INVOICE_SERVICE=real python -m ui.main
```

The real agent starts its six MCP servers when the interface starts. Choose an invoice in the inbox and select "Verify" to watch the checks fill in live, then open its verdict, review case and the "What the AI saw" log. Set `UI_RELOAD=0` to stop the interface restarting itself when files change.

Two pages are not in the menu, for the team only: <http://localhost:8080/demo> (the demo script, with live runs or recordings; keys 1 to 6 start a step, R switches mode) and <http://localhost:8080/eval> (evaluation results).

### Optional: inspect the MCP servers

```bash
python scripts/start_servers.py
```

This starts all six MCP servers over HTTP (documents on port 8101, transactions 8102, tax_rules 8103, vendor_checks 8104, anomaly_stats 8105, review_cases 8106, each at `/mcp`). Connect to it with the MCP Inspector (`npx @modelcontextprotocol/inspector`, transport "Streamable HTTP"). The agent does not need this; it starts the servers itself.

## Project files

| File | Purpose |
|---|---|
| `pyproject.toml` | Project metadata and dependencies |
| `.python-version` | Python version used for the virtual environment |
| `.gitignore` | Files kept out of git (virtual environment, local data, secrets) |
