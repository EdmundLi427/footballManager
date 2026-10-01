# Databricks Notebooks & Testing

This directory contains PySpark/Databricks notebooks for ingesting NFL data into Delta Lake tables, along with a comprehensive test suite organized in three layers.

## Notebooks

### `ingest_game_data.ipynb` (Legacy CSV)
Auto Loader notebook that reads raw NFL CSVs from Azure Blob Storage, applies snake_case column transformation, and writes to Delta tables under `football_manager.nfl` catalog.

### `ingest_news_data.ipynb` (Injury News)
Reads FantasyPros injury news JSON snapshots from Azure Blob (`injury/` prefix), flattens the items array, applies trim/timestamp parsing, and upserts into `nfl.injury_news` table with deduplication and merge semantics.

### `ingest_game_*.ipynb` (7 ESPN Data Ingestion Notebooks)
Each notebook handles a specific ESPN data source:
- `ingest_game_games.ipynb` → `nfl.games` (game summaries with venue/attendance)
- `ingest_game_teams.ipynb` → `nfl.teams` (team metadata)
- `ingest_game_players.ipynb` → `nfl.players` (player bio data)
- `ingest_game_rosters.ipynb` → `nfl.rosters` (active rosters per team, composite key: teamId + playerId)
- `ingest_game_schedules.ipynb` → `nfl.schedules` (game schedules)
- `ingest_game_standings.ipynb` → `nfl.standings` (team standings, composite key: teamId + season)
- `ingest_game_team_stats.ipynb` → `nfl.game_team_stats` (per-game team stats, composite key: gameId + teamId)

All game notebooks follow the same pattern:
1. Load JSON from Azure Blob Auto Loader
2. Apply schema validation
3. Extract/transform columns (e.g., explode arrays, build stats maps)
4. Deduplicate per composite key, keeping latest `snapshot_at`
5. Upsert into Delta table with merge condition: update only if source `snapshot_at > target snapshot_at`

## Testing Architecture

The test suite is organized in **three layers** to balance coverage, speed, and environment requirements.

### Layer 1: Unit Tests (Pure Python, no Spark)
**File:** `databricks/tests/test_transforms_unit.py`  
**Command:** `uv run pytest databricks/tests/test_transforms_unit.py`

Tests the extracted pure Python logic:
- `to_snake()` column name transformation
- Table name derivation from filenames
- Schema definitions (verify all expected fields present)
- `TABLE_KEYS` mapping (composite keys per table)

**Why:** Fast, deterministic, runs anywhere.  
**Coverage:** Utility functions, schema contracts, key definitions.

### Layer 2: Integration Tests (PySpark + Delta, requires Java 17)
**File:** `databricks/tests/test_transforms_integration.py`  
**Command:** `uv run pytest databricks/tests/test_transforms_integration.py -m spark`

Tests DataFrame transforms and merge semantics with real Spark:
- `dedupe_latest()` with single and composite keys
- `flatten_injury()` (explode, trim, timestamp parsing)
- `build_stats_map()` (dynamic stat extraction)
- `upsert_latest()` merge behavior:
  - Inserts new records
  - Updates on newer `snapshot_at`
  - Ignores older `snapshot_at` (idempotent)
  - Respects composite keys (rosters, standings, game_team_stats)

**Contract tests** verify fixture data has non-null key columns.

**Why:** Validates the core merge logic that ensures data consistency and idempotency.  
**Requires:** Java 17 (PySpark runtime).  
**Skips gracefully** when Java unavailable (e.g., local dev without Java).

### Layer 3: Static Notebook Checks (JSON parsing, no execution)
**File:** `databricks/tests/test_notebooks_structure.py`  
**Command:** `uv run pytest databricks/tests/test_notebooks_structure.py`

Validates notebook files without executing:
- All notebooks parse as valid JSON
- Each notebook has code cells
- Notebook naming follows `ingest_*.ipynb` pattern
- Notebooks reference composite keys for multi-key tables (e.g., `gameId`, `teamId`)
- No hardcoded credentials or secrets
- Merge operations use `snapshot_at` in conditions

**Why:** Catches structural issues (malformed notebooks, schema misalignment) before cluster runs.  
**Coverage:** Notebook structure, naming conventions, security (no credentials).

## Extracted Shared Library

**File:** `databricks/lib/transforms.py`

