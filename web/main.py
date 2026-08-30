"""spik local web GUI — FastAPI backend (app factory).

Goal (this iteration):
  - Input checker: camera preview (MJPEG), camera info, live mic level meter (VU),
    device selection, and the "Speak Clean Mic" noise filter toggle.
  - Record: start/stop reusing capture/record.sh.
  - Feedback: runs the existing pipeline (spik.report) and shows the result + cost.

Privacy ("everything local"):
  - The server listens ONLY on 127.0.0.1 (see __main__ and web/README.md).
  - Preview and the VU meter are local subprocesses; video/audio never leaves the machine.
  - Only text/metrics go to Vertex, exactly like the CLI (same spik.report path).

Subprocess security:
  - shell=True is never used. Device/source names coming from the browser are validated
    with regexes before being passed to ffmpeg/parec/wpctl (prevents command injection).

Run:  uvicorn web.main:app --host 127.0.0.1 --port 8000   (or: python -m web.main)
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from spik import config as spik_config
from web.paths import DIST_DIR, STATIC_DIR
from web.routers import (
    analysis,
    config,
    devices,
    library,
    mic,
    noise,
    preview,
    recording,
    vcam,
)

_log = logging.getLogger("spik")


def _warm_models() -> None:
    """Preload the WhisperX models so the FIRST analysis is fast. Errors are swallowed so a
    missing whisperx/torch (or any load failure) never stops the server from starting."""
    try:
        from spik import model_cache  # noqa: PLC0415 (heavy import, only on warmup)

        model_cache.warm(spik_config.WHISPER_MODEL, spik_config.asr_threads())
    except Exception as e:  # noqa: BLE001 - warmup is best-effort
        _log.warning("model warmup skipped: %s", e)


def _start_warmup() -> threading.Thread | None:
    """Kick off the model warmup in a background daemon thread (non-blocking). Returns the
    thread (or None when SPIK_WARMUP is disabled) so tests can await it deterministically."""
    if not spik_config.WARMUP:
        return None
    t = threading.Thread(target=_warm_models, name="spik-warmup", daemon=True)
    t.start()
    return t


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Startup: schedule (don't await) model warmup so it doesn't block the server accepting
    # requests. The warm cache shares a lock, so a first job racing warmup won't double-load.
    _start_warmup()
    yield


app = FastAPI(title="spik", docs_url=None, redoc_url=None, lifespan=_lifespan)

# Feature routers (each owns its APIRouter and endpoints).
app.include_router(config.router)
app.include_router(devices.router)
app.include_router(preview.router)
app.include_router(mic.router)
app.include_router(noise.router)
app.include_router(vcam.router)
app.include_router(recording.router)
app.include_router(analysis.router)
app.include_router(library.router)


# ============================================================================
# Frontend serving
#
# Primary UI is the built React/Vite app in DIST_DIR (index.html + /assets). The legacy
# plain-static UI in STATIC_DIR is kept for rollback and served only when DIST_DIR is
# absent. Everything is guarded with existence checks so importing this module and
# starting the app never fail if either directory is missing.
# ============================================================================
def _index_file() -> Path:
    """Return the index.html to serve: built frontend first, legacy as fallback."""
    dist_index = DIST_DIR / "index.html"
    if dist_index.is_file():
        return dist_index
    return STATIC_DIR / "index.html"


@app.get("/")
def index() -> FileResponse:
    index_file = _index_file()
    if not index_file.is_file():
        # Neither the built frontend nor the legacy UI is present.
        raise HTTPException(status_code=404, detail="Frontend not built. Run: cd frontend && npm run build")
    return FileResponse(index_file)


@app.get("/favicon.svg")
def favicon() -> FileResponse:
    for candidate in (DIST_DIR / "favicon.svg", STATIC_DIR / "favicon.svg"):
        if candidate.is_file():
            return FileResponse(candidate, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="favicon not found")


# Built frontend assets (JS/CSS/vendored fonts). Referenced as /assets/* by index.html.
if (DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

# Legacy static mount (kept for rollback). Only mounted if the directory still exists.
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def _cache_control(request: Request, call_next):
    resp = await call_next(request)
    # Avoid caching preview/SSE and static assets during development.
    if request.url.path.startswith(("/video", "/api")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


if __name__ == "__main__":
    import shutil

    import uvicorn

    # localhost ONLY: never 0.0.0.0. Video/audio must not be reachable from the network.
    missing = [t for t in ("ffmpeg", "parec", "pactl", "wpctl", "systemctl") if not shutil.which(t)]
    if missing:
        print(f"WARNING: missing tools: {', '.join(missing)}. Some features will not work.")
    uvicorn.run(app, host="127.0.0.1", port=8000)
