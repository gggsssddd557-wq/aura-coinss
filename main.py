import os
import hashlib
import hmac
import json
import time
import asyncio
from pathlib import Path
from urllib.parse import parse_qsl
from contextlib import asynccontextmanager, suppress

import psycopg
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
WEB_APP_URL = "https://aura-coinss.onrender.com"
INDEX_FILE = Path(__file__).resolve().parent / "index.html"

dp = Dispatcher()


def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(DATABASE_URL)


def init_db():
    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT,
                coins BIGINT DEFAULT 0,
                energy INTEGER DEFAULT 100,
                energy_updated DOUBLE PRECISION DEFAULT 0,
                click_power INTEGER DEFAULT 1,
                click_level INTEGER DEFAULT 0,
                energy_level INTEGER DEFAULT 0,
                last_daily DOUBLE PRECISION DEFAULT 0
            )
        """)


def verify(data):
    if not BOT_TOKEN:
        raise HTTPException(500, "BOT_TOKEN is not configured")

    if not data:
        raise HTTPException(401, "Telegram initData is empty")

    values = dict(parse_qsl(data, keep_blank_values=True))
    received = values.pop("hash", None)

    if not received:
        raise HTTPException(401, "Telegram hash is missing")

    check = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )

    secret = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated = hmac.new(
        secret,
        check.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated, received):
        raise HTTPException(401, "Telegram signature is invalid")

    try:
        auth_date = int(values.get("auth_date", "0"))

        if time.time() - auth_date > 86400:
            raise HTTPException(401, "Telegram data expired")

        if auth_date > time.time() + 60:
            raise HTTPException(401, "Invalid Telegram auth date")

        user = json.loads(values["user"])

        if not isinstance(user, dict) or "id" not in user:
            raise HTTPException(401, "Invalid Telegram user")

        return user

    except (KeyError, ValueError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid Telegram user data")


def current_user(authorization):
    user = verify(authorization)
    return str(user["id"]), (
        user.get("username") or user.get("first_name") or "Player"
    )


def update_energy(con, uid, name):
    now = time.time()

    con.execute("""
        INSERT INTO users (id, name)
        VALUES (%s, %s)
        ON CONFLICT (id) DO NOTHING
    """, (uid, name))

    row = con.execute("""
        SELECT energy, energy_updated, energy_level,
               coins, click_power, click_level, last_daily
        FROM users WHERE id=%s
    """, (uid,)).fetchone()

    energy, updated, energy_level, coins, power, click_level, daily = row

    maximum = 100 + energy_level * 20

    if updated == 0:
        updated = now

    gained = int((now - updated) // 5)

    if gained > 0:
        energy = min(maximum, energy + gained)
        updated += gained * 5

    con.execute("""
        UPDATE users
        SET name=%s, energy=%s, energy_updated=%s
        WHERE id=%s
    """, (name, energy, updated, uid))

    return {
        "coins": coins,
        "energy": energy,
        "max_energy": maximum,
        "click_power": power,
        "click_level": click_level,
        "energy_level": energy_level,
        "daily_ready": now - daily >= 86400
    }


@dp.message(CommandStart())
async def start_command(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Играть 🪙",
            web_app=WebAppInfo(url=WEB_APP_URL)
        )
    ]])

    await message.answer(
        "Добро пожаловать в Aura Coins! 🪙\n"
        "Нажми кнопку ниже, чтобы начать игру.",
        reply_markup=keyboard
    )


@asynccontextmanager
async def lifespan(app):
    init_db()

    bot = None
    task = None

    if BOT_TOKEN:
        bot = Bot(token=BOT_TOKEN)
        await bot.delete_webhook(drop_pending_updates=False)
        task = asyncio.create_task(dp.start_polling(bot))

    yield

    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    if bot:
        await bot.session.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def home():
    if not INDEX_FILE.exists():
        raise HTTPException(404, "index.html not found")
    return FileResponse(INDEX_FILE)


@app.get("/api/me")
def me(authorization: str = Header(None)):
    uid, name = current_user(authorization)

    with connect() as con:
        return update_energy(con, uid, name)


@app.post("/api/tap")
def tap(authorization: str = Header(None)):
    uid, name = current_user(authorization)

    with connect() as con:
        state = update_energy(con, uid, name)

        if state["energy"] < 1:
            raise HTTPException(400, "Нет энергии")

        reward = state["click_power"]

        con.execute("""
            UPDATE users
            SET coins=coins+%s, energy=energy-1
            WHERE id=%s
        """, (reward, uid))

        state["coins"] += reward
        state["energy"] -= 1

        return state


@app.post("/api/upgrade/{kind}")
def upgrade(kind: str, authorization: str = Header(None)):
    if kind not in ("click", "energy"):
        raise HTTPException(400, "Unknown upgrade")

    uid, name = current_user(authorization)

    with connect() as con:
        state = update_energy(con, uid, name)

        if kind == "click":
            level = state["click_level"]
            price = 50 * (level + 1)

            if state["coins"] < price:
                raise HTTPException(400, "Недостаточно монет")

            con.execute("""
                UPDATE users
                SET coins=coins-%s,
                    click_power=click_power+1,
                    click_level=click_level+1
                WHERE id=%s
            """, (price, uid))

        else:
            level = state["energy_level"]
            price = 100 * (level + 1)

            if state["coins"] < price:
                raise HTTPException(400, "Недостаточно монет")

            con.execute("""
                UPDATE users
                SET coins=coins-%s,
                    energy_level=energy_level+1
                WHERE id=%s
            """, (price, uid))

        return update_energy(con, uid, name)


@app.post("/api/daily")
def daily(authorization: str = Header(None)):
    uid, name = current_user(authorization)
    now = time.time()

    with connect() as con:
        state = update_energy(con, uid, name)

        row = con.execute(
            "SELECT last_daily FROM users WHERE id=%s",
            (uid,)
        ).fetchone()

        if now - row[0] < 86400:
            raise HTTPException(400, "Награда уже получена")

        con.execute("""
            UPDATE users
            SET coins=coins+100, last_daily=%s
            WHERE id=%s
        """, (now, uid))

        state["coins"] += 100
        state["daily_ready"] = False

        return state


@app.get("/api/top")
def top():
    with connect() as con:
        rows = con.execute("""
            SELECT name, coins FROM users
            ORDER BY coins DESC LIMIT 20
        """).fetchall()

    return [
        {"name": row[0], "coins": row[1]}
        for row in rows
    ]
