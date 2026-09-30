#!/usr/bin/env python3
"""Test each table individually to find which ones exist."""

import json
import os
from pathlib import Path

from deltalake import DeltaTable
from dotenv import load_dotenv

load_dotenv()

# Parse connection string
conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
storage_options = {}
if conn_str:
    parts = {p.split("=", 1)[0]: p.split("=", 1)[1] for p in conn_str.split(";")}
    if "AccountName" in parts:
        storage_options["account_name"] = parts["AccountName"]
    if "AccountKey" in parts:
        storage_options["account_key"] = parts["AccountKey"]

# Load tables from tables.json
config_path = Path(__file__).with_name("tables.json")
tables = json.loads(config_path.read_text()).get("tables", {})

print("Testing each table:")
print("=" * 60)

working = []
broken = []

for name, path in tables.items():
    try:
        dt = DeltaTable(path, storage_options=storage_options)
        print(f"✅ {name:20} v{dt.version()}")
        working.append(name)
    except (FileNotFoundError, ValueError, OSError) as e:
        print(f"❌ {name:20} {str(e)[:50]}")
        broken.append(name)

print("=" * 60)
print(f"\nWorking tables: {working}")
print(f"Broken tables: {broken}")
