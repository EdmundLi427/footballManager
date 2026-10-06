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
   ┌──────────────────┐      ┌──────────────────┐
   │    FantasyPros   │      │      ESPN        │
   │      API         │      │      API         │
   │  (Injury News)   │      │ (Games/Teams)    │
   └────────┬─────────┘      └────────┬─────────┘
            │                         │
       (6-hourly)        (6h, daily depending on tier)
            │                         │
            └────────────┬────────────┘
                         │
   ┌─────────────────────▼──────────────────────┐
   │        Azure Blob Storage                  │
   │  ├─ injury/... (FantasyPros news)         │
   │  └─ espn/ (raw ESPN JSON):                │
   │     teams/, players/, standings/,         │
   │     rosters/, schedules/, game_summaries/ │
   └─────────────────────┬──────────────────────┘
                         │
   ┌─────────────────────┴──────────────────────┐
   │   Raw Data (CSVs from etl/)               │
   └─────────────────────┬──────────────────────┘
                         │
                  (ETL/Databricks)
                         │
                ┌────────▼──────────┐
                │   Delta Lake      │
                │  (cleaned data)   │
                └────────┬──────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   ┌────▼─────────────┐  │  ┌─────▼──────┐    ┌─────▼──────────┐
   │  Data Service    │  │  │   Web App  │    │   (CLI Chat)   │
   │  (MCP / SQL)     │  │  │ (Flask UI) │    │   (dev-only)   │
   └──────────────────┘  │  └────────────┘    └────────────────┘
                         │
                    (future)
                    Delta Lake
                    Loader from
                    Blob Storage
```

## Modules

| Module | Purpose | Tech Stack |
|--------|---------|-----------|
| **`azure/`** | Scheduled jobs: (1) fetches injury news every 6h from FantasyPros API; (2) fetches ESPN game/team/player data on three independent cadences (daily for season-level, every 6h for game-level). All data stored as raw JSON to Azure Blob Storage under separate path prefixes. | Node.js (v4 Azure Functions), FantasyPros API, ESPN unofficial API |
| **`data-service/`** | Query layer: MCP server providing read-only SQL access to Delta Lake tables via DuckDB | Python 3.10+, MCP, Delta Lake, DuckDB |
| **`etl/`** | Batch pipelines: ingests raw NFL data, cleans and standardizes (snake_case, nulls, dedupe), outputs to Delta Lake | Python 3.10+, Jupyter notebooks, Pandas |
| **`webapp/`** | Web chat UI: Flask server with Claude-powered natural-language interface to query NFL data | Python 3.10+, Flask, Anthropic API, Claude |

See each module's `README.md` for setup, development, and architecture details.

## Data Schema

All tables are stored in Delta Lake under schema `nfl` (in the workspace's default catalog). Tables are populated by Databricks notebooks in `databricks/notebooks/` that read from Azure Blob Storage (`alsource`).

**Column-by-column reference (keys, types, parsing rules, audit columns): [`databricks/TRANSFORMS.md`](databricks/TRANSFORMS.md).** The source of truth is `databricks/lib/schemas.py`.

| Table | One row per |
|-------|-------------|
| `teams` | team |
| `players` | athlete in ESPN's season index (includes inactive/retired) |
| `rosters` | (season, team, player) |
| `standings` | (season, season type, team) |
| `schedules` | game |
| `games` | game (venue, attendance, final score) |
| `game_team_stats` | (game, team) box score |
| `injury_news` | FantasyPros injury news item |

### Data Refresh Strategy

- **Season-level data** (teams, players): Daily 06:15 UTC
- **Weekly-level data** (standings, rosters): Daily 08:30 UTC
- **Game-level data** (schedules, game summaries): Every 6 hours
  - Schedules: regular season and playoffs, deduplicated across team APIs
  - Game summaries (→ `nfl.games` + `nfl.game_team_stats`): only fetched for games that have kicked off and are in progress or completed within the trailing window (default 3 days)
- **Historical seasons** (2023 onward) were landed once by `azure/scripts/backfill-espn.js`; those blobs are permanent and re-ingest after any table reset
- Transforms from raw ESPN JSON to `nfl.*` tables are documented in [`databricks/TRANSFORMS.md`](databricks/TRANSFORMS.md)

### Ingestion Pipeline

1. **Azure Functions** (`azure/`) fetch data from ESPN/FantasyPros and write raw JSON to `alsource` blob container
2. **Databricks notebooks** (`databricks/notebooks/ingest_game_*.ipynb`) read JSON via CloudFiles
3. **Deduplication & Merge**: Per-batch dedup, then upsert into Delta tables (update if snapshot is newer)
4. **Data Service** exposes read-only access via MCP/SQL for downstream applications

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

- **`azure/`** — `npm test` (fixture-based unit tests for ESPN API clients, data assembly, and blob naming)
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

**Last updated**: 2026-09-29
