#!/usr/bin/env python3
"""Check which tables use unsupported Delta features."""

import json
import os
from pathlib import Path
from dotenv import load_dotenv
from deltalake import DeltaTable

load_dotenv()

conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
storage_options = {}
if conn_str:
    parts = {p.split("=", 1)[0]: p.split("=", 1)[1] for p in conn_str.split(";")}
    if "AccountName" in parts:
        storage_options["account_name"] = parts["AccountName"]
    if "AccountKey" in parts:
        storage_options["account_key"] = parts["AccountKey"]

config_path = Path(__file__).with_name("tables.json")
tables = json.loads(config_path.read_text()).get("tables", {})

print("Checking for unsupported features...")
print("=" * 60)

for name, path in tables.items():
    try:
        dt = DeltaTable(path, storage_options=storage_options)
        dt.to_pyarrow_dataset()  # This will fail if unsupported features exist
        print(f"✅ {name:20} OK")
    except Exception as e:
        error = str(e)
        print(f"❌ {name:20} {error[:50]}")

print("=" * 60)
print("Tables with deletion vectors should be queried via Databricks SQL instead.")
