"""Delta table creation and idempotent upserts, driven by ``schemas.TABLES``."""

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from .schemas import Table


def _sql_literal(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def create_table_ddl(table: Table, location: str | None = None) -> str:
    """``CREATE TABLE IF NOT EXISTS`` with comments and NOT NULL on non-nullable (key) columns."""
    cols = []
    for field in table.schema.fields:
        line = f"  {field.name} {field.dataType.simpleString().upper()}"
        if not field.nullable:
            line += " NOT NULL"
        comment = field.metadata.get("comment")
        if comment:
            line += f" COMMENT {_sql_literal(comment)}"
        cols.append(line)

    ddl = (
        f"CREATE TABLE IF NOT EXISTS {table.full_name} (\n"
        + ",\n".join(cols)
        + "\n)\nUSING DELTA"
    )
    if table.description:
        ddl += f"\nCOMMENT {_sql_literal(table.description)}"
    if location:
        ddl += f"\nLOCATION '{location}'"
    return ddl


def ensure_table(spark, table: Table, location: str | None = None) -> None:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {table.full_name.split('.')[0]}")
    spark.sql(create_table_ddl(table, location))


def dedupe_latest(df: DataFrame, table: Table) -> DataFrame:
    """Keeps one row per key: newest snapshot, ties broken by source file for determinism."""
    window = Window.partitionBy(*table.keys).orderBy(
        F.col(table.snapshot_col).desc(), F.col(_source_col(table)).desc()
    )
    return (
        df.withColumn("_rn", F.row_number().over(window)).filter("_rn = 1").drop("_rn")
    )


def _source_col(table: Table) -> str:
    return "_source_file" if table.snapshot_col == "_snapshot_at" else "source_file"


def upsert_latest(spark, df: DataFrame, table: Table) -> None:
    """Inserts new keys; updates existing keys only when the incoming snapshot is newer."""
    from delta.tables import DeltaTable

    on = " AND ".join(f"t.{k} = s.{k}" for k in table.keys)
    snap = table.snapshot_col
    (
        DeltaTable.forName(spark, table.full_name)
        .alias("t")
        .merge(dedupe_latest(df, table).alias("s"), on)
        .whenMatchedUpdateAll(condition=f"s.{snap} > t.{snap}")
        .whenNotMatchedInsertAll()
        .execute()
    )


def upsert_batch_fn(spark, table: Table):
    """``foreachBatch`` callable for ``writeStream``."""

    def _upsert(batch_df: DataFrame, batch_id: int) -> None:
        upsert_latest(spark, batch_df, table)

    return _upsert
