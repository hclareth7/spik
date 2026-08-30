"""HTTP smoke tests for the assembled app (web.main).

These exercise the app through a real ASGI client (TestClient) instead of calling
route functions directly, so they stay decoupled from the internal module layout
and verify that routers are actually wired into the FastAPI instance.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from spik import config
from web.main import app
from web.routers import library

client = TestClient(app)


def test_smoke_config_ok():
    resp = client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "mode" in body and "noise" in body


def test_smoke_projects_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(library, "PROJECTS_FILE", tmp_path / ".projects.json")
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    slugs = {p["slug"] for p in resp.json()["projects"]}
    assert "default" in slugs


def test_smoke_video_path_traversal_forbidden(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    resp = client.get("/api/video", params={"path": "/etc/passwd"})
    assert resp.status_code == 403


def test_smoke_video_missing_under_data_dir_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    resp = client.get("/api/video", params={"path": str(tmp_path / "missing.mkv")})
    assert resp.status_code == 404
