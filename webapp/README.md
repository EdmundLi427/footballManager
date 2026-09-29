# Webapp

A Flask web application providing a chat UI for querying NFL data via the Delta Lake data service.

## Overview

The webapp exposes a web interface where users can ask questions about NFL data (injuries, schedules, rosters, etc.). Queries are processed by the `Chatbot` class, which uses the Claude API with tool calling to access Delta Lake tables through the `data-service` module.

## Setup

```bash
# Install dependencies (run from repo root)
uv sync

# Create .env from the example
cp .env.example .env

# Set your Anthropic API key
export ANTHROPIC_API_KEY="your-key-here"
```

## Development

```bash
# Run the app locally
uv run --directory webapp python app.py

# Open http://localhost:5000 in your browser

# Run tests
uv run --directory webapp pytest
```

## Configuration

Environment variables (set in `.env` or at runtime):

- `ANTHROPIC_API_KEY` — your Anthropic API key (required)
- `FLASK_DEBUG` — enable/disable debug mode (default: `True`)
- `FLASK_PORT` — port to run on (default: `5000`)

## Architecture

The webapp depends on the `data-service` module to query Delta Lake tables. The `Chatbot` class:

1. Defines MCP-style tools for listing, describing, and querying tables
2. Sends user messages to Claude with these tools available
3. Handles tool calls by invoking `data-service` functions in-process
4. Returns results to the user

## Routes

- `GET /` — render the chat UI
- `POST /api/chat` — send a message (returns assistant response)
- `POST /api/clear` — clear the chat history
- `GET /api/history` — retrieve the full conversation history

## Testing

Placeholder smoke tests exist in `tests/`. Run with:

```bash
uv run --directory webapp pytest
```
