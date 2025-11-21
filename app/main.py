from flask import Flask, jsonify
import os
import socket

app = Flask(__name__)

@app.route("/")
def home():
    # Simple response that includes container hostname
    return jsonify({
        "message": "Hello from Agentic AIOps Demo App!",
        "host": socket.gethostname(),
        "env": dict(os.environ)
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

