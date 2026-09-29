---
name: query-delta
description: Helper for working with Delta Lake tables – common SQL patterns, table discovery, and MCP troubleshooting
---

# Query Delta Lake Tables

Use this skill when you're working with the Delta Lake MCP server (`files/server.py`). It provides shortcuts for common tasks: discovering available tables, writing efficient SQL, debugging queries, and understanding Delta/DuckDB quirks.

## Quick Start

If the MCP server is running locally or configured in Claude:
- Ask "what tables do I have?" → uses `list_tables`
- Ask "describe the players table" → uses `describe_table`
- Ask "total orders by region last month" → builds and runs a read-only query

## Common Patterns

**List all tables:**
```
list_tables → returns name, storage path, partition columns
```

**Inspect a table:**
```
describe_table('my_table') → columns, types, Delta version, file count
```

**Recent changes:**
```
table_history('my_table') → last N Delta commits (metadata)
```

**Safe SQL queries:**
```
query("SELECT COUNT(*) FROM players WHERE team = 'KC'")
-- Read-only: no INSERT, UPDATE, DELETE, DDL, COPY, ATTACH, SET, multiple statements
-- Results capped at DELTA_MAX_ROWS (default 1000)
-- DuckDB SQL dialect; Parquet files scanned with partition/column filters pushed down
```

## Troubleshooting

**"Can't read this table"**: Delta-rs doesn't support all Delta features yet (e.g., deletion vectors written by Databricks). Route through Databricks SQL warehouse instead.

**Slow query**: Unfiltered scans over huge tables are expensive. Use `WHERE` clauses and partition filters; avoid `SELECT *` on wide tables.

**Credentials failing**: Check env vars (AWS_PROFILE, AZURE_STORAGE_ACCOUNT_NAME, GOOGLE_APPLICATION_CREDENTIALS) or `storage_options` in `tables.json`.

**"Table not found"**: Confirm the path in `tables.json` is correct and accessible (S3, abfss://, gs://, or local).

## Reference

- **Config**: `files/tables.json` – list Delta tables as `{ "name": "s3://bucket/path", ... }`
- **Env vars**: `DELTA_TABLES_CONFIG`, `DELTA_MAX_ROWS`
- **Setup**: See `files/README.md` for MCP registration with Claude Desktop or Claude Code
- **DuckDB docs**: [DuckDB SQL reference](https://duckdb.org/docs/sql/introduction.html)
