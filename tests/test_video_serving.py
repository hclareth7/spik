"""Tests de servir/abrir grabaciones — foco en la guardia anti path-traversal.

Se invocan las funciones de ruta de FastAPI directamente (leen config en tiempo de llamada).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from spik import config
from web.routers import library
from web.routers.library import open_video
from web.validation import safe_video_path


def test_rejects_absolute_path_outside_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with pytest.raises(HTTPException) as e:
        safe_video_path("/etc/passwd")
    assert e.value.status_code == 403


def test_rejects_dotdot_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with pytest.raises(HTTPException) as e:
        safe_video_path(str(tmp_path / ".." / "outside.mkv"))
    assert e.value.status_code == 403


def test_rejects_nonexistent_in_range(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with pytest.raises(HTTPException) as e:
        safe_video_path(str(tmp_path / "nope.mkv"))
    assert e.value.status_code == 404


def test_rejects_bad_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(HTTPException) as e:
        safe_video_path(str(f))
    assert e.value.status_code == 400


def test_accepts_valid_file_in_project_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    proj = tmp_path / "p"
    proj.mkdir()
    f = proj / "v.mkv"
    f.write_bytes(b"x")
    assert safe_video_path(str(f)) == f.resolve()


def test_symlink_escape_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    outside = tmp_path.parent / "secret.mkv"
    outside.write_bytes(b"x")
    link = tmp_path / "link.mkv"
    link.symlink_to(outside)
    try:
        with pytest.raises(HTTPException) as e:
            safe_video_path(str(link))
        assert e.value.status_code == 403
    finally:
        outside.unlink()


@pytest.mark.parametrize("mode", ["server", "appliance"])
def test_open_video_blocked_off_local(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "MODE", mode)
    with pytest.raises(HTTPException) as e:
        open_video(path=str(tmp_path / "x.mkv"))
    assert e.value.status_code == 503


def test_open_video_local_invokes_xdg_open(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "MODE", "local")
    f = tmp_path / "v.mkv"
    f.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(library.shutil, "which", lambda name: "/usr/bin/xdg-open")
    monkeypatch.setattr(library.subprocess, "Popen", lambda *a, **k: calls.append(a) or object())

    res = open_video(path=str(f))
    assert res["opened"] is True
    assert calls and calls[0][0] == ["xdg-open", str(f.resolve())]
