# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Football Manager** is a monorepo for NFL fantasy football data management. Data flows through the modules in this order:

`azure/` (fetch ESPN + injury news → raw blob JSON) feeds `databricks/` (Spark ingestion into Delta Lake), which `data-service/` exposes read-only via MCP/SQL, which `webapp/` queries through a chat UI.

- `azure/`: Azure Functions (Node.js v4) – lands raw ESPN game/team/player JSON and FantasyPros injury news snapshots in Azure Blob Storage
- `etl/`: Data cleaning notebooks (Python/Jupyter) – ingests raw NFL league data, cleans and standardizes it, writes cleaned CSVs to local disk (does **not** feed Delta Lake; the legacy CSV ingest notebook was removed)
- `databricks/`: PySpark/Databricks notebooks + tested `lib/` – the actual Delta Lake writer. Parses the raw blob JSON from `azure/` and merges into Delta tables under schema `nfl`
- `data-service/`: Delta Lake MCP server (Python 3.10+) – exposes read-only SQL access to the `nfl` schema's Delta tables via Model Context Protocol
- `webapp/`: Flask web application (Python 3.10+) – provides a chat UI for querying NFL data via Claude and the `data-service` API

Each module is independent with its own dependencies and configuration except `etl/`, which has no `pyproject.toml` of its own and relies on the root `uv` workspace. See `README.md` in the repo root for the architecture diagram and `REORGANIZATION_SUMMARY.md` for the history of the `files/`→`data-service/`, `dataIngestion/`→`etl/` renames.

**uv workspace**: root `pyproject.toml` declares `[tool.uv.workspace] members = ["data-service", "webapp"]` — `etl/` and `databricks/` are intentionally not members (they run as notebooks, not installed packages).

## Module: azure/ (Azure Functions)

**Purpose**: Scheduled workers that land raw NFL data in blob storage:
1. **FantasyPros Injury News** – every 6 hours, blob path `injury/...`
2. **ESPN** – three timers by volatility tier: teams+players (daily), standings+rosters (daily), schedules+game summaries (every 6 hours), under `espn/<dataset>/`.

All data lands in one container (`alsource`) as JSON arrays at `{prefix}/{YYYY-MM-DD}/{ISO-timestamp}.json`. **ESPN payloads keep ESPN's own field names/nesting** — Azure only trims unused subtrees (logos, links, leaders). All renaming/typing/parsing happens in `databricks/lib/transforms.py` (see `databricks/TRANSFORMS.md`), so parser bugs can be fixed and replayed over existing blobs. Don't reintroduce field plucking/renaming in Azure.

**Dev**:
- `npm start` – runs `func start` (local Azure Functions runtime)
- `npm test` – `node --test` on `test/lib/*.test.js` (fixture-based, no network)
- `node scripts/backfill-espn.js [--seasons 2023,2024,2025,2026] [--dry-run]` – one-off historical backfill (default: last 3 completed seasons + current). Lands schedules, every completed game's summary (chunked 100/blob) and regular+playoff standings under the normal `espn/` prefixes, so the Databricks ingest notebooks pick them up unchanged. No rosters (ESPN's site roster endpoint is empty for past seasons). Storage connection from env or `local.settings.json`; safe to re-run (keyed merge).
- `node scripts/refresh-espn-fixtures.js` – re-records small real ESPN responses into `test/fixture/espn/` and the matching landing payloads into `databricks/tests/fixtures/espn/` (the cross-module contract; a diff = ESPN schema drift). Also records `databricks/tests/fixtures/fantasypros/injury_news.json` using `FANTASY_PRO_API_KEY` from env or `local.settings.json` (never logged; skipped if absent)

