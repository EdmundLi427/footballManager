"""
Chatbot that leverages the Delta Lake MCP server to query and discuss NFL data.

Usage:
    from chatbot import Chatbot

    bot = Chatbot()
    response = bot.chat("How many players are injured?")
    print(response)
"""

import json
import os

import anthropic
from data_service.server import describe_table, list_tables, query, table_history


class Chatbot:
    """A chatbot that can use MCP server tools to query Delta Lake tables."""

    def __init__(self):
        """Initialize the chatbot."""
        from dotenv import load_dotenv

        load_dotenv()

        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = "claude-opus-5-5"
        self.messages = []
        self.tools = self._define_tools()

    def _define_tools(self) -> list:
        """Define the MCP tools available to the chatbot."""
        return [
            {
                "name": "list_tables",
                "description": "List the Delta tables available to query, with their storage paths.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "describe_table",
                "description": "Show a table's columns and types plus Delta metadata: current version, partition columns, number of data files, and description.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {
                            "type": "string",
                            "description": "The name of the table to describe",
                        }
                    },
                    "required": ["table"],
                },
            },
            {
                "name": "table_history",
                "description": "Show recent commits to a Delta table (timestamp, operation, parameters), newest first. Useful for 'when was this last updated?' questions.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {
                            "type": "string",
                            "description": "The name of the table",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of recent commits to show (default: 10)",
                            "default": 10,
                        },
                    },
                    "required": ["table"],
                },
            },
            {
                "name": "query",
                "description": "Run one read-only SQL query (DuckDB dialect) against the Delta tables and return the results as JSON.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "A read-only SQL statement",
                        },
                        "max_rows": {
                            "type": "integer",
                            "description": "Maximum number of rows to return (default: 200)",
                            "default": 200,
                        },
                    },
                    "required": ["sql"],
                },
            },
        ]

    def add_user_message(self, content: str) -> None:
        """Add a user message to the chat history."""
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to the chat history."""
        self.messages.append({"role": "assistant", "content": content})

    def _call_tool(self, tool_name: str, tool_input: dict) -> str:
        """
        Call an MCP tool directly.

        Args:
            tool_name: Name of the tool to call
            tool_input: Input parameters for the tool

        Returns:
            JSON string result from the tool
        """
        try:
            if tool_name == "list_tables":
                result = list_tables()
            elif tool_name == "describe_table":
                result = describe_table(tool_input["table"])
            elif tool_name == "table_history":
                result = table_history(tool_input["table"], tool_input.get("limit", 10))
            elif tool_name == "query":
                result = query(tool_input["sql"], tool_input.get("max_rows", 200))
            else:
                result = json.dumps({"error": f"Unknown tool: {tool_name}"})
            return result
        except (ValueError, KeyError, RuntimeError) as e:
            return json.dumps({"error": str(e)})

    def chat(self, user_message: str) -> str:
        """
        Send a message and get a response, with tool use.

        Args:
            user_message: The user's message

        Returns:
            The assistant's final response
        """
        self.add_user_message(user_message)

        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                tools=self.tools,
                messages=self.messages,
            )

            if response.stop_reason == "end_turn":
                # Extract final text response
                final_response = "".join(
                    block.text for block in response.content if hasattr(block, "text")
                )
                self.add_assistant_message(final_response)
                return final_response

            if response.stop_reason == "tool_use":
                # Process tool calls
                tool_results = []
                assistant_content = response.content

                for block in response.content:
                    if block.type == "tool_use":
                        tool_result = self._call_tool(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": tool_result,
                            }
                        )

                # Add assistant response with tool use
                self.add_assistant_message(assistant_content)

                # Add tool results
                self.messages.append(
                    {
                        "role": "user",
                        "content": tool_results,
                    }
                )
            else:
                # Unknown stop reason
                break

        return "Chatbot encountered an unexpected state."

    def clear_history(self) -> None:
        """Clear the chat history."""
        self.messages = []

    def get_history(self) -> list:
        """Get the current chat history."""
        return self.messages.copy()


if __name__ == "__main__":
    bot = Chatbot()

    print("Football Manager Chatbot")
    print(
        "Type 'quit' to exit, 'clear' to clear history, 'history' to see conversation\n"
    )

    while True:
        try:
            user_input = input("You: ").strip()

            if not user_input:
                continue
            if user_input.lower() == "quit":
                print("Goodbye!")
                break
            if user_input.lower() == "clear":
                bot.clear_history()
                print("Chat history cleared.\n")
                continue
            if user_input.lower() == "history":
                for msg in bot.get_history():
                    role = msg["role"].upper()
                    if isinstance(msg["content"], str):
                        content = msg["content"]
                    else:
                        content = str(msg["content"])
                    print(f"\n{role}:\n{content}")
                print()
                continue

            print("\nAssistant: ", end="", flush=True)
            response = bot.chat(user_input)
            print(f"{response}\n")

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
