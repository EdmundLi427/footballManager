"""Data service for querying Delta Lake tables via MCP."""

from .server import describe_table, list_tables, query, table_history

__all__ = ["describe_table", "list_tables", "query", "table_history"]
