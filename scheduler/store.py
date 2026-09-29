"""
Persistent store for per-user Google tokens and web sessions.

Backends:
  - DATABASE_URL set (postgres://... — e.g. Neon on Render): psycopg
  - otherwise: local SQLite file (SCHEDULER_DB, default scheduler.db)

Deliberately tiny — two tables, no ORM. Every call opens a short-lived
connection, which suits both SQLite and serverless Postgres (Neon closes
idle connections anyway) and keeps the module state-free for tests.
"""

import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path


def _database_url() -> str | None:
    return os.getenv("DATABASE_URL") or None


def _sqlite_path() -> Path:
    return Path(os.getenv("SCHEDULER_DB", "scheduler.db"))


def _is_postgres() -> bool:
    return _database_url() is not None


@contextlib.contextmanager
def _connect():
    url = _database_url()
    if url:
        import psycopg
        conn = psycopg.connect(url)
    else:
        conn = sqlite3.connect(_sqlite_path())
        conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


def _ph(sql: str) -> str:
    """Adapt '?' placeholders to '%s' for postgres."""
    return sql.replace("?", "%s") if _is_postgres() else sql


_initialized = False


def ensure_init() -> None:
    """Create tables once, lazily. Lets the app survive a database that was
    unreachable at startup — the first request that needs it initializes."""
    global _initialized
    if _initialized:
        return
    init()


def init() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    global _initialized
    with _connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tokens ("
            "  user_email TEXT PRIMARY KEY,"
            "  token_json TEXT NOT NULL,"
            "  updated_at DOUBLE PRECISION NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "  session_id TEXT PRIMARY KEY,"
            "  user_email TEXT NOT NULL,"
            "  schedule_json TEXT NOT NULL,"
            "  config_json TEXT NOT NULL,"
            "  confirmed INTEGER NOT NULL,"
            "  constraints_json TEXT,"
            "  updated_at DOUBLE PRECISION NOT NULL)"
        )
        conn.commit()
    _initialized = True


# ── Tokens ─────────────────────────────────────────────────────────────────

def save_token(user_email: str, token_json: str) -> None:
    ensure_init()
    with _connect() as conn:
        conn.execute(
            _ph(
                "INSERT INTO tokens (user_email, token_json, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (user_email) DO UPDATE SET "
                "token_json = excluded.token_json, updated_at = excluded.updated_at"
            ),
            (user_email, token_json, time.time()),
        )
        conn.commit()


def load_token(user_email: str) -> str | None:
    ensure_init()
    with _connect() as conn:
        row = conn.execute(
            _ph("SELECT token_json FROM tokens WHERE user_email = ?"),
            (user_email,),
        ).fetchone()
    return row[0] if row else None


def delete_token(user_email: str) -> None:
    ensure_init()
    with _connect() as conn:
        conn.execute(_ph("DELETE FROM tokens WHERE user_email = ?"), (user_email,))
        conn.commit()


# ── Web sessions ───────────────────────────────────────────────────────────

def save_session(
    session_id: str,
    user_email: str,
    schedule: list[dict],
    config: dict,
    confirmed: bool,
    constraints: dict | None,
) -> None:
    ensure_init()
    with _connect() as conn:
        conn.execute(
            _ph(
                "INSERT INTO sessions (session_id, user_email, schedule_json, "
                "config_json, confirmed, constraints_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (session_id) DO UPDATE SET "
                "schedule_json = excluded.schedule_json, "
                "config_json = excluded.config_json, "
                "confirmed = excluded.confirmed, "
                "constraints_json = excluded.constraints_json, "
                "updated_at = excluded.updated_at"
            ),
            (
                session_id,
                user_email,
                json.dumps(schedule),
                json.dumps(config),
                int(confirmed),
                json.dumps(constraints) if constraints is not None else None,
                time.time(),
            ),
        )
        conn.commit()


def load_session(session_id: str) -> dict | None:
    """Return {user_email, schedule, config, confirmed, constraints} or None."""
    ensure_init()
    with _connect() as conn:
        row = conn.execute(
            _ph(
                "SELECT user_email, schedule_json, config_json, confirmed, "
                "constraints_json FROM sessions WHERE session_id = ?"
            ),
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    user_email, schedule_json, config_json, confirmed, constraints_json = row
    return {
        "user_email": user_email,
        "schedule": json.loads(schedule_json),
        "config": json.loads(config_json),
        "confirmed": bool(confirmed),
        "constraints": json.loads(constraints_json) if constraints_json else None,
    }
