# Schema Evolution Pattern for Delta Lake Ingestion

## Problem
When ingesting data into Delta Lake tables, if the source data has a different schema than the target table (missing or extra columns), the merge/write operation fails with:
```
A column, variable, or function parameter with name `_column_name` cannot be resolved.
```

## Solution: Automatic Column Addition (Option 2)

Before merging or writing, add any missing columns from the target table schema to the batch DataFrame with NULL values.

### Pattern

```python
from pyspark.sql import functions as F
from delta.tables import DeltaTable

def upsert_batch(batch_df, batch_id):
    # Step 1: Your transformation logic (dedupe, clean, etc.)
    # ...
    
    # Step 2: ADD MISSING COLUMNS
    target_table = DeltaTable.forName(spark, "TABLE_NAME")
    target_schema = {field.name: field.dataType for field in target_table.toDF().schema}
    batch_columns = {field.name for field in batch_df.schema}
    
    for col_name, col_type in target_schema.items():
        if col_name not in batch_columns:
            batch_df = batch_df.withColumn(col_name, F.lit(None).cast(col_type))
    
    # Step 3: Merge/Write as normal
    (target_table.merge(batch_df.alias("s"), "t.id = s.id")
        .whenMatched...()
        .whenNotMatched...()
        .execute())
```

### Using the Library Helper

For cleaner code, use the helper from `databricks.lib`:

```python
from lib.schema_evolution import auto_add_columns_to_batch

def upsert_batch(batch_df, batch_id):
    # Step 1: Your transformation logic
    # ...
    
    # Step 2: Auto-add missing columns
    batch_df = auto_add_columns_to_batch(batch_df, "TABLE_NAME", spark)
    
    # Step 3: Merge as normal
    DeltaTable.forName(spark, "TABLE_NAME").merge(...).execute()
```

## When to Use

- ✅ **foreachBatch** with merge/upsert (e.g., `ingest_news_data.ipynb`)
- ✅ Streaming writes where schema might evolve
- ✅ When you want explicit control over missing columns

## When NOT to Use

- ❌ Simple `writeStream.format("delta")` writes without merge — use `mergeSchema=True` instead
- ❌ When new columns should raise an error (validate schema strictly)

## Notebooks Using This Pattern

- `ingest_news_data.ipynb` — inline implementation (see cell 5)

## Future Use

For other ingestion notebooks with `foreachBatch` + merge:
1. Add `from lib.schema_evolution import auto_add_columns_to_batch`
2. Call `batch_df = auto_add_columns_to_batch(batch_df, "table_name", spark)` before merge

## Reference

- [Delta Lake Schema Evolution](https://docs.databricks.com/en/delta/table-schema-evolution.html)
- `databricks/lib/schema_evolution.py` — implementation
