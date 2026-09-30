# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Football Manager** is a monorepo for NFL fantasy football data management. Data flows through the modules in this order:

`azure/` (fetch injury news → blob JSON) and `etl/` (clean raw CSVs locally) feed `databricks/` (Spark ingestion into Delta Lake), which `data-service/` exposes read-only via MCP/SQL, which `webapp/` queries through a chat UI.

- `azure/`: Azure Functions (Node.js v4) – fetches injury news every 6 hours via FantasyPros API and stores JSON snapshots to Azure Blob Storage
- `etl/`: Data cleaning notebooks (Python/Jupyter) – ingests raw NFL league data, cleans and standardizes it, writes cleaned CSVs to local disk (does **not** write to Delta Lake itself)
- `databricks/`: PySpark/Databricks notebooks – the actual Delta Lake writer. Reads the blob JSON from `azure/` and raw/cleaned CSVs, writes/merges into Delta tables under catalog `football_manager`, schema `nfl`
- `data-service/`: Delta Lake MCP server (Python 3.10+) – exposes read-only SQL access to the `nfl` schema's Delta tables via Model Context Protocol
- `webapp/`: Flask web application (Python 3.10+) – provides a chat UI for querying NFL data via Claude and the `data-service` API

Each module is independent with its own dependencies and configuration except `etl/`, which has no `pyproject.toml` of its own and relies on the root `uv` workspace. See `README.md` in the repo root for the architecture diagram and `REORGANIZATION_SUMMARY.md` for the history of the `files/`→`data-service/`, `dataIngestion/`→`etl/` renames.

**uv workspace**: root `pyproject.toml` declares `[tool.uv.workspace] members = ["data-service", "webapp"]` — `etl/` and `databricks/` are intentionally not members (they run as notebooks, not installed packages).

## Module: azure/ (Azure Functions)

**Purpose**: Scheduled workers that ingest NFL data from two sources:
1. **FantasyPros Injury News** – fetches every 6 hours, stores to blob path `injury/...`
2. **ESPN Game/Team/Player Data** – three independent timers by volatility tier: teams+players (daily), standings+rosters (daily), schedules+games+game-team-stats (every 6 hours), each writing to a sibling path (`teams/`, `schedules/`, etc.) in the same container.

All data lands in one Azure Blob Storage container (deployed as `alsource`, organized by top-level path prefix) as raw JSON snapshots.

**Dev**:
- `npm start` – runs `func start` (local Azure Functions runtime)
- `npm test` – `node --test test/` (fixture-based unit tests, no network access)

**Key files**:
- `src/functions/newsTrigger.js` – FantasyPros injury news, every 6h (cron: `0 0 */6 * * *`)
  - Uses shared `gameDataStorage.js` helper module (reused by all 3 new functions)
- `src/functions/teamsPlayersTrigger.js` – ESPN teams + players (season/bio-level), daily at 06:15 UTC
  - Teams: 1 API call, ~32 records
  - Players: paginated, current season only (current-year athlete index)
- `src/functions/standingsRostersTrigger.js` – ESPN standings + rosters (weekly-level), daily at 08:30 UTC
  - Standings: 1 API call, deduped by team/season
  - Rosters: 32 parallel calls (one per team), bounded concurrency (default 5)
  - Partial-failure handling: uploads what succeeded if ≥1 roster call fails
- `src/functions/gameDataTrigger.js` – ESPN schedules + games + game-team-stats (per-game-level), every 6h
  - Schedules: 32 parallel calls, deduped by `game_id`
  - Target-games filter: selects only `completed === false` OR recently-completed (trailing 3 days) games to avoid re-fetching ancient data
  - Games + game-team-stats: parallel fetch of target game summaries, plucks header/boxscore per game
  - Partial-failure handling: uploads what succeeded; fails only if all datasets completely failed
- `src/lib/espnClient.js` – shared retry/concurrency/season helpers
  - `getCurrentSeasonYear()` – stateless NFL season year resolver
  - `fetchWithRetry()` – retry with linear backoff
  - `mapWithConcurrency()` – bounded-concurrency batching (no new dependency)
  - `paginateEspnEndpoint()` – handles ESPN's pageCount pagination
