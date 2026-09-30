"""Test script for Delta Lake MCP server without needing mcp dev."""

import json
import sys

from dotenv import load_dotenv
from server import describe_table, list_tables, query, table_history

load_dotenv()


def test_server():
    print("=" * 60)
    print("Testing Delta Lake MCP Server")
    print("=" * 60)

    try:
        print("\n1. Listing tables...")
        result = list_tables()
        tables = json.loads(result)
        print(f"   Found {len(tables)} table(s):")
        for t in tables:
            print(f"   - {t['table']}: {t['path']}")

        if not tables:
            print("   ⚠️  No tables defined. Create tables.json with sample data.")
            return False

        table_name = tables[0]["table"]
        print(f"\n2. Describing table '{table_name}'...")
        result = describe_table(table_name)
        desc = json.loads(result)
        print(f"   Version: {desc['version']}")
        print(f"   Columns: {len(desc['columns'])}")
        print(f"   Partitions: {desc['partition_columns']}")
        print(f"   Files: {desc['num_files']}")

        print(f"\n3. Querying table '{table_name}'...")
        result = query(f"SELECT * FROM {table_name} LIMIT 5")
        qry = json.loads(result)
        print(f"   Columns: {qry['columns']}")
        print(f"   Rows returned: {qry['row_count']}")
        print(f"   Truncated: {qry['truncated']}")

        if qry["rows"]:
            print(f"   First row: {qry['rows'][0]}")

        print(f"\n4. Table history for '{table_name}'...")
        result = table_history(table_name, limit=3)
        hist = json.loads(result)
        print(f"   Latest {len(hist)} commit(s):")
        for i, commit in enumerate(hist[:3], 1):
            print(
                f"   {i}. {commit.get('operation', 'unknown')} @ v{commit.get('version', '?')}"
            )

        print("\n" + "=" * 60)
        print("✅ Server is working!")
        print("=" * 60)
        return True

    except FileNotFoundError as e:
        print(f"\n❌ Error: {e}")
        print("   Make sure you've created tables.json (copy from tables.example.json)")
        return False
    except (ValueError, OSError, KeyError) as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_server()
    sys.exit(0 if success else 1)
