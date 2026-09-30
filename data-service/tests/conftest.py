"""Pytest configuration for data-service tests."""

import sys
from pathlib import Path

# Add src directory to path so tests can import data_service
src_path = Path(__file__).parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))
