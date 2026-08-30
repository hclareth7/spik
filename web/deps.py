"""Execution-mode gates (local / appliance / server).

Centralizes the ``config.MODE`` checks used by the routers so mode gating lives
in one place. ``config.MODE`` is read at call time (tests monkeypatch it).
"""

from __future__ import annotations

from fastapi import HTTPException

from spik import config

_HOST_SESSION_DETAIL = (
    "El filtro de ruido en vivo solo está disponible en la app local del host."
)


def require(cond: bool, msg: str) -> None:
    """Raise HTTP 400 with ``msg`` unless ``cond`` holds."""
    if not cond:
        raise HTTPException(status_code=400, detail=msg)


def require_local() -> None:
    """Block capture (camera/mic/recording) when no host hardware is available.

    Only 'server' mode (Traefik/spik.hclareth.local) lacks /dev/video* and PipeWire.
    In 'appliance' (privileged container with devices + mounted PipeWire socket)
    capture works, so only 'server' is blocked here.
    """
    if config.MODE == "server":
        raise HTTPException(
            status_code=503,
            detail="No disponible en modo servidor: la captura corre en la app local (127.0.0.1).",
        )


def host_session_available() -> bool:
    """True when the host user session (systemd --user, xdg-open) is available.

    Only the local host app has it; containers (appliance/server) do not.
    """
    return config.MODE == "local"


def require_host_session(detail: str = _HOST_SESSION_DETAIL) -> None:
    """Block features that need the host user session (systemd --user, xdg-open).

    These live on the host, not inside a container, so they only work in 'local'
    mode. ``detail`` lets callers keep their specific error message.
    """
    if not host_session_available():
        raise HTTPException(status_code=503, detail=detail)
