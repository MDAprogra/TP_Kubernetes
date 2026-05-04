from flask import Flask, jsonify, request
import os, socket, datetime

app = Flask(__name__)
MESSAGES = ["Bienvenue sur le livre d'or k8s !"]

@app.route("/api/messages", methods=["GET", "POST"])
def messages():
    if request.method == "POST":
        data = request.get_json(force=True)
        MESSAGES.append(data.get("text", ""))
    return jsonify({
        "messages": MESSAGES,
        "served_by": socket.gethostname(),
        "env": os.environ.get("APP_ENV", "dev"),
        "ts": datetime.datetime.utcnow().isoformat()
    })

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)