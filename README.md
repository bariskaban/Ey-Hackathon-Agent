# Invoice Verification Agent

A local AI agent that checks an invoice against a company's transaction records and Dutch VAT rules. The whole workflow is English-only: invoices, prompts, tool outputs and the UI. The records sit behind MCP servers, so the agent can only call narrow, read-only tools. Everything runs on your own machine, so no financial data leaves it.

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

### 7. Run the tests

```bash
python -m pytest
```

The tests generate their own temporary dataset, so they never touch `data/db.sqlite`.

### Optional: inspect the MCP servers

```bash
python scripts/start_servers.py
```

This starts every server that exists over HTTP (the transactions server is at `http://127.0.0.1:8102/mcp`). Connect to it with the MCP Inspector (`npx @modelcontextprotocol/inspector`, transport "Streamable HTTP"). The agent does not need this; it starts the servers itself.

## Project files

| File | Purpose |
|---|---|
| `pyproject.toml` | Project metadata and dependencies |
| `.python-version` | Python version used for the virtual environment |
| `.gitignore` | Files kept out of git (virtual environment, local data, secrets) |
