# databricks/

PySpark notebooks and a small tested library that turn the raw blobs landed by `azure/` into the `nfl.*` Delta tables that `data-service/` queries.

**Column-by-column transform reference: [TRANSFORMS.md](TRANSFORMS.md).**

## Layout

```
lib/
  schemas.py      raw read schemas (*_RAW) + silver table definitions (TABLES): columns, types, comments, keys
  transforms.py   parse_<table>(): pure DataFrame → DataFrame parsers, no I/O
  delta_io.py     ensure_table() DDL from TABLES, dedupe + idempotent MERGE, foreachBatch helper
notebooks/
  ingest_game_{teams,players,rosters,standings,schedules,games,team_stats}.ipynb   ESPN → nfl.<table>
  ingest_news_data.ipynb     FantasyPros injury news → nfl.injury_news
  reset_espn_tables.ipynb    guarded: drops ESPN tables + checkpoints for a full rebuild
tests/
  fixtures/espn/             real ESPN landing payloads (recorded by azure/scripts/refresh-espn-fixtures.js)
  fixtures/fantasypros/      real FantasyPros injury snapshot (also recorded by the refresh script)
  test_schemas.py            static: keys NOT NULL, snake_case, audit columns, DDL
  test_transforms.py         Spark: each parser on real fixtures; exact schema + golden values
  test_delta_io.py           Spark: merge semantics (newer wins, older ignored, idempotent, null key rejected)
  test_notebooks_structure.py static: notebooks stay thin wrappers over lib
```

## How a notebook runs

Every ingest notebook is the same four cells:

1. Put `databricks/` on `sys.path` (the notebook's CWD is `databricks/notebooks/` in a Git folder) and import `lib`.
2. Pick `TABLE = schemas.TABLES["<name>"]`. The source prefix, keys and schema all come from it.
3. `ensure_table` → Auto Loader `readStream` with `TABLE.raw_schema` → `transforms.parse_<name>` → `foreachBatch(delta_io.upsert_batch_fn(...))` with `trigger(availableNow=True)`.
4. Sanity check: row count equals distinct key count.

Paths: source `abfss://alsource@footballmanagerli.../<prefix>/`, data `abfss://aldestination@.../silver/<table>`, checkpoint `.../_checkpoints/silver_<table>`. `injury_news` keeps its original `injury/injury_news` location and checkpoint.

Run order doesn't matter; each notebook is independent. `ingest_game_games` and `ingest_game_team_stats` both read `espn/game_summaries/` with separate checkpoints.

## First deployment of this layout

The earlier layout wrote broken data (see git history for this change), so rebuild:

1. Deploy `azure/` and let each timer run once so `espn/*` blobs exist.
2. Run `notebooks/reset_espn_tables.ipynb` with the `confirm` widget set to `RESET`. It drops the 7 ESPN tables, their checkpoints and the old data paths. `nfl.injury_news` is untouched.
3. Run each `ingest_game_*` notebook and check its sanity cell.

## Tests

```bash
uv sync --all-groups
uv run pytest databricks/tests -m "not spark"    # static, no Java needed
uv run pytest databricks/tests -m spark          # needs Java 17 (brew install openjdk@17)
uv run ruff check databricks/ && uv run ruff format --check databricks/
```

Spark tests skip automatically if Java is missing. See [PIPELINE_TESTING.md](PIPELINE_TESTING.md) for the full pipeline test procedure.