**Key files**:
- `src/functions/newsTrigger.js` – FantasyPros injury news, every 6h → `injury/`
- `src/functions/teamsPlayersTrigger.js` – daily 06:15 UTC → `espn/teams/`, `espn/players/` (core v3 athlete index, paginated; includes retired players)
- `src/functions/standingsRostersTrigger.js` – daily 08:30 UTC → `espn/standings/` (one record: `{season, children}`), `espn/rosters/` (one record per team, 32 calls, bounded concurrency, partial-failure tolerant)
- `src/functions/gameDataTrigger.js` – every 6h → `espn/schedules/` (raw events from 32 team schedules × seasontype 2 and 3, deduped by id — without `seasontype` ESPN returns only the regular season) and `espn/game_summaries/` (one `{game_id, header, gameInfo, boxscore.{teams,players}}` per targeted game; `players` = per-athlete stat groups with parallel `keys`/`stats` string arrays)
- `src/lib/espnAssemble.js` – pure landing builders: `BLOB_PREFIXES`, `buildTeamsLanding`, `buildPlayersLanding`, `buildRosterLanding`, `buildStandingsLanding`, `trimScheduleEvent`, `buildGameSummaryLanding`, `dedupeSchedulesByGameId`, `selectTargetGames` (games that kicked off and are in progress or completed within the trailing window; uses `status.type.completed`), `selectCompletedGames` (backfill: all completed games, uncapped)
- `src/lib/espnFetch.js` – fetch+assemble shared by triggers and backfill: `fetchTeamIds`, `fetchSeasonSchedules` (regular + playoffs), `fetchGameSummaries`, `fetchStandings`; item failures are collected, not thrown
- `src/lib/espnClient.js` – `getCurrentSeasonYear()`, `fetchWithRetry()`, `mapWithConcurrency()`, `paginateEspnEndpoint()`
- `src/lib/gameDataStorage.js` – `buildBlobName()`, `uploadJsonBlob()`, `getStorageInfo()`
- `local.settings.json` – local dev env (gitignored; contains real API credentials — never commit)
- `test/lib/espnAssemble.test.js` – contract tests: builders on `test/fixture/espn/*` must deep-equal `databricks/tests/fixtures/espn/*`

**ESPN payload quirks (verified live)**: `status.type` is an object (`{name, state, completed, ...}`), not a string; `boxscore.teams[0]` is the **away** team (use `homeAway`); box score paired stats carry real data in `displayValue` (`"9-17"`) while `value` is a ratio or `'-'`; header scores are strings, schedule scores are `{value, displayValue}`; standings live at `children[].standings.entries`; player box score keys repeat across groups (`interceptions` = thrown in `passing`, caught in `interceptions`) and missing values are `'--'`; the player box score has no position.

**Environment variables** (set in `local.settings.json` locally or `.env`):
- `FANTASY_PRO_API_KEY` – FantasyPros API key (required for news)
- `NEWS_STORAGE_CONNECTION` – Azure Storage connection string (defaults to `AzureWebJobsStorage` if unset)
- `ALSOURCE_CONTAINER` – Blob container name (defaults to `alsource`)
- `ESPN_FETCH_CONCURRENCY` – max in-flight ESPN calls (default 5)
- `ESPN_FETCH_RETRIES` – retry attempts per call (default 3)
- `GAME_SUMMARY_TRAILING_DAYS` – look-back window for recently-completed games (default 3)
- `GAME_SUMMARY_MAX_TARGETS` – safety ceiling on per-run game-summary fetches (default 50)

**Logging**: bracketed tags (`[FETCH_START]`, `[API_CALL]`, `[DATA_PARSED]`, `[UPLOAD_START]`, `[UPLOAD_SUCCESS]`, `[STORAGE_INFO]`, `[SUMMARY]`, `[ERROR]`, `[ITEM_ERROR]`, `[DATASET_SUMMARY]`, `[TARGET_GAMES]`). Each invocation returns a `summary` object with per-dataset status, byte counts, and failure accounting.

**Convention**: Timer triggers use NCronTab 6-field syntax. Functions throw on fatal errors (never swallow); partial failures are logged/counted but don't fail the invocation unless every dataset failed.

## Module: data-service/ (Delta Lake MCP Server)

