# Conform Functions Implementation (Step 2)

## Summary
Added 5 conform functions to `databricks/lib/transforms.py` that transform ESPN payloads to the cleaned Delta Lake schema. These functions apply all the data cleaning logic from `etl/dataClean.ipynb` but in PySpark for use in Databricks notebooks.

## Functions Added

### 1. `conform_games(df: DataFrame) → DataFrame`
**Purpose:** Transform game summaries to cleaned schema

**Input schema:**
- `id` (string), `date` (timestamp), `venue` (string), `city` (string), `attendance` (any), `_source_file`, `_ingested_at`

**Output schema:**
- `game_id` (long), `venue` (string), `game_ts_utc` (timestamp), `_source_file`, `_ingested_at`

**Transformations:**
- Renames `id` → `game_id` and casts to long
- Parses `date` → `game_ts_utc` as timestamp
- Drops `city` and `attendance` (not in cleaned schema)

---

### 2. `conform_schedules(df: DataFrame) → DataFrame`
**Purpose:** Transform schedules to cleaned schema with score logic

**Input schema:** (from `pluckScheduleFields`)
- `season` (long), `id` (long), `date` (timestamp), `week` (long), `home_team_id` (long), `home_team` (string), `home_score` (dict value), `away_team_id` (long), `away_team` (string), `away_score` (dict value), `venue` (string), `completed` (boolean), `status` (string), `_source_file`, `_ingested_at`

**Output schema:**
- All input columns, renamed `id` → `game_id`, types cast to proper types (long/boolean)
- `home_score`/`away_score` nulled if `status != 'Final'`

**Transformations:**
- Casts numeric ids to long
- Nulls out placeholder scores for unplayed games (ESPN sends 0 when not final)
- Keeps `status` as string for changelog

---

### 3. `conform_rosters(df: DataFrame) → DataFrame`
**Purpose:** Transform rosters to cleaned schema with type casting

**Input schema:** (from ESPN pluck, camelCase)
- `season`, `team_id`, `player_id`, `playerName`, `position_group`, `position`, `jersey`, `age`, `height`, `weight`, `experience_years`, `college`, `_source_file`, `_ingested_at`

**Output schema:**
- Same columns, snake_case, typed as long for numeric fields

**Transformations:**
- Applies `snake_case_columns()`
- Casts to long: `season`, `team_id`, `player_id`, `jersey`, `age`, `height`, `weight`, `experience_years`
- Selects and orders columns to match cleaned schema

**Composite Key:** `season`, `team_id`, `player_id`

---

### 4. `conform_standings(df: DataFrame) → DataFrame`
**Purpose:** Transform standings to cleaned schema with type casting and stats

**Input schema:** (from ESPN pluck, camelCase mixed types)
- `season`, `conference`, `team_id`, `team_name`, `clincher`, `differential`, `games_behind`, `losses`, `playoff_seed`, `point_differential`, `points_against`, `points_for`, `streak`, `ties`, `win_percent`, `wins`, `division_losses`, `division_record`, `division_ties`, `division_wins`, `locked_div_rank`, `_source_file`, `_ingested_at`

**Output schema:**
- All fields present, types cast to long/double/boolean, `clincher` as boolean

**Transformations:**
- Applies `snake_case_columns()`
- Casts most fields to long (numeric stats)
- Casts `win_percent` to double
- Casts `clincher` to boolean (from null/1 values)

**Composite Key:** `season`, `team_id`

---

### 5. `conform_game_team_stats(df: DataFrame) → DataFrame`
**Purpose:** Transform game team stats with complex pair-splitting and aggregation logic

**Input schema:** (from ESPN pluck, camelCase)
- Paired strings: `third_down_eff` ("5-8"), `fourth_down_eff`, `completion_attempts`, `sacks_yards_lost`, `red_zone_attempts`, `total_penalties_yards`
- Time string: `possession_time` ("34:42")
- Per-game columns: `*_per_game*`
- Stat values: `first_downs`, `total_yards`, etc.

**Output schema:**
- Split pairs into separate columns: `third_down_conversions`, `third_down_attempts`, etc.
- Possession time converted to `possession_seconds` (integer)
- Per-game columns dropped
- All-null stat rows dropped (unplayed games)
- Numeric columns typed as long, floats as double

