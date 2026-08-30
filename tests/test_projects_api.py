"""Tests de la API de proyectos y de que la grabación escribe en la subcarpeta del proyecto.

Se invocan las rutas directamente. `PROJECTS_FILE` se calcula al importar el módulo, así que se
monkeypatchea junto con `config.DATA_DIR`.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from spik import config
from web import state
from web.routers import library
from web.routers.library import create_project, list_projects, list_videos
from web.routers.recording import record_start


@pytest.fixture(autouse=True)
def _reset_capture():
    """Leave the single-owner capture session clean before and after each test."""
    def _clear():
        log = state.capture.log
        if log is not None and hasattr(log, "close"):
            try:
                log.close()
            except Exception:
                pass
        state.capture.proc = None
        state.capture.sinks = set()
        state.capture.device = None
        state.capture.record_path = None
        state.capture.log = None

    _clear()
    yield
    _clear()


def _patch_capture(monkeypatch, proc, captured, write_stderr=False):
    """Stub the impure boundaries of CaptureSession.start (probe + subprocess spawn).

    Lets record_start run without launching ffmpeg or opening a camera, while capturing the
    command that WOULD have been spawned so we can assert on it.
    """
    monkeypatch.setattr(state, "probe_formats", lambda dev: "mjpeg : 1280x720")

    async def _fake_exec(*cmd, **k):
        captured["cmd"] = list(cmd)
        if write_stderr:
            k["stderr"].write(b"video4: Device or resource busy\n")
            k["stderr"].flush()
        return proc

    monkeypatch.setattr(state.asyncio, "create_subprocess_exec", _fake_exec)


class _FakeAliveProc:
    """Still-running process: wait() times out => record_start treats it as OK."""
    returncode = None

    async def wait(self):
        raise asyncio.TimeoutError

    def terminate(self): pass
    def kill(self): pass
    def send_signal(self, sig): pass


class _DeadProc:
    """Process that already exited => record_start treats it as immediate failure."""
    returncode = 1

    async def wait(self):
        return 1

    def terminate(self): pass
    def kill(self): pass
    def send_signal(self, sig): pass


def _use_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(library, "PROJECTS_FILE", tmp_path / ".projects.json")


def test_create_project_rejects_bad_slug(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        create_project(slug="../evil")
    assert e.value.status_code == 400


def test_create_project_creates_folder_and_persists(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    out = create_project(slug="marketing")
    assert (tmp_path / "marketing").is_dir()
    assert (tmp_path / ".projects.json").exists()
    slugs = {p["slug"] for p in out["projects"]}
    assert {"marketing", "default"} <= slugs


def test_list_projects_counts_videos(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    (tmp_path / "legacy.mkv").write_bytes(b"x")  # raíz => default
    proj = tmp_path / "alpha"
    proj.mkdir()
    (proj / "a.mkv").write_bytes(b"x")
    (proj / "b.mp4").write_bytes(b"x")

    counts = {p["slug"]: p["count"] for p in list_projects()["projects"]}
    assert counts["default"] == 1
    assert counts["alpha"] == 2


def test_videos_listed_with_project_field(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    proj = tmp_path / "alpha"
    proj.mkdir()
    (proj / "a.mkv").write_bytes(b"x")
    (tmp_path / "legacy.mkv").write_bytes(b"x")

    vids = list_videos()["videos"]
    by_name = {v["name"]: v["project"] for v in vids}
    assert by_name["a.mkv"] == "alpha"
    assert by_name["legacy.mkv"] == "default"

    only_alpha = list_videos(project="alpha")["videos"]
    assert [v["name"] for v in only_alpha] == ["a.mkv"]


def test_record_start_uses_project_folder(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "MODE", "local")
    captured = {}
    _patch_capture(monkeypatch, _FakeAliveProc(), captured)

    res = asyncio.run(record_start(
        audio_source="mysource", video_device="/dev/video4", name="clip", project="alpha",
    ))
    assert res["recording"] is True
    assert res["path"] == str(tmp_path / "alpha" / "clip.mkv")
    assert (tmp_path / "alpha").is_dir()
    # The built ffmpeg command writes to the output path inside the project subfolder.
    assert str(tmp_path / "alpha" / "clip.mkv") in captured["cmd"]


def test_record_start_reports_immediate_ffmpeg_death(tmp_path, monkeypatch):
    """If ffmpeg dies at once (e.g. busy camera), record_start returns 500 with detail."""
    _use_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "MODE", "local")
    captured = {}
    _patch_capture(monkeypatch, _DeadProc(), captured, write_stderr=True)

    with pytest.raises(HTTPException) as e:
        asyncio.run(record_start(
            audio_source="s", video_device="/dev/video4", name="clip", project="alpha",
        ))
    assert e.value.status_code == 500
    assert "busy" in e.value.detail.lower()
    # No active recording must remain after the failure.
    assert state.capture.proc is None


def test_record_start_rejects_bad_project(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "MODE", "local")
    with pytest.raises(HTTPException) as e:
        asyncio.run(record_start(
            audio_source="s", video_device="/dev/video4", name="clip", project="../evil",
        ))
    assert e.value.status_code == 400
