"""Tests for deleting recordings and empty projects.

Route functions are invoked directly (they read config at call time). `PROJECTS_FILE` is
computed at import, so it is monkeypatched alongside `config.DATA_DIR`, mirroring
test_projects_api.py. The single-owner capture session and the job registry are reset so the
"busy" guards start from a clean state.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from spik import config, store
from web import state
from web.routers import library
from web.routers.library import delete_project, delete_video
from web.validation import safe_project_dir


@pytest.fixture(autouse=True)
def _reset_state():
    def _clear():
        state.capture.proc = None
        state.capture.sinks = set()
        state.capture.record_path = None
        state.jobs.jobs = {}

    _clear()
    yield
    _clear()


def _use_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "sessions.db")
    monkeypatch.setattr(config, "MODE", "local")
    monkeypatch.setattr(library, "PROJECTS_FILE", tmp_path / ".projects.json")


# ============================================================================
# store.delete_by_video (pure)
# ============================================================================
def test_delete_by_video_removes_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "s.db")
    row = store.SessionRow(
        created_at="2026-01-01T00:00:00+00:00", video_path="/x/a.mkv", language="es",
        duration_s=1.0, wpm=100.0, filler_count=0, fillers_per_min=0.0,
        overall_score=None, metrics={"a": 1}, feedback=None,
    )
    store.save(row)
    assert store.delete_by_video("/x/a.mkv") == 1
    assert store.history() == []


# ============================================================================
# DELETE /api/video
# ============================================================================
def test_delete_video_removes_file_wav_and_history(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    proj = tmp_path / "alpha"
    proj.mkdir()
    vid = proj / "clip.mkv"
    vid.write_bytes(b"x")
    wav = proj / "clip.wav"
    wav.write_bytes(b"x")
    store.save(store.SessionRow(
        created_at="2026-01-01T00:00:00+00:00", video_path=str(vid.resolve()), language="es",
        duration_s=1.0, wpm=100.0, filler_count=0, fillers_per_min=0.0,
        overall_score=None, metrics={"a": 1}, feedback=None, project="alpha",
    ))

    out = delete_video(path=str(vid))
    assert out["deleted"] is True
    assert not vid.exists()
    assert not wav.exists()          # sibling WAV removed too
    assert out["sessions_removed"] >= 1
    assert store.history() == []


def test_delete_video_orphan_row_purges_history_without_fs(tmp_path, monkeypatch):
    """A history row pointing OUTSIDE DATA_DIR (legacy '/data/…' container path) or to a
    missing file must be prunable: purge the row, never touch the filesystem."""
    _use_tmp(tmp_path, monkeypatch)
    store.save(store.SessionRow(
        created_at="2026-01-01T00:00:00+00:00", video_path="/data/practica.mkv", language="es",
        duration_s=1.0, wpm=100.0, filler_count=0, fillers_per_min=0.0,
        overall_score=None, metrics={"a": 1}, feedback=None, project="default",
    ))
    out = delete_video(path="/data/practica.mkv")
    assert out["deleted"] is False           # no file was removed from disk
    assert out["sessions_removed"] == 1
    assert store.history() == []


def test_delete_video_unknown_path_404(tmp_path, monkeypatch):
    """Outside DATA_DIR AND no matching history row -> nothing to do, 404 (never a disk touch)."""
    _use_tmp(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        delete_video(path="/etc/passwd")
    assert e.value.status_code == 404


@pytest.mark.parametrize("mode", ["server"])
def test_delete_video_blocked_off_host(tmp_path, monkeypatch, mode):
    _use_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "MODE", mode)
    vid = tmp_path / "x.mkv"
    vid.write_bytes(b"x")
    with pytest.raises(HTTPException) as e:
        delete_video(path=str(vid))
    assert e.value.status_code == 503


def test_delete_video_refused_during_recording(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    vid = tmp_path / "rec.mkv"
    vid.write_bytes(b"x")
    # Simulate an active recording writing exactly this file.
    state.capture.proc = object()
    state.capture.sinks = {"record"}
    state.capture.record_path = str(vid)

    with pytest.raises(HTTPException) as e:
        delete_video(path=str(vid))
    assert e.value.status_code == 409
    assert vid.exists()  # untouched


def test_delete_video_refused_during_analysis(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    vid = tmp_path / "a.mkv"
    vid.write_bytes(b"x")
    state.jobs.jobs = {"job1": {"status": "running"}}

    with pytest.raises(HTTPException) as e:
        delete_video(path=str(vid))
    assert e.value.status_code == 409
    assert vid.exists()


# ============================================================================
# DELETE /api/projects
# ============================================================================
def test_delete_project_rejects_default(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        delete_project(slug="default")
    assert e.value.status_code == 400


def test_delete_project_rejects_non_empty(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    proj = tmp_path / "alpha"
    proj.mkdir()
    (proj / "a.mkv").write_bytes(b"x")
    with pytest.raises(HTTPException) as e:
        delete_project(slug="alpha")
    assert e.value.status_code == 400
    assert proj.exists()  # not removed


def test_delete_project_removes_empty_folder_and_slug(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    library.create_project(slug="alpha")  # creates folder + persists slug
    assert (tmp_path / "alpha").is_dir()

    out = delete_project(slug="alpha")
    assert not (tmp_path / "alpha").exists()
    slugs = {p["slug"] for p in out["projects"]}
    assert "alpha" not in slugs
    # slug removed from the persisted file too
    assert "alpha" not in (tmp_path / ".projects.json").read_text()


def test_safe_project_dir_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with pytest.raises(HTTPException) as e:
        safe_project_dir("../evil")
    assert e.value.status_code == 400