**Purpose**: MCP server providing read-only SQL access to Delta Lake tables (populated by `databricks/`), with tools for listing, describing, inspecting history, and querying tables via DuckDB SQL.

**Dev**:
- `uv run --directory data-service --with mcp[cli] mcp dev src/data_service/server.py` – inspect MCP tools and test locally
- `uv run --directory data-service pytest` – unit tests (`tests/test_unit.py`); `-m integration` tests skip unless they find `tables.json` + `AZURE_STORAGE_CONNECTION_STRING`. Requires the venv synced with `uv sync --all-packages --all-groups` (pytest/ruff live in the root `dev` group).
- Credentials via env vars or `storage_options` in `tables.json`. The real local `.env` here only sets `AZURE_STORAGE_CONNECTION_STRING` (parsed manually in `server.py`'s `Lake.__init__`); the README also documents `AWS_PROFILE`/`AZURE_STORAGE_ACCOUNT_NAME`/`GOOGLE_APPLICATION_CREDENTIALS` but `server.py` doesn't read those explicitly — they'd only take effect via delta-rs's own default credential chain.

**Key files**:
- `src/data_service/server.py` – MCP server implementation; exposes `list_tables`, `describe_table`, `table_history`, `query` tools
- `pyproject.toml` – workspace member; deps: `mcp[cli]`, `deltalake>=1.0`, `duckdb>=1.1`, `pyarrow`, `python-dotenv`, `pytz`
- `tables.example.json` – template. The real config is `src/data_service/tables.json` (default `CONFIG_PATH`, next to `server.py`; gitignored; override with `DELTA_TABLES_CONFIG`) listing the 9 `nfl` tables' `abfss://aldestination@footballmanagerli...` paths (`silver/<table>`, `injury/injury_news`).
- `README.md` – full setup & configuration guide (note: some credential env vars it documents aren't actually consumed by the code — see above)

**Architecture**: Installed as a workspace member; can be imported in-process (e.g., by `webapp/`) as `from data_service.server import list_tables, describe_table, table_history, query`.

**Convention**: Queries always see latest committed Delta version. Each table is materialized in memory via deltalake's `QueryBuilder` (`_snapshot()` in `server.py`), **not** `to_pyarrow_dataset()` — Databricks MERGE enables deletion vectors, which `to_pyarrow_dataset()` rejects; view types (`string_view`) are cast to plain types for DuckDB. `.env` loading: caller's `.env` first, then `data-service/.env`; whitespace is stripped from `AZURE_STORAGE_CONNECTION_STRING`.

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

**Purpose**: The only module that writes the `nfl.*` Delta tables. Notebooks run on a Databricks cluster; the logic lives in a locally tested library. **Column-by-column reference: `databricks/TRANSFORMS.md`.**

**Key files**:
- `lib/schemas.py` – single source of truth: `*_RAW` Auto Loader read schemas (ESPN field names) and `TABLES[name]` (silver columns/types/comments, `keys`, `source_prefix`, `snapshot_col`). `ESPN_TABLES` = the 8 ESPN tables.
- `lib/transforms.py` – `parse_<table>(df)`: pure DataFrame → DataFrame, output exactly `TABLES[name].schema`; `with_file_metadata()` adds `_source_file`/`_snapshot_at`; safe casts (`to_long`/`to_double`) so `'-'` becomes NULL under ANSI mode. Spark 3.5+/4.x compatible.
- `lib/delta_io.py` – `ensure_table()` (DDL generated from `TABLES`, keys `NOT NULL`), `dedupe_latest()`, `upsert_latest()` (MERGE; update only when incoming snapshot is newer), `upsert_batch_fn()` for `foreachBatch`.
- `notebooks/ingest_game_{teams,players,rosters,standings,schedules,games,team_stats,player_stats}.ipynb` – identical 4-cell template: `sys.path` → `lib`, `TABLE = schemas.TABLES[...]`, Auto Loader with `TABLE.raw_schema` → `parse_*` → upsert, sanity check. Data at `aldestination/silver/<table>`, checkpoints `_checkpoints/silver_<table>`.
- `notebooks/ingest_news_data.ipynb` – same pattern for `nfl.injury_news` (keeps its original location, checkpoint and `source_file`/`snapshot_at`/`ingested_at` column names).
- `notebooks/reset_espn_tables.ipynb` – destructive, guarded by widget `confirm=RESET`: drops the 8 ESPN tables + data/checkpoint dirs (incl. pre-`espn/` paths). Never touches `injury_news`.

**Conventions**: snake_case columns; ESPN ids are `STRING`; audit columns `_source_file`, `_snapshot_at`, `_ingested_at`; no schema evolution or NULL-filling of missing columns (that previously hid upstream bugs) — schema changes are deliberate (edit `schemas.py` + `transforms.py` + TRANSFORMS.md, then ALTER or reset/replay).

**Tests** (`databricks/tests/`, run from repo root; root `pyproject.toml` sets pytest `pythonpath = ["databricks"]`):
- `uv run pytest databricks/tests -m "not spark"` – static: table definitions, DDL, notebook structure
- `uv run pytest databricks/tests -m spark` – parsers on real ESPN fixtures (golden values from game 401872931) + Delta merge semantics; needs Java 17 (skips if absent)
- Fixtures in `tests/fixtures/espn/` are generated by `azure/scripts/refresh-espn-fixtures.js` — don't hand-edit them.

If data isn't showing up in `data-service`, check whether these notebooks have run recently, not just whether `azure/` produced fresh blobs.

## Module: webapp/ (Flask Chat UI)

**Purpose**: A web application providing a chat interface for querying NFL data. Uses Claude's API with tool calling to access the Delta Lake data service.

**Dev**:
- `uv run --directory webapp python app.py` – run the Flask server; open **http://127.0.0.1:5000** (on macOS, `localhost:5000` can hit AirPlay Receiver and return 403 — use 127.0.0.1 or set `FLASK_PORT`)
- `uv run --directory webapp pytest` – 2 smoke tests
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

- `pytest`/`ruff` are in the root `[dependency-groups] dev`; a plain `uv sync` or `uv sync --all-packages` installs only the default group and **uninstalls** `shared` (pandas, requests) and `databricks` (pyspark). Use `uv sync --all-packages --all-groups`.
- The repo lives in iCloud-synced `~/Documents`: the venv is `.venv.nosync/` (iCloud skips `*.nosync`) with a `.venv` symlink. Inside iCloud, `.pth` files got the macOS hidden flag (Python 3.13+ ignores hidden `.pth`, breaking the editable `data_service` import) and `* 2` conflict copies corrupted pandas. If recreating: `rm -rf .venv .venv.nosync && UV_PROJECT_ENVIRONMENT=.venv.nosync uv sync --all-packages --all-groups && ln -s .venv.nosync .venv`.
- CI (`.github/workflows/data-service.yml`, `webapp.yml`) runs `uv run ruff format --check`, `uv run ruff check`, and `uv run pytest` — all of these currently fail in CI for the same reason.
- `.github/workflows/etl.yml` runs `python etl/tests/test_clean.py` on every `etl/**` change — this is a live ~10-20 minute ESPN API scrape, not a fast validation step (see `etl/` section above).
- No `ruff.toml` or `[tool.ruff]` config exists anywhere, so there's no defined formatting standard to enforce yet. Don't assume a `ruff format` post-edit hook is configured for this repo — no hook exists in this repo's `.claude/settings.local.json` (only a permissions allowlist is defined there).

## Testing

Per-module commands (see "Known issues" above for what currently doesn't work):
- **azure/**: `cd azure && npm test` (landing builder + contract tests)
- **databricks/**: `uv run pytest databricks/tests` (Spark tests need Java 17)
- **data-service/**: `uv run --directory data-service pytest`
- **etl/**: `python etl/tests/test_clean.py` (currently a live ESPN scraper, not a validator)
- **webapp/**: `uv run --directory webapp pytest`

Or via CI: `.github/workflows/` per service (same caveats apply).
