# Pipeline Testing Guide

Test the entire flow from Azure Functions → Databricks → Delta → data-service

## Layer 1: Unit Tests (Local, Fast)
```bash
cd /Users/edmundli/Documents/Projects/footballManager/azure
npm test                                    # 33 tests, validates Azure function outputs
```

Verifies: Azure functions output correct snake_case field names and values.

---

## Layer 2: Schema Contract Tests (Local)
```bash
uv run --directory databricks pytest databricks/tests/test_schema_contract.py -v
uv run --directory databricks pytest databricks/tests/test_pipeline_e2e.py -v -m "not spark"
```

Verifies:
- Azure output schema matches Databricks notebook input schemas
- Conform functions produce expected output types
- Pair-splitting, time parsing, null logic work correctly
- Audit columns present in all tables

---

## Layer 3: Spark Integration Tests (Requires Java 17)
```bash
uv run --directory databricks pytest databricks/tests/test_pipeline_e2e.py -v -m spark
uv run --directory databricks pytest databricks/tests/test_transforms_integration.py -v -m spark
```

Verifies:
- Conform functions work with PySpark DataFrames
- Merge semantics correct on composite keys
- Deduplication works per game/game-team combo

---

## Layer 4: Databricks Cluster Integration Test (Manual)

### 4a. Deploy Test Data to Blob
```bash
# Upload sample JSON files from fixtures to test paths
az storage blob upload-batch \
  -s databricks/tests/fixtures \
  -d alsource \
  --account-name footballmanagerli \
  --destination-path test/schedules
```

### 4b. Run Notebooks on Cluster
1. Create a test workspace in Databricks
2. Clone the repo or upload notebooks
3. Update notebook paths to read from `test/schedules`, `test/games`, etc.
4. Run: `ingest_game_schedules.ipynb` → check `nfl.schedules_test` table
5. Verify output schema and row count

### 4c. Validate Delta Tables
```sql
-- In Databricks SQL
DESCRIBE nfl.schedules_test;
SELECT COUNT(*), COUNT(DISTINCT game_id) FROM nfl.schedules_test;
SELECT game_id, week, home_team, away_team, home_score FROM nfl.schedules_test LIMIT 5;
```

Expected:
- Columns: season, game_id, date, week, home_team_id, home_team, home_score, etc. (all snake_case)
- Data types: INT, STRING, INT (not STRUCT or JSON)
- Scores are integers (not 0 for unplayed games)

---

## Layer 5: End-to-End Test (Full Deployment)

### 5a. Run Azure Function
```bash
# Trigger the Azure function (or wait for scheduled run)
# Monitor Application Insights for logs
# Verify blobs uploaded to alsource/schedules/, alsource/games/, etc.
```

### 5b. Run Databricks Notebooks
```sql
-- In Databricks, run:
%run /Repos/.../ingest_game_schedules.ipynb
%run /Repos/.../ingest_game_games.ipynb
%run /Repos/.../ingest_game_team_stats.ipynb
```

### 5c. Query data-service
```python
# In webapp shell or Python REPL
from data_service.server import query

result = query("SELECT COUNT(*) as cnt FROM nfl.schedules WHERE week = 1")
print(result)  # Should show row count from latest Azure run
```

### 5d. Smoke Test in Webapp
```
# Start webapp
uv run --directory webapp python app.py

# Query via chatbot
User: "How many games in week 1?"
Expected: Chatbot reads nfl.schedules, counts rows where week=1
```

---

## Troubleshooting the Pipeline

### Azure → Blob
```bash
# Check blob contents
az storage blob download \
  --account-name footballmanagerli \
  -c alsource \
  -n schedules/2026-10-01/1234567890.json \
  -o json | jq '.' | head -50
```

**Expected format:**
```json
[
  {
    "season": 2026,
    "game_id": "game_401547439",
    "date": "2026-10-01T20:20Z",
    "week": 5,
    "home_team_id": "12",
    "home_team": "Kansas City Chiefs",
    "home_score": 21,
    ...
  }
]
```

### Blob → Databricks Read
```sql
-- In Databricks, debug the cloudFiles read
%python
from pyspark.sql import functions as F

raw = (spark.readStream
    .format("cloudFiles")
    .option("cloudFiles.format", "json")
    .option("multiLine", "true")
    .load("abfss://alsource@footballmanagerli.dfs.core.windows.net/schedules/")
)

# Display schema
raw.printSchema()
raw.limit(1).display()
```

**Expected:** JSON is readable, fields match input schema (season, game_id, date, etc.)

### Conform Function Output
```sql
%python
from transforms import conform_schedules

conformed = conform_schedules(raw)
conformed.printSchema()
conformed.limit(1).display()
```

**Expected:** Output schema matches target_schemas['schedules']

### Delta Merge
```sql
-- Check if merge succeeded
DESCRIBE EXTENDED nfl.schedules;

-- Look for:
-- Location: abfss://...
-- Provider: delta
-- Last modified: recent timestamp
```

---

## Validation Checklist

- [ ] Azure tests pass: `npm test` (33/33)
- [ ] Schema contract tests pass: pytest `test_schema_contract.py`
- [ ] E2E tests pass (no Spark): pytest `test_pipeline_e2e.py -m "not spark"`
- [ ] E2E Spark tests pass: pytest `test_pipeline_e2e.py -m spark` (requires Java 17)
- [ ] Blob JSON validates: inspect with Azure CLI
- [ ] Databricks notebook runs without errors
- [ ] Delta table schema matches target_schemas
- [ ] Row counts match expectations
- [ ] data-service can query table and see columns
- [ ] Webapp chatbot can query NFL data

---

## When to Run Each Layer

| Scenario | Layers to Run |
|----------|---------------|
| After changing Azure pluck functions | 1, 2 |
| After changing conform functions | 1, 2, 3 |
| Before deploying to cluster | 1, 2, 3 |
| After deploying notebooks to cluster | 4a, 4b, 4c |
| After full deployment | 5a, 5b, 5c, 5d |

---

## Continuous Testing

Add to CI pipeline (`.github/workflows/databricks.yml`):
1. Run Azure tests
2. Run schema contract tests (always)
3. Run Spark tests (if Java 17 available)
4. (Manual step) Deploy test data, run notebooks, validate