- `src/lib/espnAssemble.js` – pure data transformation functions (fixture-testable)
  - `dedupeSchedulesByGameId()` – game deduplication across team schedules
  - `pluckGameHeader()` / `pluckBoxscoreTeamStats()` – JSON plucking from `/summary` responses
  - `selectTargetGames()` – filters schedule to in-progress/recently-completed games
- `src/lib/gameDataStorage.js` – shared blob upload helpers (retry-free wrapper around SDK)
  - `buildBlobName()` – path construction (`{prefix}/{YYYY-MM-DD}/{ISO-timestamp}.json`)
  - `uploadJsonBlob()` – container-initialized upload with proper content-type headers
  - `getStorageInfo()` – logging helper
- `package.json` – dependencies: `@azure/functions`, `@azure/storage-blob`
- `host.json` – Azure Functions v2.0 runtime config
- `local.settings.json` – local dev env (gitignored; contains real API credentials — never commit)
- `.env.example` – new; documents all env vars (existing + new ESPN-related ones)
- `test/lib/espnClient.test.js`, `test/lib/espnAssemble.test.js`, `test/lib/gameDataStorage.test.js` – fixture-driven unit tests

**Environment variables** (set in `local.settings.json` locally or `.env`):
- `FANTASY_PRO_API_KEY` – FantasyPros API key (required for news)
- `NEWS_STORAGE_CONNECTION` – Azure Storage connection string (defaults to `AzureWebJobsStorage` if unset)
- `ALSOURCE_CONTAINER` – Blob container name (defaults to `alsource`; holds all datasets: `injury/`, `teams/`, etc.)
- `ESPN_FETCH_CONCURRENCY` – max in-flight ESPN calls (default 5)
- `ESPN_FETCH_RETRIES` – retry attempts per call (default 3)
- `GAME_SUMMARY_TRAILING_DAYS` – look-back window for recently-completed games (default 3)
- `GAME_SUMMARY_MAX_TARGETS` – safety ceiling on per-run game-summary fetches (default 50)

**Logging**: All functions log via bracketed tags (`[FETCH_START]`, `[API_CALL]`, `[DATA_PARSED]`, `[UPLOAD_START]`, `[UPLOAD_SUCCESS]`, `[STORAGE_INFO]`, `[SUMMARY]`, `[ERROR]`, `[ITEM_ERROR]`, `[DATASET_SUMMARY]`, `[TARGET_GAMES]`). Each invocation returns a `summary` object with per-dataset status, byte counts, and failure accounting.

**Convention**: Timer triggers all use NCronTab 6-field syntax. Tier 1/2 daily, Tier 3 every-6-hours (matching project default). All functions throw on fatal errors (never swallow); partial failures are logged/counted in summary but don't fail the invocation unless all datasets are 100% failed.

## Module: data-service/ (Delta Lake MCP Server)

**Purpose**: MCP server providing read-only SQL access to Delta Lake tables (populated by `databricks/`), with tools for listing, describing, inspecting history, and querying tables via DuckDB SQL.

