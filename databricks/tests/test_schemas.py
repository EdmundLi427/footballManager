"""Static checks on the table definitions (no Spark session needed)."""

import re

import pytest

pytest.importorskip("pyspark")

from lib.delta_io import create_table_ddl
from lib.schemas import AUDIT_COLUMNS, ESPN_TABLES, TABLES
from lib.transforms import PARSERS

SNAKE = re.compile(r"^_?[a-z][a-z0-9]*(_[a-z0-9]+)*$")


@pytest.mark.parametrize("name", TABLES)
def test_keys_exist_and_are_not_nullable(name):
    table = TABLES[name]
    fields = {f.name: f for f in table.schema.fields}
    assert table.keys
    for key in table.keys:
        assert key in fields, f"{name}: key {key} missing from schema"
        assert not fields[key].nullable, f"{name}: key {key} must be NOT NULL"


@pytest.mark.parametrize("name", TABLES)
def test_columns_are_snake_case_and_unique(name):
    cols = TABLES[name].columns
    assert len(cols) == len(set(cols))
    bad = [c for c in cols if not SNAKE.match(c)]
    assert not bad, f"{name}: non-snake_case columns {bad}"


@pytest.mark.parametrize("name", ESPN_TABLES)
def test_espn_tables_end_with_audit_columns(name):
    cols = TABLES[name].columns
    assert cols[-3:] == [f.name for f in AUDIT_COLUMNS]
    assert TABLES[name].snapshot_col == "_snapshot_at"
    assert TABLES[name].source_prefix.startswith("espn/")


def test_every_table_has_a_parser():
    assert set(PARSERS) == set(TABLES)


def test_ddl_has_not_null_keys_comments_and_location():
    ddl = create_table_ddl(
        TABLES["game_team_stats"], "abfss://x/silver/game_team_stats"
    )
    assert ddl.startswith("CREATE TABLE IF NOT EXISTS nfl.game_team_stats (")
    assert "  game_id STRING NOT NULL" in ddl
    assert "  team_id STRING NOT NULL" in ddl
    assert "  yards_per_play DOUBLE," in ddl
    assert "COMMENT 'Defensive / special teams TDs'" in ddl
    assert ddl.rstrip().endswith("LOCATION 'abfss://x/silver/game_team_stats'")


def test_ddl_without_location_is_managed_table():
    ddl = create_table_ddl(TABLES["schedules"])
    assert "COMMENT '1=pre, 2=regular, 3=post; week numbers repeat across types'" in ddl
    assert "LOCATION" not in ddl
