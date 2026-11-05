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

## Project files

| File | Purpose |
|---|---|
| `pyproject.toml` | Project metadata and dependencies |
| `.python-version` | Python version used for the virtual environment |
| `.gitignore` | Files kept out of git (virtual environment, local data, secrets) |
