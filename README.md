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
   │  ├─ teams/, players/ (season-level)       │
   │  ├─ standings/, rosters/ (weekly-level)   │
   │  └─ schedules/, games/, game-team-stats/  │
   │     (per-game-level)                      │
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

All tables are stored in Delta Lake under the catalog `football_manager`, schema `nfl`. Tables are populated by Databricks notebooks in `databricks/notebooks/` that read from Azure Blob Storage (`alsource`).

### Entity-Relationship Diagram

```
                    ┌─────────────┐
                    │   teams     │
                    │ ┌─────────┐ │
                    │ │ id (PK) │─┼──┐
                    │ │ uid     │ │  │
                    │ │ slug    │ │  │
                    │ └─────────┘ │  │
                    └─────────────┘  │
                           ▲         │
                           │         │
                  ┌────────┴────┬────┴────────┐
                  │             │             │
              ┌───▼───┐    ┌────▼────┐   ┌───▼────────┐
              │rosters│    │standings│   │schedules   │
              ├───────┤    ├─────────┤   ├────────────┤
              │teamId │◄──│teamId   │   │homeTeamId  │
              │playerId   │season  │   │awayTeamId  │
              │position   │conference   │week        │
              │playerName │stats   │   │date        │
              └───────┘    └─────────┘   └────────────┘
                  ▲              │             │
                  │              │             │
            ┌─────▴─┐      ┌─────▼─────────────▼─────┐
            │players│      │game_team_stats   games   │
            ├───────┤      ├────────────────────────┤
            │id (PK)│      │gameId             id(PK)│
            │name   │      │teamId             date  │
            │height │      │teamName           venue │
            │weight │      │stats              attendance│
            └───────┘      └────────────────────────┘
```

### Table Reference

| Table | Primary Key | Source | Frequency | Purpose |
|-------|-------------|--------|-----------|---------|
| **teams** | `id` | ESPN API (azure/teamsPlayersTrigger) | Daily @ 06:15 UTC | Team metadata (name, colors, abbreviations) |
| **players** | `id` | ESPN API (azure/teamsPlayersTrigger) | Daily @ 06:15 UTC | Player bio data (name, DOB, height, weight) |
| **standings** | `teamId`, `season` | ESPN API (azure/standingsRostersTrigger) | Daily @ 08:30 UTC | Team standings (wins, losses, points, etc.) — stats are dynamic |
| **rosters** | `teamId`, `playerId` | ESPN API (azure/standingsRostersTrigger) | Daily @ 08:30 UTC | Team rosters with player positions and jersey numbers |
| **schedules** | `id` (gameId) | ESPN API (azure/gameDataTrigger) | Every 6 hours | Game schedule (date, week, score) — refreshed for active games |
| **games** | `id` (gameId) | ESPN API (azure/gameDataTrigger) | Every 6 hours | Game details (venue, city, attendance) — only for targeted games |
| **game_team_stats** | `gameId`, `teamId` | ESPN API (azure/gameDataTrigger) | Every 6 hours | Per-team boxscore stats (passing yards, rushing, penalties, etc.) — dynamic |

### Common Fields

All tables include lineage tracking:

| Field | Type | Meaning |
|-------|------|---------|
| `snapshot_at` | TIMESTAMP | File modification time when data was fetched |
| `source_file` | STRING | Azure Blob path to the source JSON file |
| `ingested_at` | TIMESTAMP | When the record was inserted into Delta Lake |

### Data Refresh Strategy

- **Season-level data** (teams, players): Daily 06:15 UTC
- **Weekly-level data** (standings, rosters): Daily 08:30 UTC
- **Game-level data** (schedules, games, game-team-stats): Every 6 hours
  - Schedules: deduplicated across team APIs
  - Games & stats: only fetched for in-progress or recently-completed games (configurable trailing window, default 3 days)

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

## License

(Add your license here)

## Support

For questions or issues:
- Check the module-specific `README.md` files
- Review `CLAUDE.md` for codebase documentation
- Open an issue on GitHub

---

**Last updated**: 2026-09-29
