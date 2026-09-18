"""SQLite storage for the squad availability bot.

Three tables:
  fixtures  - one row per match posted with /fixture
  responses - one row per (fixture, player) answer
  users     - the roster: everyone the bot has seen in a squad group
"""

import os
import sqlite3
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "squad.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS fixtures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER,
    name        TEXT    NOT NULL,
    match_at    TEXT    NOT NULL,
    deadline_at TEXT    NOT NULL,
    created_by  INTEGER,
    created_at  TEXT    NOT NULL,
    reminded    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS responses (
    fixture_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    answer     TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (fixture_id, user_id)
);

CREATE TABLE IF NOT EXISTS users (
    user_id  INTEGER NOT NULL,
    chat_id  INTEGER NOT NULL,
    name     TEXT    NOT NULL,
    username TEXT,
    dm_ok    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, chat_id)
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- fixtures

def create_fixture(chat_id, name, match_at, deadline_at, created_by):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO fixtures (chat_id, name, match_at, deadline_at, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, name, match_at.isoformat(), deadline_at.isoformat(), created_by, _now()),
        )
        return cur.lastrowid


def set_fixture_message(fixture_id, message_id):
    with connect() as conn:
        conn.execute("UPDATE fixtures SET message_id = ? WHERE id = ?", (message_id, fixture_id))


def get_fixture(fixture_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM fixtures WHERE id = ?", (fixture_id,)).fetchone()


def latest_fixture(chat_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM fixtures WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,)
        ).fetchone()


def pending_reminders():
    """Fixtures that still need their reminder fired. Used to reschedule on restart."""
    with connect() as conn:
        return conn.execute("SELECT * FROM fixtures WHERE reminded = 0").fetchall()


def mark_reminded(fixture_id):
    with connect() as conn:
        conn.execute("UPDATE fixtures SET reminded = 1 WHERE id = ?", (fixture_id,))


# --------------------------------------------------------------- responses

def set_response(fixture_id, user_id, name, answer):
    with connect() as conn:
        conn.execute(
            "INSERT INTO responses (fixture_id, user_id, name, answer, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(fixture_id, user_id) DO UPDATE SET"
            " answer = excluded.answer, name = excluded.name, updated_at = excluded.updated_at",
            (fixture_id, user_id, name, answer, _now()),
        )


def responses_for(fixture_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM responses WHERE fixture_id = ? ORDER BY updated_at", (fixture_id,)
        ).fetchall()


# ------------------------------------------------------------------ roster

def remember_user(user_id, chat_id, name, username):
    with connect() as conn:
        conn.execute(
            "INSERT INTO users (user_id, chat_id, name, username) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(user_id, chat_id) DO UPDATE SET"
            " name = excluded.name, username = excluded.username",
            (user_id, chat_id, name, username),
        )


def mark_dm_ok(user_id):
    with connect() as conn:
        conn.execute("UPDATE users SET dm_ok = 1 WHERE user_id = ?", (user_id,))


def roster(chat_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE chat_id = ? ORDER BY name", (chat_id,)
        ).fetchall()


def non_responders(fixture_id, chat_id):
    with connect() as conn:
        return conn.execute(
            "SELECT u.* FROM users u"
            " WHERE u.chat_id = ?"
            " AND u.user_id NOT IN (SELECT user_id FROM responses WHERE fixture_id = ?)"
            " ORDER BY u.name",
            (chat_id, fixture_id),
        ).fetchall()
