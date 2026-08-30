"""Local session persistence in SQLite (to measure progress over time)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,          -- ISO 8601, provided by the caller
    video_path    TEXT NOT NULL,
    language      TEXT,
    duration_s    REAL,
    wpm           REAL,
    filler_count  INTEGER,
    fillers_per_min REAL,
    overall_score INTEGER,                -- Claude score (may be NULL without feedback)
    metrics_json  TEXT NOT NULL,          -- full metrics
    feedback_json TEXT,                   -- full feedback (may be NULL)
    project       TEXT NOT NULL DEFAULT 'default'  -- project the session belongs to
);
"""


@dataclass
class SessionRow:
    created_at: str
    video_path: str
    language: str
    duration_s: float
    wpm: float
    filler_count: int
    fillers_per_min: float
    overall_score: int | None
    metrics: dict
    feedback: dict | None
    project: str = "default"


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent migrations on `sessions` (SQLite has no schema versions here).

    `ADD COLUMN` with a CONSTANT default ('default') leaves old rows in the 'default'
    project without needing a backfill UPDATE.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "project" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN project TEXT NOT NULL DEFAULT 'default'")


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    _migrate(conn)
    return conn


def save(row: SessionRow, db_path: Path | None = None) -> int:
    """Save a session and return its id."""
    db_path = db_path or config.DB_PATH
    with _connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO sessions
               (created_at, video_path, language, duration_s, wpm, filler_count,
                fillers_per_min, overall_score, metrics_json, feedback_json, project)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row.created_at, row.video_path, row.language, row.duration_s, row.wpm,
                row.filler_count, row.fillers_per_min, row.overall_score,
                json.dumps(row.metrics, ensure_ascii=False),
                json.dumps(row.feedback, ensure_ascii=False) if row.feedback else None,
                row.project,
            ),
        )
        return int(cur.lastrowid)


def delete_by_video(video_path: str, db_path: Path | None = None) -> int:
    """Delete every session row for ``video_path``. Returns the number of rows removed.

    Single-table store with no foreign keys, so this is the manual cascade used when a
    recording is deleted from disk (keeps history from pointing at a missing file).
    """
    db_path = db_path or config.DB_PATH
    if not Path(db_path).exists():
        return 0
    with _connect(db_path) as conn:
        cur = conn.execute("DELETE FROM sessions WHERE video_path = ?", (video_path,))
        return cur.rowcount


def history(db_path: Path | None = None, limit: int = 50, project: str | None = None) -> list[dict]:
    """Return the latest sessions (to see trends), optionally for a single project."""
    db_path = db_path or config.DB_PATH
    if not Path(db_path).exists():
        return []
    with _connect(db_path) as conn:
        if project:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE project = ? ORDER BY created_at DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
