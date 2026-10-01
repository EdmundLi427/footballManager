"""Schema evolution utilities for Delta Lake ingestion.

Handles automatic column addition when source schema differs from target table schema.
"""

from pyspark.sql import DataFrame, functions as F
from delta.tables import DeltaTable


def add_missing_columns(df: DataFrame, target_table_name: str, spark) -> DataFrame:
    """
    Add missing columns to a DataFrame to match target table schema.

    If the target table has columns not present in the DataFrame, they are added
    with NULL values cast to the appropriate type. This prevents merge failures
    when schemas don't match.

    Args:
        df: Source DataFrame
        target_table_name: Full name of target Delta table (e.g., "catalog.schema.table")
        spark: SparkSession

    Returns:
        DataFrame with all target table columns (missing ones filled with NULLs)
    """
    target_table = DeltaTable.forName(spark, target_table_name)
    target_schema = {field.name: field.dataType for field in target_table.toDF().schema}
    df_columns = {field.name for field in df.schema}

    for col_name, col_type in target_schema.items():
        if col_name not in df_columns:
            df = df.withColumn(col_name, F.lit(None).cast(col_type))

    return df


def auto_add_columns_to_batch(batch_df: DataFrame, table_name: str, spark) -> DataFrame:
    """
    Convenience wrapper for add_missing_columns, designed for use in foreachBatch.

    Example:
        def upsert_batch(batch_df, batch_id):
            batch_df = auto_add_columns_to_batch(batch_df, "nfl.injury_news", spark)
            DeltaTable.forName(spark, "nfl.injury_news").merge(...).execute()

    Args:
        batch_df: Batch DataFrame from streaming write
        table_name: Full name of target Delta table
        spark: SparkSession

    Returns:
        DataFrame with missing columns added
    """
    return add_missing_columns(batch_df, table_name, spark)