**Dev**:
- `uv run --directory data-service --with mcp[cli] mcp dev src/data_service/server.py` – inspect MCP tools and test locally
- `uv run --directory data-service pytest` – **currently broken**: `pytest` is not an installed dependency anywhere in the workspace (`uv.lock` has neither `pytest` nor `ruff`), so this fails with "Failed to spawn: `pytest`". CI (`data-service.yml`) runs this same command and would fail too.
- Credentials via env vars or `storage_options` in `tables.json`. The real local `.env` here only sets `AZURE_STORAGE_CONNECTION_STRING` (parsed manually in `server.py`'s `Lake.__init__`); the README also documents `AWS_PROFILE`/`AZURE_STORAGE_ACCOUNT_NAME`/`GOOGLE_APPLICATION_CREDENTIALS` but `server.py` doesn't read those explicitly — they'd only take effect via delta-rs's own default credential chain.

**Key files**:
- `src/data_service/server.py` – MCP server implementation; exposes `list_tables`, `describe_table`, `table_history`, `query` tools
- `pyproject.toml` – workspace member; deps: `mcp[cli]`, `deltalake>=1.0`, `duckdb>=1.1`, `pyarrow`, `python-dotenv`, `pytz`
- `tables.example.json` – template; copy to `tables.json` (gitignored) and list your Delta tables (name → path). Note the server's actual default `CONFIG_PATH` resolves to `src/data_service/tables.json` (next to `server.py`), not a repo-root-relative path.
- `tests/test_server.py` – imports via `from server import ...` (bare, pre-reorg style) instead of `from data_service.server import ...`; breaks under normal pytest collection, but is runnable standalone via `python test_server.py` (has an `if __name__ == "__main__"` guard)
- `tests/test_tables.py` – has no `__main__` guard and executes top-level script code on import, including reading a `tests/tables.json` that doesn't exist; would error immediately even if pytest were installed
- `README.md` – full setup & configuration guide (note: some credential env vars it documents aren't actually consumed by the code — see above)

**Architecture**: Installed as a workspace member; can be imported in-process (e.g., by `webapp/`) as `from data_service.server import list_tables, describe_table, table_history, query`.

**Convention**: Queries always see latest committed Delta version; partitions and column filters pushed down to DuckDB automatically.

## Module: etl/ (Data Cleaning & Ingestion)

**Purpose**: Batch notebooks that ingest raw NFL league data and clean/standardize it into local CSVs. Does **not** write to Delta Lake — that happens downstream in `databricks/`.

**Dev**:
- No `pyproject.toml`/`requirements.txt` of its own; depends on the root `uv` workspace's installed packages (`pandas`, `requests`, `pytz`, etc.) even though `etl/` isn't a declared workspace member
- Jupyter notebooks: `dataClean.ipynb` (main cleaning pipeline), `testApi.ipynb` (ESPN API exploration), `uploadToAzure.ipynb`. `sleeper.ipynb` was deleted from the working tree (never committed) — ignore references to it in `etl/README.md`.
- Input: raw CSVs in `data/raw/` (gitignored; regenerated externally)
- Output: cleaned CSVs written to `data/clean/` (gitignored)

**`etl/tests/test_clean.py` is not a test** — despite its name and despite `etl/README.md`/CI treating it as a validator of `dataClean.ipynb`'s output, its actual content is a standalone ESPN API scraper (`site.api.espn.com`/`sports.core.api.espn.com`) that pulls ~5 seasons of teams/standings/rosters/schedules/box-scores. It has no `assert`s or `test_` functions, and its own docstring warns it makes ~1,700+ requests and takes 10-20 minutes. **`.github/workflows/etl.yml` currently runs this on every push/PR touching `etl/**`** — be aware that "running etl's tests" today means a slow live scrape, not a fast validation, and don't add logic that assumes it validates anything.

**Data conventions**:
- Column names: snake_case (enforced by `dataClean.ipynb`)
- Duplicates: removed before writing output CSVs
- No secrets or credentials committed

## Module: databricks/ (Delta Lake Ingestion — PySpark)

**Purpose**: The module that actually writes to Delta Lake. Not a `uv` workspace member, no `pyproject.toml`, no CI — these are Databricks notebooks meant to run on a Databricks cluster, not locally via `uv`.

**Key files**:
- `notebooks/ingest_game_data.ipynb` – PySpark Structured Streaming (Auto Loader / `cloudFiles`) that reads raw CSVs from `abfss://alsource@footballmanagerli.dfs.core.azure.net/`, snake_cases columns, and writes Delta tables to `abfss://aldestination@footballmanagerli.dfs.core.azure.net/data/{table}`, registered under catalog `football_manager`, schema `nfl`.
- `notebooks/ingest_news_data.ipynb` ("Injury to delta table") – reads the injury JSON snapshots that `azure/src/functions/newsTrigger.js` uploads to the `alsource` container's `injury/` path, dedupes per micro-batch, and upserts (merge) into Delta table `nfl.injury_news` (columns: id, player_id, team_id, title, description, impact, author, categories, link, sport_id, created_at, snapshot_at, source_file, ingested_at) using `foreachBatch` + `trigger(availableNow=True)`.

**Convention**: This is the only module that writes to the `football_manager.nfl` Delta catalog that `data-service/` reads from — if data isn't showing up in `data-service` queries, check whether these notebooks have actually been run on a cluster recently, not just whether `azure/`/`etl/` produced fresh source data.

## Module: webapp/ (Flask Chat UI)

**Purpose**: A web application providing a chat interface for querying NFL data. Uses Claude's API with tool calling to access the Delta Lake data service.

**Dev**:
- `uv run --directory webapp python app.py` – run the Flask server (http://localhost:5000)
- `uv run --directory webapp pytest` – **currently broken for the same reason as data-service**: `pytest` isn't installed in the workspace. (Unlike `data-service/`'s tests, `webapp/tests/test_app.py` itself is a legitimate, correctly-written 2-test smoke test — it would pass once pytest is actually installed.)
- `python webapp/chatbot.py` – CLI mode for testing the chatbot directly (interactive REPL)

**Key files**:
- `app.py` – Flask server; exposes `/` (chat UI), `/api/chat`, `/api/clear`, `/api/history`
  - Configuration via env vars: `FLASK_DEBUG` (default True), `FLASK_PORT` (default 5000), `ANTHROPIC_API_KEY` (required)
- `chatbot.py` – `Chatbot` class wrapping Anthropic's Messages API; hardcodes `self.model = "claude-opus-5-5"`
  - Defines MCP-style tools for `list_tables`, `describe_table`, `table_history`, `query` from `data-service`
  - Implements a tool-use loop to call these functions in-process
  - Imports from `data_service.server` (workspace member; no sys.path hacks)
- `templates/index.html` – React/vanilla JS chat UI
- `pyproject.toml` – workspace member; deps: `anthropic`, `flask`, `python-dotenv`, `pytz`, plus `data-service` as a workspace source
- `tests/test_app.py` – working smoke tests (this module's only genuinely functional test file)

**Architecture**: Depends on `data-service` as an in-process import (no MCP protocol overhead for this collocated scenario). Pulls secrets from `webapp/.env` via `python-dotenv` (no `.env.example` template exists yet — copy the real `.env` structure from README if starting fresh).

## Known issues (testing/tooling)

These are real gaps in the repo today, not intended behavior — call them out rather than assuming `pytest`/`ruff` work:

- Neither `ruff` nor `pytest` is declared as a dependency anywhere in the workspace (`uv.lock`, any `pyproject.toml`). `uv run --directory data-service pytest` and `uv run --directory webapp pytest` both fail with "Failed to spawn: `pytest`" until someone adds them (e.g. as a `[dependency-groups] dev` entry).
- CI (`.github/workflows/data-service.yml`, `webapp.yml`) runs `uv run ruff format --check`, `uv run ruff check`, and `uv run pytest` — all of these currently fail in CI for the same reason.
- `.github/workflows/etl.yml` runs `python etl/tests/test_clean.py` on every `etl/**` change — this is a live ~10-20 minute ESPN API scrape, not a fast validation step (see `etl/` section above).
- `data-service/tests/test_server.py` and `test_tables.py` have import/path bugs (see `data-service/` section) that would break even once pytest is installed.
- No `ruff.toml` or `[tool.ruff]` config exists anywhere, so there's no defined formatting standard to enforce yet. Don't assume a `ruff format` post-edit hook is configured for this repo — no hook exists in this repo's `.claude/settings.local.json` (only a permissions allowlist is defined there).

## Testing

Per-module commands (see "Known issues" above for what currently doesn't work):
- **azure/**: `cd azure && npm test` (placeholder only)
- **data-service/**: `uv run --directory data-service pytest` (currently fails — pytest not installed)
- **etl/**: `python etl/tests/test_clean.py` (currently a live ESPN scraper, not a validator)
- **webapp/**: `uv run --directory webapp pytest` (currently fails — pytest not installed; the test file itself is correct)

Or via CI: `.github/workflows/` per service (same caveats apply).