Extracted functions used by all notebooks:
- **Utilities:** `to_snake()`, `table_name_from_file()`, `is_csv()`
- **Schemas:** `injury_file_schema()`, `games_schema()`, `rosters_schema()`, etc. (return `StructType`)
- **TABLE_KEYS:** Dict mapping table names to composite keys
- **Transforms:** 
  - `snake_case_columns(df)` – rename columns
  - `add_audit_columns(df)` – add `_source_file` and `_ingested_at`
  - `flatten_injury(df)` – explode items, trim, parse timestamps
  - `build_stats_map(df, known_cols)` – extract dynamic stats into `MAP<STRING, STRING>`
  - `dedupe_latest(df, keys)` – row_number dedupe, keep latest `snapshot_at`
  - `upsert_latest(spark, batch_df, table, keys)` – Delta merge with snapshot semantics

**Why extracted:** Single source of truth, easier to test and maintain than pasted code across 8 notebooks.  
**Notebooks still use:** Auto Loader `cloudFiles` readers, checkpoint/write paths, `dbutils`, and `CREATE SCHEMA/TABLE` DDL (cluster-specific).

## Test Fixtures

**Directory:** `databricks/tests/fixtures/`

Sample JSON data matching the actual Azure Blob output:
- `injury_news.json` – FantasyPros injury response
- `games.json` – ESPN game summaries
- `rosters.json` – ESPN team rosters
- `standings.json` – ESPN team standings with dynamic stats
- `game_team_stats.json` – ESPN per-game team statistics

Used by Layer 2 tests to verify transforms without fetching live data.

## Running Tests

### All tests (Layer 1, 3, and Layer 2 if Java available)
```bash
uv run pytest databricks/tests/ -v
```

### Layer 1 + 3 only (no Java required)
```bash
uv run pytest databricks/tests/ -m "not spark" -v
```

### Layer 2 only (Spark + Java required)
```bash
uv run pytest databricks/tests/ -m "spark" -v
```

### Single test file
```bash
uv run pytest databricks/tests/test_transforms_unit.py -v
```

### Lint notebooks
```bash
uv run ruff check databricks/
uv run ruff format --check databricks/
```

## Dependencies

**In `pyproject.toml`:**
- `pyspark>=3.5.0` – DataFrame and SQL API
- `delta-spark>=3.1.0` – Delta Lake merge/upsert operations
- `pytest` – test framework (already in `dev` group)

**To install:**
```bash
uv sync --all-groups  # Includes databricks group
```

## CI/CD

**File:** `.github/workflows/databricks.yml`

Triggers on changes to `databricks/**`, `pyproject.toml`, or `uv.lock`.

Runs:
1. **Ruff checks** (format + lint)
2. **Layer 1 & 3 tests** (required, no Java)
3. **Layer 2 Spark tests** (optional, with Java 17)

If Layer 1 & 3 fail, the workflow fails.  
Spark tests can fail without blocking the workflow (flaky in CI).

## Key Bugs Fixed

1. **gameId in game_team_stats (✅ Fixed)**
   - `pluckBoxscoreTeamStats` now includes `gameId` from the parent game fetch
   - The composite key `(gameId, teamId)` now has both fields present
   - Added Azure test to verify the fix

## Known Issues / Future Work

1. **Status type mismatch:** ESPN `status` may be an object (`competition.status.type`) but notebooks declare it as string. Notebooks handle this via Auto Loader schema inference; Layer 2 tests should verify real payloads.

2. **Standings dead code:** The `ingest_game_standings` notebook has an unused `standings` variable in cell 4 (uses invalid `F.filter`/`F.isin` API). Only `standings_fixed` is written. Could clean up unused cell.

3. **Catalog inconsistency:** `ingest_game_data` uses `football_manager.nfl`, while others use bare `nfl.` schema. Standardizing would simplify queries.

4. **No live integration test:** To fully validate on a Databricks cluster, run the notebooks with the extracted `lib/transforms.py` imported via Databricks Repos or `%run /Repos/...`.

## Debugging Tips

- **Spark tests skip with Java error:** Install Java 17 or skip with `pytest -m "not spark"`
- **Layer 2 fixture mismatch:** Update `databricks/tests/fixtures/*.json` to match actual blob payloads
- **Notebook import error:** Ensure `databricks/lib/__init__.py` exists
- **Merge condition not working:** Verify `snapshot_at` column is present and a TIMESTAMP type

## References

- [Databricks Delta Merge](https://docs.databricks.com/en/delta/merge.html)
- [PySpark Structured Streaming](https://spark.apache.org/docs/latest/structured-streaming-programming-guide.html)
- [Azure Databricks Auto Loader](https://docs.databricks.com/en/ingestion/cloud-object-storage/auto-loader/index.html)
