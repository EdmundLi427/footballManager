"""Test each table individually to find which ones exist and can be accessed."""

import os
from pathlib import Path

import pytest
from data_service.server import Lake


@pytest.mark.integration
def test_each_table_individually():
    """Check each table individually to find which ones exist (requires credentials)."""
    if not os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
        pytest.skip("AZURE_STORAGE_CONNECTION_STRING not set")

    config_path = Path(__file__).with_name("tables.json")
    if not config_path.exists():
        pytest.skip("tables.json not found")

    lake = Lake(config_path)

    working = []
    broken = []

    for name in lake.paths:
        try:
            dt = lake.table(name)
            version = dt.version()
            print(f"✅ {name:20} v{version}")
            working.append(name)
        except (FileNotFoundError, ValueError, OSError) as e:
            error_msg = str(e)[:50]
            print(f"❌ {name:20} {error_msg}")
            broken.append(name)

    print(f"\nWorking tables: {working}")
    print(f"Broken tables: {broken}")

    # At least one table should be accessible, otherwise fail the test
    assert working, "No tables were accessible; check AZURE_STORAGE_CONNECTION_STRING"
