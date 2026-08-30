"""Tests del modo de ejecución (SPIK_MODE) y su efecto en la config del frontend.

Cubre los tres modos: 'local' (app del host, todo), 'server' (Traefik, sin captura) y
'appliance' (contenedor privilegiado para compartir: captura sí, filtro de ruido en vivo no).

Código puro: no toca whisperx/ffmpeg/systemctl ni levanta un servidor HTTP. Las rutas de FastAPI
son funciones normales que leen config.MODE en tiempo de llamada y lanzan HTTPException, así que
las invocamos directamente (basta con monkeypatchear el atributo config.MODE).
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi import HTTPException

from spik import config
from web import state
from web.deps import require_local
from web.routers import library
from web.routers.config import app_config
from web.routers.library import history, list_projects, list_videos
from web.routers.noise import noise_set_default, noise_status, noise_toggle
from web.routers.recording import record_start


# ---------------------------------------------------------------------------
# Parseo de SPIK_MODE en spik.config
# ---------------------------------------------------------------------------
@pytest.fixture
def reload_config():
    """Recarga spik.config tras el test para deshacer cualquier reload con env modificado."""
    yield
    importlib.reload(config)


@pytest.mark.parametrize("value, expected", [
    ("local", "local"),
    ("server", "server"),
    ("appliance", "appliance"),
    ("APPLIANCE", "appliance"),   # se normaliza a minúsculas
    (None, "local"),             # sin variable => default 'local'
])
def test_mode_parsing(monkeypatch, reload_config, value, expected):
    monkeypatch.delenv("SPIK_MODE", raising=False)
    monkeypatch.delenv("SPEAK_MODE", raising=False)
    if value is not None:
        monkeypatch.setenv("SPIK_MODE", value)
    reloaded = importlib.reload(config)
    assert reloaded.MODE == expected


def test_mode_accepts_legacy_speak_prefix(monkeypatch, reload_config):
    """El prefijo histórico SPEAK_* sigue funcionando (compat con .env viejos)."""
    monkeypatch.delenv("SPIK_MODE", raising=False)
    monkeypatch.setenv("SPEAK_MODE", "appliance")
    reloaded = importlib.reload(config)
    assert reloaded.MODE == "appliance"


# ---------------------------------------------------------------------------
# /api/config expone el modo y la disponibilidad del filtro de ruido
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode, host_only", [
    ("local", True),        # solo la app del host tiene el filtro en vivo y la cámara virtual
    ("server", False),
    ("appliance", False),   # el contenedor no gestiona systemctl --user ni el módulo de kernel
])
def test_api_config_reports_mode_and_noise(monkeypatch, mode, host_only):
    monkeypatch.setattr(config, "MODE", mode)
    assert app_config() == {"mode": mode, "noise": host_only, "vcam": host_only}


# ---------------------------------------------------------------------------
# /api/noise/status hace cortocircuito fuera de 'local' (no toca systemctl)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["server", "appliance"])
def test_noise_status_short_circuits_off_host(monkeypatch, mode):
    monkeypatch.setattr(config, "MODE", mode)
    assert noise_status() == {"active": False, "available": False, "is_default": False}


# ---------------------------------------------------------------------------
# Los endpoints del filtro de ruido en vivo requieren la sesión del host (503 fuera de 'local')
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["server", "appliance"])
def test_noise_toggle_blocked_off_host(monkeypatch, mode):
    monkeypatch.setattr(config, "MODE", mode)
    with pytest.raises(HTTPException) as exc:
        noise_toggle(on=True)
    assert exc.value.status_code == 503


@pytest.mark.parametrize("mode", ["server", "appliance"])
def test_noise_set_default_blocked_off_host(monkeypatch, mode):
    monkeypatch.setattr(config, "MODE", mode)
    with pytest.raises(HTTPException) as exc:
        noise_set_default()
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# La captura se bloquea SOLO en 'server'; en 'local'/'appliance' el guard pasa
# ---------------------------------------------------------------------------
def test_require_local_blocks_only_server(monkeypatch):
    monkeypatch.setattr(config, "MODE", "server")
    with pytest.raises(HTTPException) as exc:
        require_local()
    assert exc.value.status_code == 503

    for mode in ("local", "appliance"):
        monkeypatch.setattr(config, "MODE", mode)
        require_local()  # no debe lanzar


# ---------------------------------------------------------------------------
# Historial/proyectos/servir video están disponibles en TODOS los modos
# (la organización y la consulta no dependen de tener cámara/micro)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["local", "server", "appliance"])
def test_history_projects_videos_available_in_all_modes(monkeypatch, tmp_path, mode):
    monkeypatch.setattr(config, "MODE", mode)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "s.db")
    monkeypatch.setattr(library, "PROJECTS_FILE", tmp_path / ".projects.json")
    # Ninguna de estas debe lanzar por modo.
    list_projects()
    list_videos()
    history()


def test_record_start_blocked_in_server(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MODE", "server")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    state.capture.proc = None
    state.capture.sinks = set()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(record_start(
            audio_source="s", video_device="/dev/video4", name="c", project="default",
        ))
    assert exc.value.status_code == 503
