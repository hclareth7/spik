"""Validation of values coming from the browser and safe path resolution.

Device/source/project strings are matched against strict regexes before ever
reaching ffmpeg/parec/wpctl (defense against command injection). Served/opened
recordings are confined to ``DATA_DIR`` with a resolve()+parents containment
check (never a string-prefix check).
"""

from __future__ import annotations

from pathlib import Path

import re

from fastapi import HTTPException

from spik import config

# --- Input regexes ---
_VIDEO_DEV_RE = re.compile(r"^/dev/video\d+$")
_SOURCE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")   # PipeWire/Pulse source names
_PROJECT_RE = re.compile(r"^[A-Za-z0-9_-]+$")    # project slug (subfolder under data/)

# Recording containers we serve/open.
VIDEO_EXTS = {".mkv", ".mp4", ".mov"}


def safe_video_path(path: str) -> Path:
    """Resolve ``path`` and ensure it is INSIDE ``DATA_DIR`` and an allowed container.

    Both sides are resolved with ``.resolve()`` (neutralizes ``..``, symlinks and
    absolute paths) and containment is checked via ``parents`` — never by string prefix.
    """
    data_root = config.DATA_DIR.resolve()
    real = Path(path).resolve()
    if real != data_root and data_root not in real.parents:
        raise HTTPException(status_code=403, detail="ruta fuera de DATA_DIR")
    if not real.is_file():
        raise HTTPException(status_code=404, detail="no existe")
    if real.suffix.lower() not in VIDEO_EXTS:
        raise HTTPException(status_code=400, detail="tipo de archivo no permitido")
    return real


def resolve_within_data_dir(path: str) -> Path | None:
    """Resolve ``path`` and return it only if contained in ``DATA_DIR``; else ``None``.

    Unlike :func:`safe_video_path`, this does NOT require the file to exist — the delete
    endpoint uses it to tolerate a history row whose file was already removed. It returns
    ``None`` for anything OUTSIDE ``DATA_DIR`` (legacy/orphan rows such as the old container
    ``/data/…`` mount), so the caller can purge that history row WITHOUT ever touching the
    filesystem. Containment is checked via ``parents`` (resolve() neutralizes ``..``/symlinks),
    never a string prefix.
    """
    data_root = config.DATA_DIR.resolve()
    real = Path(path).resolve()
    if real != data_root and data_root not in real.parents:
        return None
    return real


def safe_project_dir(slug: str) -> Path:
    """Resolve a project subfolder INSIDE ``DATA_DIR`` (never the root), for deletion.

    Same defense as :func:`safe_video_path`: the slug is regex-validated (which already
    forbids ``/`` and ``..``) and the resolved directory must be contained in ``DATA_DIR``
    (checked via ``parents``, never a string prefix) and must be a real subfolder — never the
    root itself (deleting the data root is not a project deletion).
    """
    if not _PROJECT_RE.match(slug):
        raise HTTPException(status_code=400, detail="proyecto inválido")
    data_root = config.DATA_DIR.resolve()
    real = (config.DATA_DIR / slug).resolve()
    if real == data_root or data_root not in real.parents:
        raise HTTPException(status_code=403, detail="ruta fuera de DATA_DIR")
    if not real.is_dir():
        raise HTTPException(status_code=404, detail="el proyecto no existe")
    return real
