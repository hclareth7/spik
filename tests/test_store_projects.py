"""Tests de la capa de almacenamiento con proyectos (columna `project` + migración).

Código puro: SQLite en `tmp_path`, sin whisperx/ffmpeg. Verifica que una DB antigua (sin la
columna `project`) migra en su lugar y que las filas heredadas quedan en el proyecto 'default'.
"""

from __future__ import annotations

import sqlite3

from spik import config, store

# Esquema PREVIO a los proyectos (sin la columna `project`), para simular una DB existente.
_OLD_SCHEMA = """
CREATE TABLE sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    video_path    TEXT NOT NULL,
    language      TEXT,
    duration_s    REAL,
    wpm           REAL,
    filler_count  INTEGER,
    fillers_per_min REAL,
    overall_score INTEGER,
    metrics_json  TEXT NOT NULL,
    feedback_json TEXT
);
"""


def _row(**kw) -> store.SessionRow:
    base = dict(
        created_at="2026-01-01T00:00:00+00:00", video_path="/x/a.mkv", language="es",
        duration_s=1.0, wpm=100.0, filler_count=0, fillers_per_min=0.0,
        overall_score=None, metrics={"a": 1}, feedback=None,
    )
    base.update(kw)
    return store.SessionRow(**base)


def test_migration_adds_project_to_legacy_db(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    conn = sqlite3.connect(db)
    conn.execute(_OLD_SCHEMA)
    conn.execute(
        "INSERT INTO sessions (created_at, video_path, metrics_json) VALUES (?, ?, ?)",
        ("2026-01-01T00:00:00+00:00", "/x/legacy.mkv", "{}"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(config, "DB_PATH", db)
    rows = store.history()  # _connect -> _migrate añade la columna
    assert len(rows) == 1
    assert rows[0]["project"] == "default"


def test_save_and_filter_by_project(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "s.db")
    store.save(_row(video_path="/x/a.mkv", project="alpha"))
    store.save(_row(video_path="/x/b.mkv", project="default"))

    assert len(store.history()) == 2
    alpha = store.history(project="alpha")
    assert len(alpha) == 1 and alpha[0]["project"] == "alpha"
    assert len(store.history(project="default")) == 1


def test_fresh_db_has_project_column(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "s.db")
    store.save(_row())
    assert store.history()[0]["project"] == "default"
