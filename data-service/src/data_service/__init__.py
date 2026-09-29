"""Data service for querying Delta Lake tables via MCP."""

from .server import list_tables, describe_table, table_history, query

__all__ = ["list_tables", "describe_table", "table_history", "query"]
