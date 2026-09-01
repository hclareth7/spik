"""Camera preview as an MJPEG multipart stream.

The preview is one sink of the single-owner capture session (:mod:`web.state`), so it
shares the one ffmpeg that opens the real camera — it no longer opens the device itself.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import Response, StreamingResponse

from web import deps, state
from web.validation import _VIDEO_DEV_RE

router = APIRouter()

_BOUNDARY = b"speakframe"

# How long GET /video/snapshot.jpg waits for the first frame after starting a fresh preview
# (ffmpeg open + first MJPEG frame), polled in 50 ms steps.
_SNAPSHOT_WAIT_STEPS = 40
_SNAPSHOT_WAIT_STEP_S = 0.05


async def _fanout_stream():
    """Yield a multipart/x-mixed-replace stream from the server-owned fan-out buffer.

    Every complete JPEG frame is published by the capture session's single drain task
    (``subscribe_frames``); this generator never touches ffmpeg's pipe, so any number of
    clients can watch at once and a browser disconnect can never stall the capture (or an
    in-progress recording). Teardown of the camera is explicit (POST /video/preview/stop),
    since a StreamingResponse does not reliably observe the client disconnect.
    """
    async for frame in state.capture.subscribe_frames():
        yield (
            b"--" + _BOUNDARY + b"\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
            + frame + b"\r\n"
        )


@router.get("/video/preview.mjpeg")
async def preview(device: str = Query("/dev/video4")):
    deps.require_local()
    deps.require(bool(_VIDEO_DEV_RE.match(device)), "invalid video device")
    deps.require(Path(device).exists(), f"{device} does not exist")
    # A recording already tees a drained preview branch; otherwise start a shared preview-only
    # capture (idempotent — never opens the single-open camera twice). Both cases then stream
    # from the same fan-out buffer.
    if not state.capture.is_recording():
        await state.capture.ensure_preview(device=device)
    return StreamingResponse(
        _fanout_stream(),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY.decode()}",
    )


@router.get("/video/snapshot.jpg")
async def snapshot(device: str = Query("/dev/video4")):
    """Return the latest single preview JPEG frame.

    The desktop shell (WebKitGTK) cannot render an infinite multipart/x-mixed-replace stream
    via streaming fetch, so its shim polls this finite endpoint ~15×/s instead. Reads the same
    fan-out buffer the multipart stream uses; starts a shared preview capture on demand when
    not already previewing/recording, then waits briefly for the first frame.
    """
    deps.require_local()
    deps.require(bool(_VIDEO_DEV_RE.match(device)), "invalid video device")
    deps.require(Path(device).exists(), f"{device} does not exist")
    if not state.capture.is_recording():
        await state.capture.ensure_preview(device=device)
    frame = state.capture.latest_preview_frame()
    for _ in range(_SNAPSHOT_WAIT_STEPS):
        if frame is not None:
            break
        await asyncio.sleep(_SNAPSHOT_WAIT_STEP_S)
        frame = state.capture.latest_preview_frame()
    deps.require(frame is not None, "preview frame not available yet", status=503)
    return Response(content=frame, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.post("/video/preview/stop")
async def preview_stop():
    """Terminate the current camera preview and release the device.

    The frontend calls this when the user stops the preview or leaves the Checker tab.
    It is the reliable teardown path: a StreamingResponse does not reliably observe the
    client disconnect, so ffmpeg (and the camera) would otherwise linger.
    """
    deps.require_local()
    # Don't tear down an in-progress recording that also feeds a preview sink.
    if not state.capture.is_recording():
        await state.capture.stop()
    return {"stopped": True}
