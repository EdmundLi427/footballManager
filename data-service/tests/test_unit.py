"""Unit tests for Delta Lake MCP server using in-memory test tables."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import pytest
from data_service.server import Lake, describe_table, list_tables, query, table_history
from deltalake import write_deltalake


@pytest.fixture
def test_lake(tmp_path):
    """Create a Lake instance with in-memory test tables."""
    # Write a simple test table
    test_data = pa.table(
        {
            "id": [1, 2, 3],
            "name": ["Alice", "Bob", "Charlie"],
            "age": [30, 25, 35],
        }
    )
    write_deltalake(str(tmp_path / "users"), test_data, mode="overwrite")

    # Write a second test table for joins
    test_data2 = pa.table(
        {
            "user_id": [1, 2, 3],
            "city": ["NYC", "LA", "SF"],
        }
    )
    write_deltalake(str(tmp_path / "locations"), test_data2, mode="overwrite")

    # Create config pointing to test tables
    config = {
        "tables": {
            "users": str(tmp_path / "users"),
            "locations": str(tmp_path / "locations"),
        },
        "storage_options": {},
    }
    config_path = tmp_path / "tables.json"
    config_path.write_text(json.dumps(config))

    return Lake(config_path)


def test_list_tables(test_lake):
    """Test list_tables returns available tables."""
    # Patch the global lake() to return our test lake
    from data_service import server

    old_lake = server.lake
    server.lake = lambda: test_lake

    try:
        result = json.loads(list_tables())
        assert len(result) == 2
        tables = {t["table"]: t["path"] for t in result}
        assert "users" in tables
        assert "locations" in tables
    finally:
        server.lake = old_lake


def test_describe_table(test_lake):
    """Test describe_table returns schema and metadata."""
    from data_service import server

    old_lake = server.lake
    server.lake = lambda: test_lake

    try:
        result = json.loads(describe_table("users"))
        assert result["table"] == "users"
        assert result["version"] >= 0
        assert len(result["columns"]) == 3
        col_names = {c["name"] for c in result["columns"]}
        assert col_names == {"id", "name", "age"}
    finally:
        server.lake = old_lake


def test_query_select(test_lake):
    """Test query executes SELECT and returns results."""
    from data_service import server

    old_lake = server.lake
    server.lake = lambda: test_lake

    try:
        result = json.loads(query("SELECT * FROM users WHERE id > 1"))
        assert result["row_count"] == 2
        assert result["truncated"] is False
        assert len(result["rows"]) == 2
    finally:
        server.lake = old_lake


def test_query_count(test_lake):
    """Test query with aggregation."""
    from data_service import server

    old_lake = server.lake
    server.lake = lambda: test_lake

    try:
        result = json.loads(query("SELECT COUNT(*) as cnt FROM users"))
        assert len(result["rows"]) == 1
        assert result["rows"][0][0] == 3
    finally:
        server.lake = old_lake


def test_query_join(test_lake):
    """Test query across multiple tables."""
    from data_service import server

    old_lake = server.lake
    server.lake = lambda: test_lake

    try:
        sql = "SELECT u.name, l.city FROM users u JOIN locations l ON u.id = l.user_id"
        result = json.loads(query(sql))
        assert result["row_count"] == 3
    finally:
        server.lake = old_lake


def test_query_read_only_reject_insert(test_lake):
    """Test that INSERT is rejected."""
    from data_service import server

    old_lake = server.lake
    server.lake = lambda: test_lake

    try:
        with pytest.raises(ValueError, match="Only read-only"):
            query("INSERT INTO users VALUES (4, 'Dave', 40)")
    finally:
        server.lake = old_lake


def test_query_read_only_reject_delete(test_lake):
    """Test that DELETE is rejected."""
    from data_service import server

    old_lake = server.lake
    server.lake = lambda: test_lake

    try:
        with pytest.raises(ValueError, match="Only read-only"):
            query("DELETE FROM users WHERE id = 1")
    finally:
        server.lake = old_lake


def test_query_read_only_reject_drop(test_lake):
    """Test that DROP is rejected."""
    from data_service import server

    old_lake = server.lake
    server.lake = lambda: test_lake

    try:
        with pytest.raises(ValueError, match="Only read-only"):
            query("DROP TABLE users")
    finally:
        server.lake = old_lake


def test_query_multiple_statements_rejected(test_lake):
    """Test that multiple statements are rejected."""
    from data_service import server

    old_lake = server.lake
    server.lake = lambda: test_lake

    try:
        with pytest.raises(ValueError, match="exactly one SQL statement"):
            query("SELECT * FROM users; SELECT * FROM locations")
    finally:
        server.lake = old_lake


def test_table_history(test_lake):
    """Test table_history returns version info."""
    from data_service import server

    old_lake = server.lake
    server.lake = lambda: test_lake

    try:
        result = json.loads(table_history("users", limit=5))
        assert isinstance(result, list)
        assert len(result) > 0
        # First commit should be an ADD (table creation)
        assert result[0]["operation"] in ("WRITE", "ADD")
    finally:
        server.lake = old_lake


def test_lake_unknown_table(test_lake):
    """Test that querying unknown table raises ValueError."""
    with pytest.raises(ValueError, match="Unknown table"):
        test_lake.table("nonexistent")


def test_lake_invalid_table_name():
    """Test that invalid table names are rejected."""
    config = {
        "tables": {
            "valid_name": "/path/to/table",
            "123_invalid": "/path/to/table",
        },
        "storage_options": {},
    }
    with TemporaryDirectory() as tmp_dir:
        config_path = Path(tmp_dir) / "tables.json"
        config_path.write_text(json.dumps(config))
        with pytest.raises(ValueError, match="Invalid table name"):
            Lake(config_path)
