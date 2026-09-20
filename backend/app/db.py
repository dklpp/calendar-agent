"""SQLite state: pending confirmations, reminder dedupe, and an audit log."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import secrets
from pathlib import Path
from typing import Any

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
    token       TEXT PRIMARY KEY,
    chat_id     INTEGER,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

-- Written BEFORE the message is sent, so a crash between send and record
-- cannot produce a duplicate on restart.
CREATE TABLE IF NOT EXISTS sent_reminders (
    event_id     TEXT NOT NULL,
    occurrence   TEXT NOT NULL,
    offset_min   INTEGER NOT NULL,
    sent_at      TEXT NOT NULL,
    PRIMARY KEY (event_id, occurrence, offset_min)
);

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    source      TEXT NOT NULL,
    transcript  TEXT,
    intent      TEXT,
    outcome     TEXT
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or get_settings().database_path
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---- pending confirmations ---------------------------------------------


def store_pending(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    chat_id: int | None = None,
    ttl_minutes: int = 30,
) -> str:
    """Stash a parsed-but-unconfirmed action, returning its short token."""
    token = secrets.token_urlsafe(8)
    now = _now()
    conn.execute(
        "INSERT INTO pending (token, chat_id, payload, created_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            token,
            chat_id,
            json.dumps(payload, default=str),
            now.isoformat(),
            (now + dt.timedelta(minutes=ttl_minutes)).isoformat(),
        ),
    )
    conn.commit()
    return token


def take_pending(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    """Fetch and delete a pending action. Expired entries return None.

    Single-use by construction, so a double-tap on Confirm cannot create the
    same meeting twice.
    """
    row = conn.execute("SELECT * FROM pending WHERE token = ?", (token,)).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM pending WHERE token = ?", (token,))
    conn.commit()
    if dt.datetime.fromisoformat(row["expires_at"]) < _now():
        return None
    return json.loads(row["payload"])


def purge_expired(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        "DELETE FROM pending WHERE expires_at < ?", (_now().isoformat(),)
    )
    conn.commit()
    return cursor.rowcount


# ---- reminder dedupe ----------------------------------------------------


def claim_reminder(
    conn: sqlite3.Connection, event_id: str, occurrence: dt.datetime, offset_min: int
) -> bool:
    """Atomically claim the right to send one reminder.

    Returns True exactly once per (event, occurrence, offset). The INSERT is
    the lock, so a restart mid-sweep cannot re-notify.
    """
    try:
        conn.execute(
            "INSERT INTO sent_reminders (event_id, occurrence, offset_min, sent_at)"
            " VALUES (?, ?, ?, ?)",
            (event_id, occurrence.isoformat(), offset_min, _now().isoformat()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def forget_reminders_before(conn: sqlite3.Connection, cutoff: dt.datetime) -> int:
    cursor = conn.execute(
        "DELETE FROM sent_reminders WHERE occurrence < ?", (cutoff.isoformat(),)
    )
    conn.commit()
    return cursor.rowcount


# ---- audit --------------------------------------------------------------


def record(
    conn: sqlite3.Connection,
    *,
    source: str,
    transcript: str | None = None,
    intent: str | None = None,
    outcome: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit (at, source, transcript, intent, outcome)"
        " VALUES (?, ?, ?, ?, ?)",
        (_now().isoformat(), source, transcript, intent, outcome),
    )
    conn.commit()
