from flask import Flask, jsonify, request, Response
import os, socket, datetime, time
import psycopg2
from psycopg2 import pool
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

DB_URL = os.environ.get("DATABASE_URL")
DB_POOL = None
MEM_MESSAGES = []

# --- Métriques Prometheus ---
MESSAGES_TOTAL = Counter(
    "guestbook_messages_total",
    "Nombre total de messages postés"
)
REQUEST_LATENCY = Histogram(
    "guestbook_request_seconds",
    "Latence des requêtes",
    ["endpoint"]
)

@app.before_request
def _start_timer():
    request._start = time.time()

@app.after_request
def _record_latency(resp):
    elapsed = time.time() - getattr(request, "_start", time.time())
    REQUEST_LATENCY.labels(endpoint=request.path).observe(elapsed)
    return resp

# --- DB ---
def init_db():
    global DB_POOL
    if not DB_URL:
        return
    DB_POOL = pool.SimpleConnectionPool(1, 5, dsn=DB_URL)
    with DB_POOL.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages(
                    id SERIAL PRIMARY KEY,
                    text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.commit()
        DB_POOL.putconn(conn)

@app.route("/api/messages", methods=["GET", "POST"])
def messages():
    if DB_POOL:
        conn = DB_POOL.getconn()
        try:
            with conn.cursor() as cur:
                if request.method == "POST":
                    text = request.get_json(force=True).get("text", "")
                    cur.execute("INSERT INTO messages(text) VALUES (%s)", (text,))
                    conn.commit()
                    MESSAGES_TOTAL.inc()          # ← compteur incrémenté
                cur.execute("SELECT text FROM messages ORDER BY id")
                msgs = [r[0] for r in cur.fetchall()]
        finally:
            DB_POOL.putconn(conn)
    else:
        if request.method == "POST":
            MEM_MESSAGES.append(request.get_json(force=True).get("text", ""))
            MESSAGES_TOTAL.inc()                  # ← aussi en mode mémoire
        msgs = MEM_MESSAGES

    return jsonify({
        "messages": msgs,
        "served_by": socket.gethostname(),
        "env": os.environ.get("APP_ENV", "dev"),
        "welcome": os.environ.get("WELCOME_MESSAGE", ""),
        "backend_mode": "postgres" if DB_POOL else "memory",
        "ts": datetime.datetime.utcnow().isoformat()
    })

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)