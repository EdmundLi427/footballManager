# Football Manager

A monorepo for NFL fantasy football data management, comprising four independent services:

## What It Does

**Football Manager** ingests NFL league data from multiple sources, cleans and standardizes it, stores it in Delta Lake, and exposes both a query API (via MCP) and a web chat interface for exploratory analysis.

Data flows through:
1. **ETL** — batch notebooks that clean raw NFL data and output to Delta Lake
2. **Data Service** — MCP server providing read-only SQL access to Delta Lake tables
3. **Web App** — Flask UI with Claude-powered chat for natural-language queries
4. **Azure Worker** — scheduled job that fetches injury news from FantasyPros API and stores to Azure Blob

## Architecture

```
                ┌──────────────────┐
                │    FantasyPros   │
                │      API         │
                └────────┬─────────┘
                         │
                    (6-hourly)
                         │
        ┌────────────────┴────────────────┐
        │                                 │
   ┌────▼────────┐         ┌─────────────▼──────────┐
   │   Raw Data  │         │  Azure Blob Storage    │
   │   (CSVs)    │         │   (Injury News JSON)   │
   └────┬────────┘         └──────────────────────────┘
        │
        │ (ETL notebooks)
        │
   ┌────▼──────────────┐
   │   Delta Lake      │
   │  (cleaned data)   │
   └────┬──────────────┘
        │
        ├─────────────────────────┬──────────────────┐
        │                         │                  │
   ┌────▼─────────────┐    ┌─────▼──────┐    ┌─────▼──────────┐
   │  Data Service    │    │   Web App  │    │   (CLI Chat)   │
   │  (MCP / SQL)     │    │ (Flask UI) │    │   (dev-only)   │
   └──────────────────┘    └────────────┘    └────────────────┘
```

## Modules

| Module | Purpose | Tech Stack |
|--------|---------|-----------|
| **`azure/`** | Scheduled job: fetches injury news every 6 hours from FantasyPros API, stores JSON to Azure Blob Storage | Node.js (v4 Azure Functions), FantasyPros API |
| **`data-service/`** | Query layer: MCP server providing read-only SQL access to Delta Lake tables via DuckDB | Python 3.10+, MCP, Delta Lake, DuckDB |
| **`etl/`** | Batch pipelines: ingests raw NFL data, cleans and standardizes (snake_case, nulls, dedupe), outputs to Delta Lake | Python 3.10+, Jupyter notebooks, Pandas |
| **`webapp/`** | Web chat UI: Flask server with Claude-powered natural-language interface to query NFL data | Python 3.10+, Flask, Anthropic API, Claude |

See each module's `README.md` for setup, development, and architecture details.

## Quick Start

### Prerequisites

- Python 3.10+ (for Python modules: `data-service`, `webapp`, `etl`)
- Node.js 18+ (for Azure Functions: `azure/`)
- `uv` package manager (Python workspace management)

### Setup

```bash
# Install all Python dependencies (workspace root)
uv sync

# Set up environment for webapp/data-service
cp webapp/.env.example webapp/.env
cp data-service/.env.example data-service/.env
cp azure/.env.example azure/.env

# Fill in secrets (API keys, storage credentials)
# ANTHROPIC_API_KEY, AWS_PROFILE/AZURE_STORAGE_ACCOUNT_NAME (for data-service), etc.
```

### Run the Web App

```bash
# Terminal 1: Start the web app
uv run --directory webapp python app.py

# Open http://localhost:5000 in your browser
```

### Run Data Service (standalone)

```bash
# Test the MCP server locally
uv run --with mcp[cli] --directory data-service mcp dev src/data_service/server.py
```

### Run Tests

```bash
# Python modules
uv run --directory data-service pytest
uv run --directory webapp pytest

# ETL validation
python etl/tests/test_clean.py

# Azure functions
cd azure && npm test
```

## Development

### Adding a New Service

1. Create a directory in the repo root with a `pyproject.toml` (or `package.json` for Node.js)
2. Add it to `[tool.uv.workspace] members` in the root `pyproject.toml`
3. Add a module section to `CLAUDE.md`
4. Create `.github/workflows/<module>.yml` for CI

### Python Formatting

`ruff format` is automatically applied to Python files via a post-edit hook. No manual formatting step needed.

## Testing

- **`azure/`** — `npm test` (stub; add real tests for FantasyPros calls and Blob uploads)
- **`data-service/`** — `uv run --directory data-service pytest`
- **`etl/`** — `python etl/tests/test_clean.py` (validates output from `dataClean.ipynb`)
- **`webapp/`** — `uv run --directory webapp pytest`

Run all tests in CI via GitHub Actions workflows (`.github/workflows/`).

## CI/CD

GitHub Actions workflows are configured per service:
- **`data-service.yml`** — lint, format check, pytest on `data-service/` changes
- **`webapp.yml`** — lint, format check, pytest on `webapp/` changes
- **`azure.yml`** — npm test on `azure/` changes
- **`etl.yml`** — test_clean.py on `etl/` changes

## Security

- **Secrets**: `.env` files are gitignored; use `.env.example` templates to document required keys
- **Credentials**: Store in environment variables or secure vaults; never commit real values
- **Data**: Raw and cleaned CSV files in `etl/data/` are gitignored (large, regenerated)

## License

(Add your license here)

## Support

For questions or issues:
- Check the module-specific `README.md` files
- Review `CLAUDE.md` for codebase documentation
- Open an issue on GitHub

---

**Last updated**: 2026-09-29
