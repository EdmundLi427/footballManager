# Delta Lake MCP server

Lets Claude list, inspect, and run read-only SQL against your Delta Lake tables.

## Tools

| Tool | What it does |
|---|---|
| `list_tables` | Table names and storage paths |
| `describe_table` | Columns/types, Delta version, partition columns, file count |
| `table_history` | Recent Delta commits |
| `query` | One read-only SQL statement (DuckDB dialect), results as JSON, row-capped |

Tables are read with delta-rs and registered in an in-memory DuckDB database.
File access, `COPY`, `ATTACH`, `SET`, DDL/DML, and multiple statements are all
blocked, so Claude can only read the tables you list. Queries always see the
latest committed version of each table.

## Setup

1. Copy `tables.example.json` to `tables.json` and list your tables
   (`name -> path`). Paths can be `s3://`, `abfss://`/`az://`, `gs://`, or absolute local paths.

2. Credentials: delta-rs picks up the standard env vars
   (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_PROFILE`,
   `AZURE_STORAGE_ACCOUNT_NAME` + `AZURE_STORAGE_ACCOUNT_KEY` or a service principal,
   `GOOGLE_APPLICATION_CREDENTIALS`). You can also put keys in `storage_options`
   in `tables.json`, but env vars keep secrets out of the file.

3. Test it:
   ```bash
   uv run --with mcp[cli] mcp dev server.py      # opens the MCP Inspector
   ```

## Add to Claude

**Claude Desktop**: edit `claude_desktop_config.json`
(Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "delta-lake": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/delta-lake-mcp", "server.py"],
      "env": {
        "DELTA_TABLES_CONFIG": "/absolute/path/to/delta-lake-mcp/tables.json",
        "AWS_PROFILE": "default"
      }
    }
  }
}
```

**Claude Code**:
```bash
claude mcp add delta-lake -e AWS_PROFILE=default -- \
  uv run --directory /absolute/path/to/delta-lake-mcp server.py
```

Restart Claude, then ask things like "what tables do I have?" or
"total order amount by region for last month".

## Environment variables

- `DELTA_TABLES_CONFIG`: path to the config (default: `tables.json` next to `server.py`)
- `DELTA_MAX_ROWS`: hard cap on rows returned per query (default 1000)

## Limitations

- Tables managed by Databricks Unity Catalog are best queried through a
  Databricks SQL warehouse (`databricks-sql-connector`). Reading their raw storage
  paths needs direct storage credentials and bypasses UC permissions.
- If a table uses Delta features delta-rs can't read yet (for example some
  deletion-vector tables written by Databricks), `describe_table`/`query` will
  error on it. Route those tables through Databricks instead.
- Every query scans the underlying Parquet files. Partition filters and column
  pruning are pushed down, but very large unfiltered scans will be slow.
