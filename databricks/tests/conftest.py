"""
Pytest configuration for databricks tests.

Provides:
  - SparkSession fixture for integration tests (marked with @pytest.mark.spark)
  - Shared test data fixtures
"""

import pytest
from pathlib import Path


@pytest.fixture(scope="session")
def spark():
    """
    Create a local SparkSession for testing.

    Marked with spark-only dependencies and optimized for local execution.
    If PySpark/Delta are not installed, tests marked @pytest.mark.spark will skip.
    """
    spark_import_error = None
    try:
        from pyspark.sql import SparkSession
        from delta import configure_spark_with_delta_pip
    except ImportError as e:
        spark_import_error = e

    if spark_import_error:
        pytest.skip(f"PySpark/Delta not installed: {spark_import_error}")

    try:
        spark = configure_spark_with_delta_pip(
            SparkSession.builder
            .appName("databricks-tests")
            .master("local[1]")
            .config("spark.sql.shuffle.partitions", "1")
            .config("spark.default.parallelism", "1")
            .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
            .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        ).getOrCreate()
    except Exception as e:
        if "Java" in str(e) or "JAVA_HOME" in str(e):
            pytest.skip(f"Java Runtime not available: {e}")
        raise

    yield spark

    spark.stop()


@pytest.fixture
def fixture_dir():
    """Return the path to the fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def injury_fixture(fixture_dir):
    """Load the sample injury news fixture."""
    import json
    fixture_file = fixture_dir / "injury_news.json"
    if fixture_file.exists():
        with open(fixture_file) as f:
            return json.load(f)
    return None


@pytest.fixture
def games_fixture(fixture_dir):
    """Load the sample games fixture."""
    import json
    fixture_file = fixture_dir / "games.json"
    if fixture_file.exists():
        with open(fixture_file) as f:
            return json.load(f)
    return None


@pytest.fixture
def rosters_fixture(fixture_dir):
    """Load the sample rosters fixture."""
    import json
    fixture_file = fixture_dir / "rosters.json"
    if fixture_file.exists():
        with open(fixture_file) as f:
            return json.load(f)
    return None


@pytest.fixture
def standings_fixture(fixture_dir):
    """Load the sample standings fixture."""
    import json
    fixture_file = fixture_dir / "standings.json"
    if fixture_file.exists():
        with open(fixture_file) as f:
            return json.load(f)
    return None


@pytest.fixture
def game_team_stats_fixture(fixture_dir):
    """Load the sample game-team-stats fixture."""
    import json
    fixture_file = fixture_dir / "game_team_stats.json"
    if fixture_file.exists():
        with open(fixture_file) as f:
            return json.load(f)
    return None
