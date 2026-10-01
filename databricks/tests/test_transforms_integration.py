"""
Layer 2: Integration tests using PySpark (requires Java and Spark).

Tests the DataFrame transform functions against real Delta tables.
Marked with @pytest.mark.spark to allow skipping when Spark is unavailable.
"""

import pytest
import json
from pathlib import Path
from datetime import datetime

pytestmark = pytest.mark.spark


@pytest.fixture
def spark_fixture(spark):
    """Yield a SparkSession for tests."""
    return spark


class TestDedupeLatest:
    """Tests for the dedupe_latest function."""

    def test_dedupe_single_key_keeps_latest_snapshot(self, spark_fixture):
        """Duplicates should keep the record with latest snapshot_at."""
        from lib.transforms import dedupe_latest

        spark = spark_fixture

        # Create test data with duplicates (same id, different snapshot_at)
        data = [
            {"id": "1", "name": "Alice", "snapshot_at": "2026-10-01T10:00:00Z"},
            {"id": "1", "name": "Alice Updated", "snapshot_at": "2026-10-01T11:00:00Z"},
            {"id": "2", "name": "Bob", "snapshot_at": "2026-10-01T10:30:00Z"},
        ]

        df = spark.createDataFrame(data)
        result = dedupe_latest(df, ["id"])

        rows = result.collect()
        assert len(rows) == 2  # Only 2 unique ids

        # Check that id=1 kept the later snapshot
        id1_rows = [r for r in rows if r.id == "1"]
        assert len(id1_rows) == 1
        assert id1_rows[0].name == "Alice Updated"

    def test_dedupe_composite_key(self, spark_fixture):
        """Should handle composite keys correctly."""
        from lib.transforms import dedupe_latest

        spark = spark_fixture

        # Rosters: (teamId, playerId) is the key
        data = [
            {"teamId": "12", "playerId": "100", "name": "Player A", "snapshot_at": "2026-10-01T10:00:00Z"},
            {"teamId": "12", "playerId": "100", "name": "Player A v2", "snapshot_at": "2026-10-01T11:00:00Z"},
            {"teamId": "12", "playerId": "101", "name": "Player B", "snapshot_at": "2026-10-01T10:30:00Z"},
            {"teamId": "25", "playerId": "100", "name": "Player C", "snapshot_at": "2026-10-01T10:45:00Z"},
        ]

        df = spark.createDataFrame(data)
        result = dedupe_latest(df, ["teamId", "playerId"])

        rows = result.collect()
        assert len(rows) == 3  # 3 unique (teamId, playerId) pairs

        # Verify (12, 100) kept the latest
        t12_p100 = [r for r in rows if r.teamId == "12" and r.playerId == "100"]
        assert len(t12_p100) == 1
        assert t12_p100[0].name == "Player A v2"

    def test_dedupe_with_empty_df(self, spark_fixture):
        """Should handle empty DataFrames."""
        from lib.transforms import dedupe_latest

        spark = spark_fixture

        # Create empty DataFrame with schema
        from pyspark.sql.types import StructType, StructField, StringType
        schema = StructType([
            StructField("id", StringType()),
            StructField("snapshot_at", StringType()),
        ])
        df = spark.createDataFrame([], schema)

        result = dedupe_latest(df, ["id"])
        assert result.count() == 0


class TestGameTeamStatsKeyPresence:
    """Contract test: verify gameId is present in game_team_stats data."""

    def test_game_team_stats_fixture_has_gameId(self, fixture_dir):
        """The fixture should include gameId in each record."""
        fixture_file = fixture_dir / "game_team_stats.json"

        with open(fixture_file) as f:
            data = json.load(f)

        assert isinstance(data, list)
        assert len(data) > 0

        # Every record must have gameId
        for record in data:
            assert "gameId" in record, f"Record missing gameId: {record}"
            assert record["gameId"] is not None
            assert "teamId" in record
            assert record["teamId"] is not None

    def test_game_team_stats_composite_key_non_null(self, spark_fixture, fixture_dir):
        """Both gameId and teamId must be non-null in the DataFrame."""
        spark = spark_fixture

        fixture_file = fixture_dir / "game_team_stats.json"
        with open(fixture_file) as f:
            data = json.load(f)

        df = spark.createDataFrame(data)

        # Check no nulls in key columns
        gameId_nulls = df.filter(df.gameId.isNull()).count()
        teamId_nulls = df.filter(df.teamId.isNull()).count()

        assert gameId_nulls == 0, "gameId column has null values"
        assert teamId_nulls == 0, "teamId column has null values"


