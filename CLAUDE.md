# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Football Manager** is a monorepo for NFL fantasy football data management:
- `azure/`: Azure Functions (Node.js v4) – fetches injury news every 6 hours via FantasyPros API and stores to Azure Blob Storage
- `data-service/`: Delta Lake MCP server (Python 3.10+) – exposes read-only SQL access to processed data via Model Context Protocol
- `etl/`: Data cleaning pipelines (Python/Jupyter) – ingests raw NFL league data, cleans, standardizes, and outputs to Delta Lake
- `webapp/`: Flask web application (Python 3.10+) – provides a chat UI for querying NFL data via Claude and the Delta Lake API

Each module is independent with its own dependencies, tests, and configuration. See `README.md` in the repo root for an architecture overview.

## Module: azure/ (Azure Functions)

**Purpose**: Scheduled worker that fetches NFL injury news from FantasyPros API every 6 hours and stores JSON snapshots to Azure Blob Storage.

**Dev**:
- `npm start` – runs `func start` (local Azure Functions runtime)
- `npm test` – placeholder; add real tests for API calls and Blob uploads

**Key files**:
- `src/functions/newsTrigger.js` – timer trigger handler (cron: `0 0 */6 * * *`, every 6 hours)
  - Fetches `https://api.fantasypros.com/public/v2/json/nfl/news?category=injury&pageIndex=1`
  - Parses JSON response, uploads to blob path `injury/YYYY-MM-DD/ISO-timestamp.json`
  - Logs structured summaries (`[FETCH_START]`, `[API_CALL]`, `[UPLOAD_SUCCESS]`, etc.)
  - Throws errors on API/upload failure (does not swallow)
- `package.json` – dependencies: `@azure/functions`, `@azure/storage-blob` (removed unused `azure@0.10.6`)
- `host.json` – Azure Functions runtime config
- `local.settings.json` – local dev env (gitignored; contains API credentials)
- `.env.example` – template for `FANTASY_PRO_API_KEY`, `NEWS_STORAGE_CONNECTION`, `NEWS_CONTAINER`

**Environment variables**:
- `FANTASY_PRO_API_KEY` – FantasyPros API key (required)
- `NEWS_STORAGE_CONNECTION` – Azure Storage connection string (defaults to `AzureWebJobsStorage` if unset)
- `NEWS_CONTAINER` – Blob container name (defaults to `nfl-news`)

**Convention**: Timer trigger = "every 6 hours" by default unless context says otherwise.

## Module: data-service/ (Delta Lake MCP Server)

**Purpose**: MCP server providing read-only SQL access to Delta Lake tables, with tools for listing, describing, inspecting history, and querying tables via DuckDB SQL.

**Dev**:
- `uv run --directory data-service --with mcp[cli] mcp dev src/data_service/server.py` – inspect MCP tools and test locally
- `uv run --directory data-service pytest` – run unit tests
- Credentials via env vars (`AWS_PROFILE`, `AZURE_STORAGE_ACCOUNT_NAME`, `GOOGLE_APPLICATION_CREDENTIALS`) or `storage_options` in `tables.json`

**Key files**:
- `src/data_service/server.py` – MCP server implementation; exposes `list_tables`, `describe_table`, `table_history`, `query` tools
- `pyproject.toml` – workspace member; dependencies: `mcp[cli]`, `deltalake>=1.0`, `duckdb>=1.1`, `pyarrow`, `python-dotenv`, `pytz`
- `tables.example.json` – template; copy to `tables.json` and list your Delta tables (name → path)
- `.env.example` – template for credentials (AWS profile, Azure account name, Google service account JSON path)
- `tests/test_server.py`, `test_tables.py` – unit and integration tests
- `README.md` – full setup & configuration guide

**Architecture**: Installed as a workspace member; can be imported in-process (e.g., by `webapp/`) as `from data_service.server import list_tables, describe_table, table_history, query`.

**Convention**: Queries always see latest committed Delta version; partitions and column filters pushed down to DuckDB automatically.

## Module: etl/ (Data Cleaning & Ingestion)

**Purpose**: Batch ETL pipelines (Jupyter notebooks) that ingest raw NFL league data, clean and standardize it, and output to Delta Lake.

**Dev**:
- `python etl/tests/test_clean.py` – run data validation tests (validates output from `dataClean.ipynb`)
- Jupyter notebooks: `dataClean.ipynb` (main ETL), `testApi.ipynb` (ESPN API exploration), `uploadToAzure.ipynb`, `sleeper.ipynb`
- Input: raw CSVs in `data/raw/` (players, teams, schedules, rosters, games, standings, athletes)
- Output: cleaned CSVs written to `data/clean/`

**Data conventions**:
- Column names: snake_case (enforced by `dataClean.ipynb`)
- Null handling: documented per notebook; typically explicit `NaN` or `NULL` for missing data
- Duplicates: removed before writing output CSVs
- No secrets or credentials committed; use `.env` for API keys

**Key files**:
- `data/raw/` – input CSVs (gitignored; large, regenerated externally)
- `data/clean/` – output CSVs from `dataClean.ipynb` (gitignored)
- `tests/test_clean.py` – validates row counts, required columns, null patterns, no unexpected duplicates

**Note**: Notebooks are exploratory; consider migrating stable ETL logic to Python modules (in `etl/src/`) if patterns solidify.

## Module: webapp/ (Flask Chat UI)

**Purpose**: A web application providing a chat interface for querying NFL data. Uses Claude's API with tool calling to access the Delta Lake data service.

**Dev**:
- `uv run --directory webapp python app.py` – run the Flask server (http://localhost:5000)
- `uv run --directory webapp pytest` – run tests
- `python webapp/chatbot.py` – CLI mode for testing the chatbot directly (interactive REPL)

**Key files**:
- `app.py` – Flask server; exposes `/` (chat UI), `/api/chat`, `/api/clear`, `/api/history`
  - Configuration via env vars: `FLASK_DEBUG` (default True), `FLASK_PORT` (default 5000), `ANTHROPIC_API_KEY` (required)
- `chatbot.py` – `Chatbot` class wrapping Anthropic's Messages API
  - Defines MCP-style tools for `list_tables`, `describe_table`, `table_history`, `query` from `data-service`
  - Implements a tool-use loop to call these functions in-process
  - Imports from `data_service.server` (workspace member; no sys.path hacks)
- `templates/index.html` – React/vanilla JS chat UI
- `pyproject.toml` – workspace member; dependencies: `anthropic`, `flask`, `python-dotenv`, `pytz`, plus `data-service` as a workspace source
- `.env.example` – template for `ANTHROPIC_API_KEY`, `FLASK_DEBUG`, `FLASK_PORT`
- `tests/test_app.py` – placeholder smoke tests

**Architecture**: Depends on `data-service` as an in-process import (no MCP protocol overhead for this collocated scenario). Pulls secrets from `.env` via `python-dotenv`.

## Testing

Run tests per service:
- **azure/**: `cd azure && npm test`
- **data-service/**: `uv run --directory data-service pytest`
- **etl/**: `python etl/tests/test_clean.py`
- **webapp/**: `uv run --directory webapp pytest`

Or test everything via CI: `.github/workflows/` per service.

## Python Formatting

Use `ruff format` for all Python files. A post-edit hook runs `ruff format` automatically; no manual step needed. Apply to all three Python services: `azure/`, `data-service/`, `etl/`, `webapp/`.
