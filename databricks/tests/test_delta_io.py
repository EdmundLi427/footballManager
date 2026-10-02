"""Merge semantics and table creation against a local Delta catalog."""

from datetime import datetime

import pytest
from lib import delta_io
from lib.schemas import TABLES

pytestmark = pytest.mark.spark

TEAMS = TABLES["teams"]
STATS = TABLES["game_team_stats"]


@pytest.fixture
def teams_table(spark):
    spark.sql(f"DROP TABLE IF EXISTS {TEAMS.full_name}")
    delta_io.ensure_table(spark, TEAMS)
    yield TEAMS
    spark.sql(f"DROP TABLE IF EXISTS {TEAMS.full_name}")


def team_rows(spark, rows):
    """rows: (team_id, abbreviation, snapshot_at, source_file)"""
    from pyspark.sql import functions as F

    df = spark.createDataFrame(
        rows,
        "team_id STRING, abbreviation STRING, _snapshot_at TIMESTAMP, _source_file STRING",
    )
    for field in TEAMS.schema.fields:
        if field.name not in df.columns:
            df = df.withColumn(field.name, F.lit(None).cast(field.dataType))
    return df.select(*TEAMS.columns)


def table_rows(spark, table):
    return {r.team_id: r for r in spark.table(table.full_name).collect()}


T1, T2, T3 = datetime(2026, 10, 1), datetime(2026, 10, 2), datetime(2026, 10, 3)


def test_insert_then_newer_snapshot_wins(spark, teams_table):
    delta_io.upsert_latest(
        spark, team_rows(spark, [("12", "KC", T1, "a")]), teams_table
    )
    delta_io.upsert_latest(
        spark,
        team_rows(spark, [("12", "KCC", T2, "b"), ("7", "DEN", T2, "b")]),
        teams_table,
    )

    rows = table_rows(spark, teams_table)
    assert set(rows) == {"12", "7"}
    assert rows["12"].abbreviation == "KCC"


def test_older_snapshot_is_ignored(spark, teams_table):
    delta_io.upsert_latest(
        spark, team_rows(spark, [("12", "NEW", T3, "c")]), teams_table
    )
    delta_io.upsert_latest(
        spark, team_rows(spark, [("12", "OLD", T1, "a")]), teams_table
    )
    assert table_rows(spark, teams_table)["12"].abbreviation == "NEW"


def test_replaying_same_batch_is_idempotent(spark, teams_table):
    batch = team_rows(spark, [("12", "KC", T1, "a")])
    delta_io.upsert_latest(spark, batch, teams_table)
    delta_io.upsert_latest(spark, batch, teams_table)
    assert spark.table(teams_table.full_name).count() == 1


def test_duplicates_within_batch_keep_newest(spark, teams_table):
    batch = team_rows(
        spark, [("12", "OLD", T1, "a"), ("12", "NEW", T2, "b"), ("12", "TIE", T2, "c")]
    )
    delta_io.upsert_latest(spark, batch, teams_table)
    assert (
        table_rows(spark, teams_table)["12"].abbreviation == "TIE"
    )  # same snapshot: later file wins


def test_null_key_is_rejected(spark, teams_table):
    with pytest.raises(Exception, match="(?i)null"):
        delta_io.upsert_latest(
            spark, team_rows(spark, [(None, "??", T1, "a")]), teams_table
        )


def test_composite_key_upsert_from_fixture(spark, read_fixture):
    from lib.transforms import parse_game_team_stats

    spark.sql(f"DROP TABLE IF EXISTS {STATS.full_name}")
    delta_io.ensure_table(spark, STATS)
    parsed = parse_game_team_stats(read_fixture("game_team_stats"))

    delta_io.upsert_latest(spark, parsed, STATS)
    delta_io.upsert_latest(spark, parsed, STATS)

    assert spark.table(STATS.full_name).count() == 2
    spark.sql(f"DROP TABLE IF EXISTS {STATS.full_name}")


def test_ensure_table_sets_comments(spark, teams_table):
    cols = {
        r.col_name: r.comment
        for r in spark.sql(f"DESCRIBE TABLE {teams_table.full_name}").collect()
    }
    assert cols["color"] == "Primary color, '#rrggbb'"
