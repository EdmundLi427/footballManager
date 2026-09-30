"""Integration tests using real Azure credentials (skipped unless AZURE_STORAGE_CONNECTION_STRING is set)."""

import json
import os
from pathlib import Path

import pytest
from data_service.server import Lake, describe_table, list_tables, query


@pytest.mark.integration
class TestServerIntegration:
    """Tests that require real Azure credentials and tables.json."""

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def setup_lake(cls):
        """Load tables.json and set up Lake for integration tests."""
        config_path = Path(__file__).with_name("tables.json")
        if not config_path.exists():
            pytest.skip("tables.json not found; skipping integration tests")
        if not os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
            pytest.skip(
                "AZURE_STORAGE_CONNECTION_STRING not set; skipping integration tests"
            )

        cls.lake = Lake(config_path)

    def test_list_tables_integration(self):
        """Test list_tables against real Azure storage."""
        from data_service import server

        old_lake = server.lake
        server.lake = lambda: self.lake

        try:
            result = json.loads(list_tables())
            assert isinstance(result, list)
            if result:
                assert "table" in result[0]
                assert "path" in result[0]
        finally:
            server.lake = old_lake

    def test_describe_table_integration(self):
        """Test describe_table against real Azure storage."""
        from data_service import server

        old_lake = server.lake
        server.lake = lambda: self.lake

        try:
            if not self.lake.paths:
                pytest.skip("No tables available")
            table_name = next(iter(self.lake.paths.keys()))
            result = json.loads(describe_table(table_name))
            assert result["table"] == table_name
            assert "version" in result
            assert "columns" in result
        finally:
            server.lake = old_lake

    def test_query_integration(self):
        """Test query against real Azure storage."""
        from data_service import server

        old_lake = server.lake
        server.lake = lambda: self.lake

        try:
            if not self.lake.paths:
                pytest.skip("No tables available")
            table_name = next(iter(self.lake.paths.keys()))
            result = json.loads(query(f"SELECT * FROM {table_name} LIMIT 5"))
            assert "columns" in result
            assert "rows" in result
            assert "row_count" in result
        finally:
            server.lake = old_lake
