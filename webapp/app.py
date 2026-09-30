"""
Flask web server for the Football Manager chatbot UI.
"""

import os

from chatbot import Chatbot
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
bot = Chatbot()


@app.route("/")
def index():
    """Serve the chatbot UI."""
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    """Handle chat messages."""
    try:
        data = request.json
        user_message = data.get("message", "").strip()

        if not user_message:
            return jsonify({"error": "Empty message"}), 400

        response = bot.chat(user_message)
        return jsonify({"response": response})

    except (ValueError, KeyError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/clear", methods=["POST"])
def clear():
    """Clear chat history."""
    bot.clear_history()
    return jsonify({"status": "cleared"})


@app.route("/api/history", methods=["GET"])
def history():
    """Get chat history."""
    return jsonify({"history": bot.get_history()})


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "True").lower() == "true"
    port = int(os.getenv("FLASK_PORT", "5000"))
    app.run(debug=debug, port=port)
