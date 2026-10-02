# Pipeline testing

Azure Functions → blob (`alsource/espn/*`, `alsource/injury/`) → Databricks notebooks → `nfl.*` Delta → data-service.

## 1. Azure landing builders (local, ~5s)

```bash
cd azure && npm test
```

- Each builder in `src/lib/espnAssemble.js`, applied to a recorded ESPN response in `test/fixture/espn/`, must produce **exactly** the payload in `databricks/tests/fixtures/espn/`. Those same payloads are the inputs to the Databricks parser tests, so this is the cross-module contract.
- `selectTargetGames` works on real ESPN status objects.

## 2. Databricks static tests (local, <1s)

```bash
uv run pytest databricks/tests -m "not spark"
```

Checks table definitions (keys NOT NULL, snake_case, audit columns, generated DDL) and notebook structure: shared `lib` used, no NULL-filling, no hardcoded user paths or credentials.

## 3. Databricks Spark tests (local, ~1-2 min, Java 17)

```bash
brew install openjdk@17   # once; or point JAVA_HOME at any JDK 17
uv run pytest databricks/tests -m spark
```

- Every `parse_<table>` on the real fixtures returns exactly `TABLES[table].schema`, with non-null unique keys.
- Golden values from DEN @ KC 2026 wk 1 (event 401872931). For example, KC is `home`, 9/17 on third down, 15/27 passing, `possession_seconds = 2022`, final score 31-10. The upcoming game has NULL scores.
- Merge semantics: insert, newer snapshot wins, older ignored, replay is idempotent, in-batch duplicates resolved, null key rejected.

CI (`.github/workflows/databricks.yml`) runs layers 2 and 3 plus ruff on Python 3.10–3.12. `azure.yml` runs layer 1, and also runs when the shared fixtures change.

## 4. Schema drift check (manual, needs network)

```bash
node azure/scripts/refresh-espn-fixtures.js
git diff azure/test/fixture/espn databricks/tests/fixtures/espn
cd azure && npm test && cd .. && uv run pytest databricks/tests
```

Re-records small real ESPN responses. A diff in the raw fixtures means ESPN changed its payload, and the tests tell you whether the builders and parsers still cope. If a golden value changes because ESPN corrected a stat, update the assertion.

## 5. On the cluster (manual)

1. Deploy `azure/` and confirm blobs appear under `alsource/espn/{teams,players,rosters,standings,schedules,game_summaries}/YYYY-MM-DD/`.
2. Run `notebooks/reset_espn_tables.ipynb` (`confirm=RESET`). This is only needed when the schema changes; see TRANSFORMS.md, "Changing a transform".
3. Run each `notebooks/ingest_game_*.ipynb`. The last cell asserts row count equals distinct key count.
4. Spot-check:
   ```sql
   SELECT * FROM nfl.game_team_stats WHERE game_id = '401872931';   -- KC home, 9/17 3rd down
   SELECT season_type, week, COUNT(*) FROM nfl.schedules GROUP BY ALL ORDER BY ALL;
   SELECT conference, COUNT(*) FROM nfl.standings GROUP BY ALL;    -- 16 / 16
   ```
5. From the webapp or data-service, `describe_table("nfl.schedules")` shows the snake_case columns.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Merge fails with a NOT NULL / null constraint error | A blob has a record without the key field: ESPN shape changed or Azure wrote a partial record. Re-run the drift check. |
| Table exists but a column is all NULL | The raw schema doesn't match ESPN anymore. Read the blob (`spark.read.json(path).printSchema()`), then update `*_RAW` and the parser. |
| `CREATE TABLE` fails with a schema mismatch at LOCATION | Old data exists at the silver path. Run `reset_espn_tables`. |
| No new rows | Check that Azure is writing under `espn/` (not the old `schedules/` and similar prefixes) and that the notebook checkpoint isn't stale. |
