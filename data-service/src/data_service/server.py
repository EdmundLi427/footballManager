"""
Delta Lake MCP server.

Gives Claude read-only SQL access to Delta tables on S3, Azure (ADLS), GCS,
or local disk. Each table is read with delta-rs (the `deltalake` package) and
registered in an in-memory DuckDB database, so Claude can join and aggregate
across tables with plain SQL. DuckDB's file access is disabled, so queries
can only touch the tables you list in tables.json.

Tools:
  list_tables     - table names and storage paths
  describe_table  - columns/types plus Delta metadata (version, partitions, files)
  table_history   - recent Delta commits
  query           - one read-only SQL statement, results as JSON
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

import duckdb
from deltalake import DeltaTable

CONFIG_PATH = Path(
    os.environ.get("DELTA_TABLES_CONFIG", Path(__file__).with_name("tables.json"))
)
MAX_ROWS_CAP = int(os.environ.get("DELTA_MAX_ROWS", "1000"))
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
READ_ONLY_TYPES = {"SELECT", "EXPLAIN"}  # DESCRIBE/SUMMARIZE/SHOW parse as SELECT

# MCP setup (only needed when running as server)
try:
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as _Server
    except ImportError:
        _Server = None

mcp = _Server("delta-lake") if _Server else None


class Lake:
    """Holds the DeltaTable handles and the locked-down DuckDB connection."""

    def __init__(self, config_path: Path):
        cfg = json.loads(config_path.read_text())
        self.paths: dict[str, str] = cfg.get("tables") or {}
        if not self.paths:
            raise ValueError(f"No tables defined in {config_path}")
        for name in self.paths:
            if not NAME_RE.match(name):
                raise ValueError(
                    f"Invalid table name {name!r}: use letters, digits, underscores"
                )
        # Passed to delta-rs; leave empty to use env vars / default credential chains.
        self.storage_options: dict[str, str] = cfg.get("storage_options", {})

        # Parse Azure connection string from env if present
        conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
        if conn_str:
            parts = {
                p.split("=", 1)[0]: p.split("=", 1)[1] for p in conn_str.split(";")
            }
            if "AccountName" in parts:
                self.storage_options["account_name"] = parts["AccountName"]
            if "AccountKey" in parts:
                self.storage_options["account_key"] = parts["AccountKey"]

        self.lock = threading.Lock()
        self.tables: dict[str, DeltaTable] = {}
        self.con = duckdb.connect(":memory:")
        for name, path in self.paths.items():
            dt = DeltaTable(path, storage_options=self.storage_options)
            self.tables[name] = dt
            self.con.register(name, dt.to_pyarrow_dataset())
        # From here on, SQL cannot read/write files, attach DBs, or change settings.
        self.con.execute("SET enable_external_access = false")
        self.con.execute("SET lock_configuration = true")

    def table(self, name: str) -> DeltaTable:
        if name not in self.tables:
            raise ValueError(
                f"Unknown table {name!r}. Available: {', '.join(self.tables)}"
            )
        return self.tables[name]

    def refresh(self) -> None:
        """Pick up new commits so queries always see the latest table version."""
        for name, dt in self.tables.items():
            before = dt.version()
            dt.update_incremental()
            if dt.version() != before:
                self.con.unregister(name)
                self.con.register(name, dt.to_pyarrow_dataset())


_lake: Lake | None = None


def lake() -> Lake:
    global _lake
    if _lake is None:
        _lake = Lake(CONFIG_PATH)
    return _lake


def _to_json(obj: Any) -> str:
    return json.dumps(obj, default=str, indent=2)


def _check_read_only(sql: str) -> None:
    stmts = duckdb.extract_statements(sql)
    if len(stmts) != 1:
        raise ValueError("Send exactly one SQL statement.")
    if stmts[0].type.name not in READ_ONLY_TYPES:
        raise ValueError(
            f"Only read-only queries are allowed (got {stmts[0].type.name})."
        )


def _mcp_tool(func):
    """Decorator that applies @mcp.tool() only if mcp is available."""
    if mcp:
        return mcp.tool()(func)
    return func


@_mcp_tool
def list_tables() -> str:
    """List the Delta tables available to query, with their storage paths.
    Use these names directly in SQL, e.g. SELECT * FROM orders."""
    return _to_json([{"table": n, "path": p} for n, p in lake().paths.items()])


@_mcp_tool
def describe_table(table: str) -> str:
    """Show a table's columns and types plus Delta metadata: current version,
    partition columns, number of data files, and description.
    Call this before querying an unfamiliar table."""
    lk = lake()
    dt = lk.table(table)
    with lk.lock:
        lk.refresh()
        cols = lk.con.execute(f"DESCRIBE {table}").fetchall()
    meta = dt.metadata()
    return _to_json(
        {
            "table": table,
            "path": lk.paths[table],
            "version": dt.version(),
            "description": meta.description,
            "partition_columns": meta.partition_columns,
            "num_files": len(dt.file_uris()),
            "columns": [
                {"name": c[0], "type": c[1], "nullable": c[2] == "YES"} for c in cols
            ],
        }
    )


@_mcp_tool
def table_history(table: str, limit: int = 10) -> str:
    """Show recent commits to a Delta table (timestamp, operation, parameters),
    newest first. Useful for 'when was this last updated?' questions."""
    dt = lake().table(table)
    return _to_json(dt.history(limit=max(1, min(limit, 100))))


@_mcp_tool
def query(sql: str, max_rows: int = 200) -> str:
    """Run one read-only SQL query (DuckDB dialect) against the Delta tables
    and return the results as JSON.

    - Reference tables by the names from list_tables.
    - Supports CTEs, joins, window functions, QUALIFY, DESCRIBE, SUMMARIZE.
    - Prefer aggregates and explicit columns over SELECT * on big tables.
    - Results are capped at max_rows; 'truncated' says whether more rows exist.
    """
    _check_read_only(sql)
    max_rows = max(1, min(max_rows, MAX_ROWS_CAP))
    lk = lake()
    with lk.lock:
        lk.refresh()
        rel = lk.con.sql(sql)
        columns = rel.columns
        rows = rel.limit(max_rows + 1).fetchall()
    truncated = len(rows) > max_rows
    return _to_json(
        {
            "columns": columns,
            "rows": [list(r) for r in rows[:max_rows]],
            "row_count": min(len(rows), max_rows),
            "truncated": truncated,
        }
    )


if __name__ == "__main__":
    mcp.run()
