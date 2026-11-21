import os
import sqlite3
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DB = "coins.db"

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"


def init_db():
    with sqlite3.connect(DB) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT,
                coins INTEGER DEFAULT 0
            )
        """)


def verify(data):
    if not BOT_TOKEN or not data:
        raise HTTPException(401, "Unauthorized")

    values = dict(parse_qsl(data))
    received = values.pop("hash", None)

    if not received:
        raise HTTPException(401, "Invalid data")

    check = "\n".join(
        f"{k}={v}" for k, v in sorted(values.items())
    )

    secret = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated = hmac.new(
        secret, check.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received):
        raise HTTPException(401, "Invalid signature")

    try:
        if time.time() - int(values.get("auth_date", 0)) > 86400:
            raise HTTPException(401, "Expired data")
        return json.loads(values["user"])
    except (KeyError, ValueError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid user data")


init_db()


@app.get("/")
def home():
    if not INDEX_FILE.exists():
        raise HTTPException(404, "index.html not found")
    return FileResponse(INDEX_FILE)


@app.get("/api/me")
def me(authorization: str = Header(None)):
    user = verify(authorization)
    uid = str(user["id"])
    name = user.get("username") or user.get("first_name", "Player")

    with sqlite3.connect(DB) as con:
        con.execute(
            "INSERT OR IGNORE INTO users (id, name, coins) VALUES (?, ?, 0)",
            (uid, name)
        )
        con.execute("UPDATE users SET name=? WHERE id=?", (name, uid))
        row = con.execute(
            "SELECT coins FROM users WHERE id=?", (uid,)
        ).fetchone()

    return {"id": uid, "name": name, "coins": row[0]}


@app.post("/api/tap")
def tap(authorization: str = Header(None)):
    user = verify(authorization)
    uid = str(user["id"])
    name = user.get("username") or user.get("first_name", "Player")

    with sqlite3.connect(DB) as con:
        con.execute(
            "INSERT OR IGNORE INTO users (id, name, coins) VALUES (?, ?, 0)",
            (uid, name)
        )
        con.execute(
            "UPDATE users SET name=?, coins=coins+1 WHERE id=?",
            (name, uid)
        )
        row = con.execute(
            "SELECT coins FROM users WHERE id=?", (uid,)
        ).fetchone()

    return {"coins": row[0]}


@app.get("/api/top")
def top():
    with sqlite3.connect(DB) as con:
        rows = con.execute(
            "SELECT name, coins FROM users ORDER BY coins DESC LIMIT 20"
        ).fetchall()

    return [{"name": r[0], "coins": r[1]} for r in rows]