class TestRostersCompositeKey:
    """Contract test: rosters must have non-null teamId and playerId."""

    def test_rosters_composite_key_non_null(self, spark_fixture, fixture_dir):
        """Both teamId and playerId must be non-null."""
        spark = spark_fixture

        fixture_file = fixture_dir / "rosters.json"
        with open(fixture_file) as f:
            data = json.load(f)

        df = spark.createDataFrame(data)

        teamId_nulls = df.filter(df.teamId.isNull()).count()
        playerId_nulls = df.filter(df.playerId.isNull()).count()

        assert teamId_nulls == 0, "teamId column has null values"
        assert playerId_nulls == 0, "playerId column has null values"


class TestStandingsCompositeKey:
    """Contract test: standings must have non-null teamId and season."""

    def test_standings_fixture_has_season(self, fixture_dir):
        """The standings fixture should include season."""
        fixture_file = fixture_dir / "standings.json"

        with open(fixture_file) as f:
            data = json.load(f)

        for record in data:
            assert "teamId" in record and record["teamId"] is not None
            assert "season" in record and record["season"] is not None


class TestFlattenInjury:
    """Tests for the flatten_injury transform."""

    def test_flatten_injury_explodes_items(self, spark_fixture, fixture_dir):
        """Should explode items array and trim fields."""
        from lib.transforms import flatten_injury

        spark = spark_fixture

        # Load the injury fixture
        fixture_file = fixture_dir / "injury_news.json"
        with open(fixture_file) as f:
            data = json.load(f)

        # Create DataFrame with the injury response
        df = spark.createDataFrame([data])

        # Add source_file and snapshot_at columns (simulating _metadata)
        from pyspark.sql.functions import lit
        df = df.withColumn("source_file", lit("s3://bucket/injury/2026-10-01/iso.json"))
        df = df.withColumn("snapshot_at", lit("2026-10-01T10:00:00Z"))

        result = flatten_injury(df)

        # Should have exploded the 2 items
        assert result.count() == 2

        # Check columns
        col_names = result.columns
        assert "id" in col_names
        assert "player_id" in col_names
        assert "description" in col_names  # was "desc"
        assert "created_at" in col_names  # was "created"
        assert "source_file" in col_names
        assert "ingested_at" in col_names

        # Verify data integrity
        rows = result.collect()
        ids = sorted([r.id for r in rows])
        assert ids == [1001, 1002]


class TestBuildStatsMap:
    """Tests for the build_stats_map function."""

    def test_build_stats_map_extracts_dynamic_stats(self, spark_fixture, fixture_dir):
        """Should extract non-known columns into a stats map."""
        from lib.transforms import build_stats_map

        spark = spark_fixture

        # Load game_team_stats fixture
        fixture_file = fixture_dir / "game_team_stats.json"
        with open(fixture_file) as f:
            data = json.load(f)

        df = spark.createDataFrame(data)

        # Add audit columns to define known_cols
        from pyspark.sql.functions import lit
        df = df.withColumn("source_file", lit("s3://bucket/game-team-stats/2026-10-01/iso.json"))
        df = df.withColumn("snapshot_at", lit("2026-10-01T10:00:00Z"))
        df = df.withColumn("ingested_at", lit("2026-10-01T10:30:00Z"))

        known_cols = ["gameId", "teamId", "teamName", "teamAbbr", "source_file", "snapshot_at", "ingested_at"]
        result = build_stats_map(df, known_cols)

        # Should have stats column
        assert "stats" in result.columns

        # Known columns should still exist
        for col in ["gameId", "teamId", "teamName", "teamAbbr"]:
            assert col in result.columns

        # Original stat columns should be dropped
        assert "firstDowns" not in result.columns
        assert "totalYards" not in result.columns

        # Verify stats map contains the stats
        rows = result.collect()
        for row in rows:
            stats = row.stats
            assert isinstance(stats, dict)
            # Should contain the stat fields
            assert any("firstDowns" in k for k in stats.keys())


