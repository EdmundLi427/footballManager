"""
Smoke tests for the Flask app.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import app


def test_app_creates():
    """Test that the Flask app initializes without errors."""
    assert app is not None
    assert app.config is not None


def test_index_route_exists():
    """Test that the index route is registered."""
    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200
