import os
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import Flask, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)

DB_PATH = os.getenv("DB_PATH", "/data/sqlite.db")
JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
JWT_ISSUER = os.getenv("JWT_ISSUER", "local-dev-key")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
            """
        )
        # Seed a default user for local testing if not already present.
        cur = conn.execute("SELECT id FROM users WHERE username = ?", ("admin",))
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("admin", generate_password_hash("admin123")),
            )
        conn.commit()
    finally:
        conn.close()


def encode_token(username: str) -> str:
    payload = {
        "sub": username,
        "iss": JWT_ISSUER,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str):
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def require_jwt(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "missing bearer token"}), 401

        token = auth.split(" ", 1)[1].strip()
        try:
            claims = decode_token(token)
        except jwt.PyJWTError as exc:
            return jsonify({"error": f"invalid token: {str(exc)}"}), 401

        request.claims = claims
        return fn(*args, **kwargs)

    return wrapper


@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.post("/login")
def login():
    body = request.get_json(silent=True) or {}
    username = body.get("username", "")
    password = body.get("password", "")

    if not username or not password:
        return jsonify({"error": "username and password are required"}), 400

    conn = get_conn()
    try:
        cur = conn.execute("SELECT username, password_hash FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
    finally:
        conn.close()

    if row is None or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "invalid credentials"}), 401

    token = encode_token(username)
    return jsonify({"token": token}), 200


@app.get("/verify")
def verify():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"valid": False, "message": "no bearer token provided"}), 200

    token = auth.split(" ", 1)[1].strip()
    try:
        claims = decode_token(token)
        return jsonify({"valid": True, "claims": claims}), 200
    except jwt.PyJWTError as exc:
        return jsonify({"valid": False, "error": str(exc)}), 200


@app.get("/users")
@require_jwt
def users():
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, username FROM users ORDER BY id").fetchall()
    finally:
        conn.close()

    result = [{"id": row["id"], "username": row["username"]} for row in rows]
    return jsonify({"users": result}), 200


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
