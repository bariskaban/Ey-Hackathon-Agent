# CLAUDE.md

Guidance for working in this repository. See [README.md](README.md) for setup, how Vera works and the project layout.

## Git workflow

- **Never commit directly to `main`.** Every change is made on a separate branch.
- **Branch naming:** `<name>-<work>`, where `<name>` is the person doing the work and `<work>` is a short description of the change, in lowercase with hyphens (for example `joshua-gateway-allowlists`).
- When the work is done, push the branch, **open a pull request** into `main`, and **merge the PR**. Do not merge branches locally or push straight to `main`.
- Keep each branch and PR focused on one piece of work.

## Dependencies

- **Every new package must be added to `pyproject.toml`** in the same change that starts using it. Put runtime packages under `dependencies` and development-only tools (test, lint) under the `dev` dependency group.
- Do not install a package into the virtual environment without also declaring it in `pyproject.toml`. The environment must be reproducible from `pyproject.toml` alone.
- Use sensible minimum version constraints, as the existing entries do.

## Environment

- Python version is pinned in `.python-version` (3.12; the project requires 3.11 or newer).
- Work inside the `.venv` virtual environment. It is git-ignored; never commit it.
- Do not commit secrets, `.env` files or generated local data. `.gitignore` already covers these.
