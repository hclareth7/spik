"""Camera preview as an MJPEG multipart stream.

The preview is one sink of the single-owner capture session (:mod:`web.state`), so it
shares the one ffmpeg that opens the real camera — it no longer opens the device itself.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from web import deps, state
from web.capture_pipeline import SINK_PREVIEW
from web.validation import _VIDEO_DEV_RE

router = APIRouter()

_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"
_BOUNDARY = b"speakframe"


async def _mjpeg_stream(device: str):
    """Yield a multipart/x-mixed-replace stream of JPEG frames from the capture session.

    Starts (or, if already running, replaces) the single-owner ffmpeg with a preview sink
    and reads its MJPEG stdout, re-framing each complete JPEG (SOI…EOI) into a multipart
    part. Teardown is via ``state.capture`` so the device is reliably released even though
    a StreamingResponse does not reliably observe the client disconnect.
    """
    proc = await state.capture.start(device=device, sinks={SINK_PREVIEW})
    buf = b""
    try:
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            # Extract each complete JPEG (SOI … EOI) and emit it as a multipart part.
            while True:
                soi = buf.find(_JPEG_SOI)
                eoi = buf.find(_JPEG_EOI, soi + 2)
                if soi == -1 or eoi == -1:
                    break
                frame = buf[soi : eoi + 2]
                buf = buf[eoi + 2 :]
                yield (
                    b"--" + _BOUNDARY + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                    + frame + b"\r\n"
                )
    finally:
        # Only tear down if we are still the active owner (a newer preview may have
        # replaced us via start()'s reap).
        if state.capture.proc is proc:
            await state.capture.stop()


async def _fanout_stream():
    """Stream the recording's live preview from the server-owned fan-out buffer.

    Used ONLY while a recording is in progress: it reads the latest JPEG frames that the
    capture session is already draining (``subscribe_frames``), so this generator never
    touches ffmpeg's pipe. Its teardown does nothing to the capture session — a browser
    disconnect must never be able to stop an in-progress recording.
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
    # While recording, the single-owner ffmpeg already tees a preview branch that the server
    # drains into a fan-out buffer. Serve from that instead of opening the camera a second time
    # (it is single-open) — and this request's teardown never touches the recording.
    if state.capture.is_recording():
        return StreamingResponse(
            _fanout_stream(),
            media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY.decode()}",
        )
    return StreamingResponse(
        _mjpeg_stream(device),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY.decode()}",
    )


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
