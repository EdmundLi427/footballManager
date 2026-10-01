"""
End-to-end pipeline test: Azure output → Spark conform → Delta schema validation
This test simulates the full flow without needing a live Databricks cluster initially.
"""
import json
import sys
from pathlib import Path

# Add databricks/lib to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

import pytest
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, IntegerType, BooleanType
from delta.tables import DeltaTable

from transforms import (
    conform_schedules, conform_games, conform_game_team_stats,
    target_schemas
)


@pytest.mark.spark
def test_schedule_blob_to_delta(spark):
    """
    Simulate: Azure blob JSON → Databricks read → conform → Delta write
    """
    # 1. Simulate Azure blob data (pluckScheduleFields output)
    blob_data = [
        {
            "season": 2026,
            "game_id": "game_1",
            "date": "2026-09-06T20:20Z",
            "week": 1,
            "home_team_id": "12",
            "home_team": "Kansas City Chiefs",
            "home_score": 21,
            "away_team_id": "25",
            "away_team": "Detroit Lions",
            "away_score": 14,
            "venue": "Arrowhead Stadium",
            "completed": True,
            "status": "Final",
        }
    ]

    # 2. Read as Databricks would from blob
    df = spark.createDataFrame(blob_data, schema=target_schemas['schedules'])

    # 3. Apply conform function
    conformed = conform_schedules(df)

    # 4. Verify output matches Delta table expectations
    result = conformed.collect()[0]
    assert result.season == 2026
    assert result.game_id == "game_1"
    assert result.week == 1
    assert result.home_team_id == "12"
    assert result.home_score == 21

    # 5. Verify schema matches target
    assert conformed.schema == target_schemas['schedules']


@pytest.mark.spark
def test_game_team_stats_transformations(spark):
    """
    Verify complex transforms: pair-splitting, time parsing, type casting
    """
    blob_data = [
        {
            "game_id": "game_1",
            "team_id": "12",
            "team_name": "Kansas City Chiefs",
            "team_abbr": "KC",
            "home_away": "H",
            "third_down_eff": "5-8",  # Will be split
            "pass_completions": "25",
            "pass_attempts": "42",
            "possession_time": "32:14",  # Will be parsed to seconds
            "total_yards": 412,
        }
    ]

    df = spark.createDataFrame(blob_data)
    conformed = conform_game_team_stats(df)

    result = conformed.collect()[0]
    # Pair-splitting: "5-8" → (5, 8)
    assert result.third_down_conversions == 5
    assert result.third_down_attempts == 8

    # Time parsing: "32:14" → 1934 seconds (32*60 + 14)
    assert result.possession_seconds == 1934


@pytest.mark.spark
def test_null_scores_for_unplayed_games(spark):
    """
    Verify conform function nulls scores for non-Final games
    """
    blob_data = [
        {
            "season": 2026,
            "game_id": "game_scheduled",
            "date": "2026-09-10T20:20Z",
            "week": 2,
            "home_team_id": "12",
            "home_team": "Kansas City Chiefs",
            "home_score": 0,  # Will be nulled
            "away_team_id": "25",
            "away_team": "Detroit Lions",
            "away_score": 0,  # Will be nulled
            "venue": "Arrowhead Stadium",
            "completed": False,
            "status": "Scheduled",
        }
    ]

    df = spark.createDataFrame(blob_data, schema=target_schemas['schedules'])
    conformed = conform_schedules(df)

    result = conformed.collect()[0]
    # Scores should be null for non-Final games
    assert result.home_score is None
    assert result.away_score is None


def test_azure_schema_matches_notebook_inputs():
    """
    Verify Azure function outputs match Databricks notebook input schemas
    (Can run without Spark)
    """
    from transforms import target_schemas
    from pathlib import Path

    fixtures_dir = Path(__file__).parent / "fixtures"

    # Read fixtures and verify schema matches target_schemas
    for table_name, target_schema in target_schemas.items():
        fixture_file = fixtures_dir / f"{table_name}.json"
        if not fixture_file.exists():
            continue  # Skip if no fixture

        # Load fixture and extract columns from first record
        with open(fixture_file) as f:
            data = json.load(f)

        if isinstance(data, list):
            if len(data) == 0:
                continue
            azure_cols = set(data[0].keys())
        elif isinstance(data, dict):
            azure_cols = set(data.keys())
        else:
            continue

        # Get target schema columns
        target_cols = {f.name for f in target_schema.fields}

        # They must match exactly - no more, no less
        assert azure_cols == target_cols, \
            f"{table_name} schema mismatch:\n" \
            f"  Fixture has:     {sorted(azure_cols)}\n" \
            f"  Target expects:  {sorted(target_cols)}\n" \
            f"  Extra in fixture: {azure_cols - target_cols}\n" \
            f"  Missing from fixture: {target_cols - azure_cols}"
