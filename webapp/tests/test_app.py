"""
Smoke tests for the Flask app.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import app
from chatbot import SYSTEM_PROMPT, Chatbot


def test_app_creates():
    """Test that the Flask app initializes without errors."""
    assert app is not None
    assert app.config is not None


def test_index_route_exists():
    """Test that the index route is registered."""
    with app.test_client() as client:
        response = client.get("/")
        assert response.status_code == 200


def test_system_prompt_exists():
    """Test that SYSTEM_PROMPT is defined."""
    assert SYSTEM_PROMPT is not None
    assert len(SYSTEM_PROMPT) > 0
    assert "injury_news" in SYSTEM_PROMPT.lower()
    assert "freshness" in SYSTEM_PROMPT.lower()


def test_system_prompt_passed_to_api():
    """Test that the system prompt is passed to the Anthropic API."""
    with patch("anthropic.Anthropic") as mock_anthropic_class:
        mock_client = MagicMock()
        mock_anthropic_class.return_value = mock_client

        # Mock the API response
        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [MagicMock(text="Test response")]
        mock_client.messages.create.return_value = mock_response

        # Create chatbot and send a message
        bot = Chatbot()
        bot.chat("Test message")

        # Verify that messages.create was called with the system prompt
        mock_client.messages.create.assert_called()
        call_kwargs = mock_client.messages.create.call_args[1]
        assert "system" in call_kwargs
        assert call_kwargs["system"] == SYSTEM_PROMPT
