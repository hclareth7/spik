"""Library endpoints: projects, recordings listing, history, and video serving.

Serving/opening a recording is always confined to ``DATA_DIR`` via
:func:`web.validation.safe_video_path` (resolve()+parents containment).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from spik import config, store
from web import deps, state
from web.paths import PROJECTS_FILE
from web.validation import (
    _PROJECT_RE,
    resolve_within_data_dir,
    safe_project_dir,
    safe_video_path,
)

router = APIRouter()


# ============================================================================
# Grabaciones (listado) e historial
# ============================================================================
def _videos_in(folder: Path, project: str) -> list[dict]:
    """Grabaciones (mkv/mp4/mov) directamente dentro de `folder`, etiquetadas con `project`."""
    out = []
    if folder.exists():
        for ext in ("*.mkv", "*.mp4", "*.mov"):
            for p in folder.glob(ext):
                out.append({
                    "path": str(p), "name": p.name, "project": project,
                    "size_mb": round(p.stat().st_size / 1e6, 1),
                })
    return out


@router.get("/api/videos")
def list_videos(project: str | None = None) -> dict:
    """Lista grabaciones para analizar. Sin `project` recorre todos; con él, solo ese proyecto.

    Los archivos sueltos en la raíz de data/ (grabaciones "legacy" previas a los proyectos) se
    tratan como del proyecto 'default'.
    """
    vids: list[dict] = []
    if project is not None:
        deps.require(bool(_PROJECT_RE.match(project)), "proyecto inválido")
        if project == "default":
            vids += _videos_in(config.DATA_DIR, "default")
        vids += _videos_in(config.DATA_DIR / project, project)
    else:
        vids += _videos_in(config.DATA_DIR, "default")  # raíz = default (legacy)
        if config.DATA_DIR.exists():
            for sub in config.DATA_DIR.iterdir():
                if sub.is_dir() and _PROJECT_RE.match(sub.name):
                    vids += _videos_in(sub, sub.name)
    vids.sort(key=lambda v: (v["project"], v["name"]))
    return {"videos": vids}


@router.get("/api/history")
def history(limit: int = 30, project: str | None = None) -> JSONResponse:
    if project is not None:
        deps.require(bool(_PROJECT_RE.match(project)), "proyecto inválido")
    return JSONResponse(store.history(limit=limit, project=project))


# ============================================================================
# Proyectos (agrupan grabaciones) — disponibles en todos los modos
# ============================================================================
def _persisted_projects() -> set[str]:
    if PROJECTS_FILE.exists():
        try:
            data = json.loads(PROJECTS_FILE.read_text())
            return {s for s in data if isinstance(s, str) and _PROJECT_RE.match(s)}
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _all_projects() -> set[str]:
    """Unión de: subcarpetas de data/, proyectos persistidos, y siempre 'default'."""
    slugs = {"default"} | _persisted_projects()
    if config.DATA_DIR.exists():
        for sub in config.DATA_DIR.iterdir():
            if sub.is_dir() and _PROJECT_RE.match(sub.name):
                slugs.add(sub.name)
    return slugs


def _project_count(slug: str) -> int:
    """Nº de grabaciones del proyecto (la raíz de data/ cuenta para 'default')."""
    n = len(_videos_in(config.DATA_DIR / slug, slug))
    if slug == "default":
        n += len(_videos_in(config.DATA_DIR, "default"))
    return n


@router.get("/api/projects")
def list_projects() -> dict:
    projects = [{"slug": s, "count": _project_count(s)} for s in sorted(_all_projects())]
    return {"projects": projects}


@router.post("/api/projects")
def create_project(slug: str = Query(...)) -> dict:
    deps.require(bool(_PROJECT_RE.match(slug)),
                 "nombre de proyecto inválido (usa letras, dígitos, - y _)")
    (config.DATA_DIR / slug).mkdir(parents=True, exist_ok=True)
    # Persistimos el slug para que sobreviva aunque el proyecto quede vacío.
    slugs = _persisted_projects() | {slug}
    PROJECTS_FILE.write_text(json.dumps(sorted(slugs), indent=2))
    return list_projects()


@router.delete("/api/projects")
def delete_project(slug: str = Query(...)) -> dict:
    """Elimina un proyecto VACÍO: su carpeta y el slug persistido. Solo local/appliance.

    'default' (la raíz de data/) nunca se borra. Si el proyecto tiene grabaciones, hay que
    borrarlas primero (protege el historial de borrados masivos accidentales).
    """
    deps.require_local()
    if slug == "default":
        raise HTTPException(status_code=400, detail="no se puede borrar el proyecto por defecto")
    real = safe_project_dir(slug)
    if _videos_in(real, slug):
        raise HTTPException(status_code=400,
                            detail="proyecto no vacío — borra las grabaciones primero")
    # No lo borres si se está grabando dentro de él ahora mismo.
    rec = state.capture.record_path
    if state.capture.is_recording() and rec and Path(rec).resolve().parent == real:
        raise HTTPException(status_code=409, detail="no se puede borrar: grabación en curso")
    try:
        real.rmdir()  # solo si está vacía (defensa extra sobre _videos_in)
    except OSError as e:
        raise HTTPException(status_code=400,
                            detail=f"no se pudo borrar la carpeta: {e}") from e
    slugs = _persisted_projects() - {slug}
    PROJECTS_FILE.write_text(json.dumps(sorted(slugs), indent=2))
    return list_projects()


# ============================================================================
# Servir / abrir grabaciones (siempre dentro de DATA_DIR: anti path-traversal)
# ============================================================================
@router.get("/api/video")
def get_video(path: str = Query(...)) -> FileResponse:
    """Sirve la grabación (con Range para buscar/streamear). Respaldo/descarga en cualquier modo."""
    real = safe_video_path(path)
    return FileResponse(real, headers={"Accept-Ranges": "bytes"})


@router.delete("/api/video")
def delete_video(path: str = Query(...)) -> dict:
    """Elimina una grabación del disco y sus filas de historial. Solo local/appliance.

    Dos casos:
      - Archivo DENTRO de DATA_DIR y presente: se borra del disco (+ su WAV temporal) y se
        purga el historial. Nunca se borra el archivo que se está grabando ni mientras hay un
        análisis en curso (evita dejar un job leyendo un archivo desaparecido).
      - Fila huérfana/legacy: la ruta apunta FUERA de DATA_DIR (p. ej. el viejo montaje
        '/data/…' del contenedor) o el archivo ya no existe. No se toca el disco jamás; solo se
        purga la fila de historial para poder limpiar entradas obsoletas.
    """
    deps.require_local()
    real = resolve_within_data_dir(path)
    if real is None or not real.is_file():
        # Huérfana: fuera de DATA_DIR o sin archivo -> solo purga historial (sin tocar disco).
        removed = store.delete_by_video(path)
        if real is not None and str(real) != path:
            removed += store.delete_by_video(str(real))
        if removed == 0:
            raise HTTPException(status_code=404,
                                detail="no existe ni en disco ni en el historial")
        return {"deleted": False, "path": path, "sessions_removed": removed}
    rec = state.capture.record_path
    if state.capture.is_recording() and rec and Path(rec).resolve() == real:
        raise HTTPException(status_code=409, detail="no se puede borrar: grabación en curso")
    if state.jobs.is_active():
        raise HTTPException(status_code=409, detail="no se puede borrar: análisis en curso")
    real.unlink(missing_ok=True)
    # WAV temporal del análisis, si quedó junto al video (keep_audio / fallo previo).
    real.with_suffix(".wav").unlink(missing_ok=True)
    removed = store.delete_by_video(str(real))
    if str(real) != path:  # el historial pudo guardarse con la ruta sin resolver
        removed += store.delete_by_video(path)
    return {"deleted": True, "path": str(real), "sessions_removed": removed}


@router.post("/api/video/open")
def open_video(path: str = Query(...)) -> dict:
    """Abre la grabación en el reproductor de video por defecto del sistema (solo modo local).

    Requiere sesión de escritorio del host (xdg-open + DISPLAY/entorno XDG), que no existe en un
    contenedor. En appliance/server el frontend usa /api/video (descarga) en su lugar.
    """
    deps.require_host_session(
        detail="Abrir en el reproductor del sistema solo está disponible en la app local; "
               "usa la descarga.",
    )
    real = safe_video_path(path)
    if not shutil.which("xdg-open"):
        raise HTTPException(
            status_code=500,
            detail="Falta 'xdg-open' (paquete xdg-utils). Instálalo o usa la descarga.",
        )
    subprocess.Popen(["xdg-open", str(real)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"opened": True, "path": str(real)}
