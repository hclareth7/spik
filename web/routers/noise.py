"""Live noise filter ("Speak Clean Mic") — host-only (systemd --user)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from web import deps
from web.paths import NOISE_UNIT
from web.utils import run

router = APIRouter()


def _noise_active() -> bool:
    return run(["systemctl", "--user", "is-active", NOISE_UNIT]).stdout.strip() == "active"


def _clean_mic_source_id() -> str | None:
    for line in run(["pactl", "list", "sources", "short"]).stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] == "speak_clean_mic":
            return parts[0]
    return None


def _default_source_name() -> str:
    return run(["pactl", "get-default-source"]).stdout.strip()


@router.get("/api/noise/status")
def noise_status() -> dict:
    # El filtro en vivo solo existe en la app local (systemd --user). En server/appliance no
    # está disponible: devolvemos todo en falso sin tocar systemctl (que no existe allí).
    if not deps.host_session_available():
        return {"active": False, "available": False, "is_default": False}
    active = _noise_active()
    return {
        "active": active,
        "available": _clean_mic_source_id() is not None,
        "is_default": _default_source_name() == "speak_clean_mic",
    }


@router.post("/api/noise/toggle")
def noise_toggle(on: bool = Query(...)) -> dict:
    deps.require_host_session()
    action = "start" if on else "stop"
    proc = run(["systemctl", "--user", action, NOISE_UNIT], timeout=15.0)
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo {action} el filtro. ¿Corriste noise/install.sh? Detalle: {proc.stderr.strip()}",
        )
    return {"active": _noise_active()}


@router.post("/api/noise/set-default")
def noise_set_default() -> dict:
    deps.require_host_session()
    deps.require(_clean_mic_source_id() is not None,
                 "La fuente 'Speak Clean Mic' no existe. Prende el filtro primero.")
    # Usamos pactl con el NOMBRE de la fuente. `wpctl set-default` espera el node ID de
    # WirePlumber, que NO coincide con el índice de fuente de pactl/PipeWire (por eso antes
    # daba "Node 'NNNN' not found"). El nombre es estable y sin ambigüedad.
    proc = run(["pactl", "set-default-source", "speak_clean_mic"])
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail=f"pactl falló: {proc.stderr.strip()}")
    return {"is_default": _default_source_name() == "speak_clean_mic"}
