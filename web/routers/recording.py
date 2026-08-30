"""Recording endpoints backed by the single-owner capture session.

Recording is one sink of ``state.capture`` (the one ffmpeg that opens the real camera).
A recording runs with BOTH the record and preview sinks so the Record tab can show the live
camera while writing the MKV; the preview MJPEG pipe is drained server-side
(``CaptureSession._drain_preview``) into a fan-out buffer, so a browser viewer connecting or
disconnecting can never stall ffmpeg and corrupt the recording.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from spik import config
from web import deps, state
from web.capture_pipeline import SINK_PREVIEW, SINK_RECORD
from web.utils import read_tail
from web.validation import _PROJECT_RE, _SOURCE_RE, _VIDEO_DEV_RE

router = APIRouter()


@router.post("/api/record/start")
async def record_start(
    audio_source: str = Query(...),
    video_device: str = Query("/dev/video4"),
    name: str = Query("practica"),
    project: str = Query("default"),
) -> dict:
    # Synchronous gates first: mode/validation must raise before any await.
    deps.require_local()
    deps.require(not state.capture.is_recording(), "A recording is already in progress.")
    deps.require(bool(_SOURCE_RE.match(audio_source)), "invalid audio source")
    deps.require(bool(_VIDEO_DEV_RE.match(video_device)), "invalid video device")
    deps.require(bool(_PROJECT_RE.match(name)), "invalid file name")
    deps.require(bool(_PROJECT_RE.match(project)), "invalid project")

    out_dir = config.DATA_DIR / project
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.mkv"
    n = 1
    while out.exists():
        out = out_dir / f"{name}-{n:02d}.mkv"
        n += 1

    # Spawn the single-owner ffmpeg with record + preview sinks: the recording writes the MKV
    # while a fan-out preview branch lets the Record tab show the live camera. The preview pipe
    # is drained by the server (state.capture._drain_preview), never by the browser, so a viewer
    # disconnecting can never stall ffmpeg and ruin the recording.
    proc = await state.capture.start(
        device=video_device, sinks={SINK_RECORD, SINK_PREVIEW},
        audio_source=audio_source, out_path=str(out),
    )
    # Give ffmpeg a moment to open the camera/source. If it dies immediately (device busy,
    # bad audio source, …) that is a failure — report it now instead of failing silently.
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass  # still alive => recording correctly
    else:
        log = state.capture.log
        log_name = log.name if log is not None else None
        detail = read_tail(log_name)
        await state.capture.stop()
        if log_name:
            Path(log_name).unlink(missing_ok=True)
        out.unlink(missing_ok=True)  # ffmpeg may have left a 0-byte container
        raise HTTPException(
            status_code=500,
            detail=f"Could not start recording (is the camera in use by another app?). "
                   f"Detail: {detail}",
        )

    return {"recording": True, "path": str(out)}


@router.post("/api/record/stop")
async def record_stop() -> dict:
    deps.require(state.capture.is_recording(), "No recording in progress.")
    path = state.capture.record_path
    log = state.capture.log
    log_name = log.name if log is not None else None

    await state.capture.stop()  # SIGINT => ffmpeg finalizes the MKV cleanly

    # Verify the recording produced a real file; otherwise report ffmpeg's error instead of
    # returning success empty-handed.
    detail = read_tail(log_name)
    if log_name:
        Path(log_name).unlink(missing_ok=True)
    out = Path(path) if path else None
    produced = out is not None and out.is_file() and out.stat().st_size > 1024
    if not produced:
        if out is not None:
            out.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=f"The recording produced no file. Detail: {detail}",
        )
    return {"recording": False, "path": path}


@router.get("/api/record/status")
def record_status() -> dict:
    return {"recording": state.capture.is_recording(), "path": state.capture.record_path}
