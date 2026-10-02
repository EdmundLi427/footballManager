"""Shared fixtures. Spark tests need PySpark + delta-spark + Java 17; they skip if any is missing."""

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# PySpark converts collected timestamps to the Python process's local zone; pin it so
# golden UTC values compare the same on every machine.
os.environ["TZ"] = "UTC"
time.tzset()


@pytest.fixture(scope="session")
def spark():
    try:
        from delta import configure_spark_with_delta_pip
        from pyspark.sql import SparkSession
    except ImportError as e:
        pytest.skip(f"PySpark/Delta not installed: {e}")

    warehouse = tempfile.mkdtemp(prefix="spark-warehouse-")
    try:
        builder = (
            SparkSession.builder.appName("databricks-tests")
            .master("local[1]")
            .config("spark.sql.shuffle.partitions", "1")
            .config("spark.sql.session.timeZone", "UTC")
            .config("spark.sql.warehouse.dir", warehouse)
            .config("spark.ui.enabled", "false")
            .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
            .config(
                "spark.sql.catalog.spark_catalog",
                "org.apache.spark.sql.delta.catalog.DeltaCatalog",
            )
        )
        session = configure_spark_with_delta_pip(builder).getOrCreate()
    except Exception as e:
        if "Java" in str(e) or "JAVA_HOME" in str(e):
            pytest.skip(f"Java runtime not available: {e}")
        raise

    yield session
    session.stop()
    shutil.rmtree(warehouse, ignore_errors=True)


@pytest.fixture(scope="session")
def read_fixture(spark):
    """Reads a landing fixture exactly as the notebooks read blobs: explicit raw schema + file metadata."""
    from lib import transforms
    from lib.schemas import TABLES

    def _read(table_name: str, fixture: str | None = None):
        table = TABLES[table_name]
        if fixture is None:
            fixture = (
                "fantasypros/injury_news"
                if table_name == "injury_news"
                else f"espn/{table.source_prefix.split('/')[-1]}"
            )
        path = str(FIXTURES / f"{fixture}.json")
        raw = spark.read.schema(table.raw_schema).option("multiLine", "true").json(path)
        return transforms.with_file_metadata(raw)

    return _read