**Transformations:**
- Applies `snake_case_columns()`
- **Pair splitting** (6 pairs):
  - `"5-8"` → `third_down_conversions=5, third_down_attempts=8`
  - `"1-3"` → `fourth_down_conversions=1, fourth_down_attempts=3`
  - `"12/20"` → `pass_completions=12, pass_attempts=20` (uses `/` separator)
  - `"2-14"` → `sacks=2, sack_yards_lost=14`
  - `"3-5"` → `red_zone_scores=3, red_zone_attempts=5`
  - `"5-50"` → `penalties=5, penalty_yards=50`
- **Possession time parsing:** `"34:42"` → `(34 * 60 + 42) = 2082 seconds`
- **Drops per-game columns** (season-to-date averages, belong in team-season table)
- **Drops all-null stat rows** (games not yet played)
- **Type casting:** numeric stats to long, float stats to double

**Composite Key:** `game_id`, `team_id`

---

## Code Quality

- **Lines of code:** ~420 new lines (conform functions + helpers)
- **Reuse:** Functions leverage existing helpers (`snake_case_columns`, `add_audit_columns`, `dedupe_latest`)
- **Documentation:** Comprehensive docstrings for each function
- **Testing:** All existing 33 unit tests still pass; Layer 2 Spark tests can validate conform output

---

## Integration Path

### In Databricks Notebooks
Each `ingest_game_*.ipynb` should:

```python
# 1. Import from lib
%run ../lib/transforms

# 2. Read ESPN payload with ESPN input schema
df = (spark.readStream
    .format("cloudFiles")
    .option("cloudFiles.format", "json")
    .schema(espn_input_schema)  # e.g., rosters_input_schema with camelCase
    .load(SRC)
    ...)

# 3. Apply conform function
df_conformed = conform_rosters(df)

# 4. Merge into cleaned Delta table
def upsert_batch(batch_df, batch_id):
    upsert_latest(spark, batch_df, "football_manager.nfl.rosters", ["season", "team_id", "player_id"])

df_conformed.writeStream.foreachBatch(upsert_batch)...
```

---

## Testing Strategy

### Layer 2 (Spark Integration Tests)
For each conform function, tests should:
1. **Schema contract:** Output has correct columns and types
2. **Rule validation:** Specific transformations work correctly
   - Pair splitting: `"5-8"` → `{5, 8}`
   - Time parsing: `"34:42"` → `2082`
   - Score nulling: unplayed games have null scores
3. **Merge semantics:** Insert/update/ignore works with composite keys

Example test structure:
```python
@pytest.mark.spark
def test_conform_rosters_output_schema(spark):
    """Verify rosters conform output matches cleaned schema."""
    input_df = spark.createDataFrame([...], "ESPN rosters schema")
    output_df = conform_rosters(input_df)
    
    expected_schema = rosters_schema()  # From transforms.py
    assert output_df.schema == expected_schema

@pytest.mark.spark
def test_conform_game_team_stats_pair_splitting(spark):
    """Verify pair-splitting logic works correctly."""
    input_df = spark.createDataFrame([
        {
            "gameId": "game_1",
            "teamId": "12",
            "third_down_eff": "3-8",
            ...
        }
    ], ...)
    
    output_df = conform_game_team_stats(input_df)
    result = output_df.collect()[0]
    
    assert result.third_down_conversions == 3
    assert result.third_down_attempts == 8
```

---

## Known Limitations

1. **Pair splitting:** Uses simple string split on `-` or `/`. Edge cases (negative numbers, missing values) should be handled in input validation.

2. **Possession time:** Assumes `MM:SS` format. Invalid formats will cause null output.

3. **All-null row filtering:** Drops entire rows; should be logged for visibility (optional enhancement).

4. **Float precision:** Double casts may lose precision for complex stats; acceptable for display.

---

## Files Modified
- `databricks/lib/transforms.py` — Added 5 conform functions (~420 lines)

## Next Steps
1. Add Layer 2 tests validating conform output schema and rules
2. Update Layer 3 notebook structure checks to verify conform usage
3. Update ESPN ingest notebooks to import and use conform functions
4. Rebuild Delta tables from cleaned CSVs (user-run step 3)