class TestMergeSemantics:
    """Tests for the upsert_latest merge behavior."""

    def test_upsert_insert_new_records(self, spark_fixture):
        """New records should be inserted."""
        from lib.transforms import upsert_latest
        from delta import DeltaTable
        import tempfile

        spark = spark_fixture

        with tempfile.TemporaryDirectory() as tmpdir:
            table_path = f"{tmpdir}/test_table"
            table_name = "test_upsert"

            # Create initial Delta table
            initial_data = [
                {"id": "1", "name": "Alice", "snapshot_at": "2026-10-01T10:00:00Z"},
            ]
            df_initial = spark.createDataFrame(initial_data)
            df_initial.write.format("delta").mode("overwrite").save(table_path)

            # Register the table
            spark.sql(f"CREATE TABLE IF NOT EXISTS {table_name} USING DELTA LOCATION '{table_path}'")

            # Prepare batch with a new record and an existing one (with newer snapshot)
            batch_data = [
                {"id": "1", "name": "Alice Updated", "snapshot_at": "2026-10-01T11:00:00Z"},
                {"id": "2", "name": "Bob", "snapshot_at": "2026-10-01T10:30:00Z"},
            ]
            df_batch = spark.createDataFrame(batch_data)

            # Perform upsert
            upsert_latest(spark, df_batch, table_name, ["id"])

            # Verify results
            result = spark.sql(f"SELECT * FROM {table_name} ORDER BY id").collect()
            assert len(result) == 2  # Both records should be present

            # Check updates
            alice_rows = [r for r in result if r.id == "1"]
            assert alice_rows[0].name == "Alice Updated"

            # Check inserts
            bob_rows = [r for r in result if r.id == "2"]
            assert bob_rows[0].name == "Bob"

            spark.sql(f"DROP TABLE {table_name}")

    def test_upsert_ignores_older_snapshots(self, spark_fixture):
        """Older snapshots should be ignored (not update)."""
        from lib.transforms import upsert_latest
        from delta import DeltaTable
        import tempfile

        spark = spark_fixture

        with tempfile.TemporaryDirectory() as tmpdir:
            table_path = f"{tmpdir}/test_table"
            table_name = "test_upsert_older"

            # Create initial Delta table with newer snapshot
            initial_data = [
                {"id": "1", "name": "Alice Latest", "snapshot_at": "2026-10-01T11:00:00Z"},
            ]
            df_initial = spark.createDataFrame(initial_data)
            df_initial.write.format("delta").mode("overwrite").save(table_path)

            spark.sql(f"CREATE TABLE IF NOT EXISTS {table_name} USING DELTA LOCATION '{table_path}'")

            # Try to upsert with an older snapshot
            batch_data = [
                {"id": "1", "name": "Alice Old", "snapshot_at": "2026-10-01T10:00:00Z"},
            ]
            df_batch = spark.createDataFrame(batch_data)

            # Perform upsert
            upsert_latest(spark, df_batch, table_name, ["id"])

            # Verify it was NOT updated
            result = spark.sql(f"SELECT * FROM {table_name}").collect()
            assert result[0].name == "Alice Latest"  # Should keep the newer version

            spark.sql(f"DROP TABLE {table_name}")

    def test_upsert_composite_key(self, spark_fixture):
        """Should respect composite keys correctly."""
        from lib.transforms import upsert_latest
        import tempfile

        spark = spark_fixture

        with tempfile.TemporaryDirectory() as tmpdir:
            table_path = f"{tmpdir}/test_roster"
            table_name = "test_roster_upsert"

            # Create initial table with two rosters
            initial_data = [
                {"teamId": "12", "playerId": "100", "name": "Player A", "snapshot_at": "2026-10-01T10:00:00Z"},
                {"teamId": "25", "playerId": "100", "name": "Player C", "snapshot_at": "2026-10-01T10:00:00Z"},
            ]
            df_initial = spark.createDataFrame(initial_data)
            df_initial.write.format("delta").mode("overwrite").save(table_path)

            spark.sql(f"CREATE TABLE IF NOT EXISTS {table_name} USING DELTA LOCATION '{table_path}'")

            # Batch: update first, insert new, ignore old
            batch_data = [
                {"teamId": "12", "playerId": "100", "name": "Player A Updated", "snapshot_at": "2026-10-01T11:00:00Z"},
                {"teamId": "25", "playerId": "100", "name": "Player C Old", "snapshot_at": "2026-10-01T09:00:00Z"},
                {"teamId": "12", "playerId": "101", "name": "Player B", "snapshot_at": "2026-10-01T10:30:00Z"},
            ]
            df_batch = spark.createDataFrame(batch_data)

            # Perform upsert
            upsert_latest(spark, df_batch, table_name, ["teamId", "playerId"])

            # Verify results
            result = spark.sql(f"SELECT * FROM {table_name} ORDER BY teamId, playerId").collect()
            assert len(result) == 3

            # (12, 100) should be updated
            t12_p100 = [r for r in result if r.teamId == "12" and r.playerId == "100"]
            assert t12_p100[0].name == "Player A Updated"

            # (25, 100) should keep old value (older snapshot ignored)
            t25_p100 = [r for r in result if r.teamId == "25" and r.playerId == "100"]
            assert t25_p100[0].name == "Player C"

            # (12, 101) should be inserted
            t12_p101 = [r for r in result if r.teamId == "12" and r.playerId == "101"]
            assert t12_p101[0].name == "Player B"

            spark.sql(f"DROP TABLE {table_name}")
