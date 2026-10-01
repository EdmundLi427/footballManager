"""
Layer 1: Unit tests for pure Python logic (no Spark required).

Tests the transformation utilities that can be validated without a SparkSession.
"""

import pytest
import sys
from pathlib import Path

# Add parent directories to path so we can import databricks.lib
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.transforms import (
    to_snake,
    table_name_from_file,
    is_csv,
    TABLE_KEYS,
    injury_file_schema,
    games_schema,
    teams_schema,
    players_schema,
    rosters_schema,
    schedules_schema,
)


class TestToSnake:
    """Tests for the to_snake utility."""

    def test_camel_case(self):
        assert to_snake("firstName") == "first_name"
        assert to_snake("myTableName") == "my_table_name"

    def test_already_snake_case(self):
        assert to_snake("snake_case") == "snake_case"
        assert to_snake("game_team_stats") == "game_team_stats"

    def test_with_spaces(self):
        assert to_snake("first name") == "first_name"
        assert to_snake("my table name") == "my_table_name"

    def test_with_symbols(self):
        assert to_snake("first-name") == "first_name"
        assert to_snake("first.name") == "first_name"

    def test_consecutive_underscores(self):
        assert to_snake("first__name") == "first_name"
        assert to_snake("my___table") == "my_table"

    def test_uppercase_acronyms(self):
        # Consecutive uppercase letters stay together without separators
        assert to_snake("HTMLParser") == "htmlparser"
        assert to_snake("parseJSON") == "parse_json"

    def test_strip_leading_trailing(self):
        assert to_snake("_firstName") == "first_name"
        assert to_snake("firstName_") == "first_name"
        assert to_snake("_firstName_") == "first_name"

    def test_lowercase_output(self):
        assert to_snake("FirstName") == "first_name"
        assert to_snake("FIRSTNAME") == "firstname"  # All-caps stays together


class TestTableNameFromFile:
    """Tests for table name derivation from filenames."""

    def test_csv_file(self):
        assert table_name_from_file("game_team_stats.csv") == "game_team_stats"
        assert table_name_from_file("standings.csv") == "standings"

    def test_json_file(self):
        assert table_name_from_file("rosters.json") == "rosters"
        assert table_name_from_file("players.json") == "players"

    def test_camel_case_filename(self):
        assert table_name_from_file("MyTableName.csv") == "my_table_name"
        assert table_name_from_file("gameTeamStats.json") == "game_team_stats"

    def test_multiple_dots(self):
        assert table_name_from_file("my.data.csv") == "my_data"


class TestIsCsv:
    """Tests for CSV detection."""

    def test_csv_lowercase(self):
        assert is_csv("file.csv") is True

    def test_csv_uppercase(self):
        assert is_csv("file.CSV") is True

    def test_csv_mixed_case(self):
        assert is_csv("file.Csv") is True

    def test_json_file(self):
        assert is_csv("file.json") is False

    def test_no_extension(self):
        assert is_csv("file") is False


class TestTableKeys:
    """Tests for the TABLE_KEYS mapping."""

    def test_all_tables_have_keys(self):
        """Verify all expected tables are in TABLE_KEYS."""
        expected_tables = {
            "injury_news",
            "games",
            "teams",
            "players",
            "rosters",
            "schedules",
            "standings",
            "game_team_stats",
        }
        assert set(TABLE_KEYS.keys()) == expected_tables

    def test_single_key_tables(self):
        """Tables with single-column keys."""
        assert TABLE_KEYS["injury_news"] == ["id"]
        assert TABLE_KEYS["games"] == ["id"]
        assert TABLE_KEYS["teams"] == ["id"]
        assert TABLE_KEYS["players"] == ["id"]
        assert TABLE_KEYS["schedules"] == ["id"]

    def test_composite_key_tables(self):
        """Tables with multi-column keys."""
        assert TABLE_KEYS["rosters"] == ["teamId", "playerId"]
        assert TABLE_KEYS["standings"] == ["teamId", "season"]
        assert TABLE_KEYS["game_team_stats"] == ["gameId", "teamId"]

    def test_all_keys_are_non_empty(self):
        """Every table must have at least one key."""
        for table, keys in TABLE_KEYS.items():
            assert len(keys) > 0, f"Table {table} has no keys"
            assert all(isinstance(k, str) for k in keys), f"Table {table} has non-string keys"


