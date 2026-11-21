import os
import sqlite3
from fastapi import FastAPI

app = FastAPI()
DB = "coins.db"

def init_db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, coins INTEGER DEFAULT 0)")
    con.commit()
    con.close()

init_db()

@app.get("/")
def home():
    return {"status": "Aura Coins server is running"}

@app.get("/balance/{user_id}")
def balance(user_id: str):
    con = sqlite3.connect(DB)
    row = con.execute("SELECT coins FROM users WHERE id=?", (user_id,)).fetchone()
    con.close()
    return {"coins": row[0] if row else 0}

@app.post("/tap/{user_id}")
def tap(user_id: str):
    con = sqlite3.connect(DB)
    con.execute("INSERT OR IGNORE INTO users (id, coins) VALUES (?, 0)", (user_id,))
    con.execute("UPDATE users SET coins=coins+1 WHERE id=?", (user_id,))
    con.commit()
    row = con.execute("SELECT coins FROM users WHERE id=?", (user_id,)).fetchone()
    con.close()
    return {"coins": row[0]}
