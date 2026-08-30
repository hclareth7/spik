"""App config and UI preferences endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request

from spik import config
from web.paths import PREFS_FILE

router = APIRouter()

_ALLOWED_PREFS = {"camera", "mic"}


@router.get("/api/config")
def app_config() -> dict:
    """Config visible para el frontend: modo de ejecución y capacidades solo-host.

    ``noise`` (filtro de ruido en vivo) y ``vcam`` (cámara virtual Speak Cam) requieren la
    sesión del host, así que solo están disponibles en modo 'local'.
    """
    return {
        "mode": config.MODE,
        "noise": config.MODE == "local",
        "vcam": config.MODE == "local",
    }


@router.get("/api/prefs")
def get_prefs() -> dict:
    if PREFS_FILE.exists():
        try:
            return {k: v for k, v in json.loads(PREFS_FILE.read_text()).items()
                    if k in _ALLOWED_PREFS}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


@router.post("/api/prefs")
async def set_prefs(request: Request) -> dict:
    body = await request.json()
    prefs = {k: str(v) for k, v in body.items() if k in _ALLOWED_PREFS and v}
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(json.dumps(prefs, indent=2))
    return prefs