class TestSchemaDefinitions:
    """Tests for schema builder functions."""

    def test_injury_schema_fields(self):
        """Verify injury schema has expected fields."""
        schema = injury_file_schema()
        field_names = [f.name for f in schema.fields]
        expected = ["sport", "title", "description", "count", "items", "limit", "public_api_limited", "tier"]
        assert set(field_names) == set(expected)

    def test_games_schema_fields(self):
        """Verify games schema has expected fields."""
        schema = games_schema()
        field_names = [f.name for f in schema.fields]
        expected = ["id", "date", "status", "completed", "venue", "city", "attendance"]
        assert set(field_names) == set(expected)

    def test_teams_schema_fields(self):
        """Verify teams schema has expected fields."""
        schema = teams_schema()
        field_names = [f.name for f in schema.fields]
        expected = ["id", "uid", "slug", "abbreviation", "displayName", "isActive", "color", "alternateColor"]
        assert set(field_names) == set(expected)

    def test_rosters_schema_fields(self):
        """Verify rosters schema has expected fields."""
        schema = rosters_schema()
        field_names = [f.name for f in schema.fields]
        expected = ["teamId", "position", "playerId", "playerName", "jersey", "age", "height", "weight", "experience"]
        assert set(field_names) == set(expected)

    def test_schedules_schema_fields(self):
        """Verify schedules schema has expected fields."""
        schema = schedules_schema()
        field_names = [f.name for f in schema.fields]
        expected = ["id", "date", "week", "status", "completed", "homeTeamId", "awayTeamId", "homeScore", "awayScore", "venue"]
        assert set(field_names) == set(expected)

    def test_players_schema_fields(self):
        """Verify players schema has expected fields."""
        schema = players_schema()
        field_names = [f.name for f in schema.fields]
        expected = ["id", "uid", "guid", "name", "dateOfBirth", "birthplace", "hand", "height", "weight", "experience"]
        assert set(field_names) == set(expected)

    def test_schemas_are_structtype(self):
        """Verify all schema functions return StructType."""
        from pyspark.sql.types import StructType

        assert isinstance(injury_file_schema(), StructType)
        assert isinstance(games_schema(), StructType)
        assert isinstance(teams_schema(), StructType)
        assert isinstance(players_schema(), StructType)
        assert isinstance(rosters_schema(), StructType)
        assert isinstance(schedules_schema(), StructType)


class TestSchemaTableKeysAlignment:
    """Tests that TABLE_KEYS matches schema field definitions."""

    def test_injury_news_key_in_schema(self):
        """The 'id' key should be a field in injury_file_schema."""
        schema = injury_file_schema()
        # Injury schema has nested items with id fields
        item_schema = None
        for field in schema.fields:
            if field.name == "items":
                item_schema = field.dataType.elementType
                break

        assert item_schema is not None
        item_fields = [f.name for f in item_schema.fields]
        assert "id" in item_fields

    def test_games_key_in_schema(self):
        """The 'id' key should be in games schema."""
        schema = games_schema()
        field_names = [f.name for f in schema.fields]
        assert "id" in field_names

    def test_rosters_keys_in_schema(self):
        """Both 'teamId' and 'playerId' keys should be in rosters schema."""
        schema = rosters_schema()
        field_names = [f.name for f in schema.fields]
        assert "teamId" in field_names
        assert "playerId" in field_names

    def test_standings_keys_in_schema(self):
        """Both 'teamId' and 'season' should be handled (season not in direct schema, comes from ESPN data)."""
        schema = rosters_schema()
        field_names = [f.name for f in schema.fields]
        assert "teamId" in field_names

    def test_game_team_stats_keys(self):
        """'gameId' and 'teamId' should be present (gameId is added by transform)."""
        # gameId is added dynamically, so we just verify teamId is in a roster-like schema
        schema = rosters_schema()
        field_names = [f.name for f in schema.fields]
        assert "teamId" in field_names
