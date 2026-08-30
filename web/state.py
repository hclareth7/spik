"""Shared mutable state for the web layer.

Two singletons live here:
  - ``capture``: the single-owner capture session (one ffmpeg opening the real camera
    once and teeing to preview/record/vcam sinks — see :mod:`web.capture_pipeline`).
  - ``jobs``: the in-memory registry of background analysis jobs.

This is deliberately process-local, single-user state; analysis results are also
persisted to SQLite by the pipeline.
"""

from __future__ import annotations

import asyncio
import signal
import tempfile

from web.capture_pipeline import (
    SINK_PREVIEW,
    SINK_RECORD,
    SINK_VCAM,
    build_capture_cmd,
    probe_formats,
)

# JPEG frame markers (Start/End Of Image) used to re-frame the raw MJPEG pipe stream.
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"


class CaptureSession:
    """The single owner of the real camera: one ffmpeg process, one device open.

    Everything that needs the camera (browser preview, recording, and — later — the
    virtual-camera loopback) becomes a *sink* of this one process, built by
    :func:`web.capture_pipeline.build_capture_cmd`. This dissolves the old single-open
    conflict (preview vs recording each opened the device independently).

    The camera device is single-open, so exactly one process may hold it. Client
    disconnect is NOT reliably delivered to a StreamingResponse generator, so teardown
    is explicit (``POST /video/preview/stop`` / ``/api/record/stop``) and every
    ``start`` reaps any previous owner first.
    """

    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.sinks: set[str] = set()
        self.device: str | None = None
        self.record_path: str | None = None
        # Filters currently applied to the vcam/preview branches (Speak Cam); None = raw.
        self.filters: dict | None = None
        # Open stderr log handle, only when a recording sink is active (failure diagnosis).
        self.log: tempfile._TemporaryFileWrapper | None = None
        # --- Live preview fan-out (used while recording; see _drain_preview) ---
        # Latest complete JPEG frame, a list of one-shot broadcast futures, and the server-
        # owned task that drains ffmpeg's MJPEG pipe so a browser disconnect can never stall it.
        self._preview_frame: bytes | None = None
        self._preview_waiters: list[asyncio.Future] = []
        self._drain_task: asyncio.Task | None = None

    def is_recording(self) -> bool:
        """True while the current session is writing a recording file."""
        return self.proc is not None and SINK_RECORD in self.sinks

    def is_vcam(self) -> bool:
        """True while the current session is feeding the virtual camera (Speak Cam)."""
        return self.proc is not None and SINK_VCAM in self.sinks

    async def start(
        self,
        *,
        device: str,
        sinks: set[str],
        audio_source: str | None = None,
        out_path: str | None = None,
        filters: dict | None = None,
        vcam_device: str | None = None,
        max_width: int | None = None,
        fps: int | None = None,
    ) -> asyncio.subprocess.Process:
        """Reap any prior owner, then spawn the single-owner ffmpeg for ``sinks``.

        stdout is a pipe only when a preview sink is present (the MJPEG stream the
        browser reads); recording failures are captured to a temp stderr log so the
        route can report *why* ffmpeg died instead of failing silently.

        ``max_width``/``fps`` cap the virtual-camera geometry/framerate for real-time output
        (see :func:`web.capture_pipeline.build_capture_cmd`); they are ignored by other sinks.
        """
        await self.stop()
        formats = probe_formats(device)
        cmd = build_capture_cmd(
            device=device, sinks=sinks, formats=formats,
            out_path=out_path, audio_source=audio_source,
            filters=filters, vcam_device=vcam_device,
            max_width=max_width, fps=fps,
        )
        stdout = asyncio.subprocess.PIPE if SINK_PREVIEW in sinks else asyncio.subprocess.DEVNULL
        log: tempfile._TemporaryFileWrapper | None = None
        if SINK_RECORD in sinks:
            log = tempfile.NamedTemporaryFile(prefix="spik-cap-", suffix=".log", delete=False)
            stderr = log
        else:
            stderr = asyncio.subprocess.DEVNULL
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=stdout, stderr=stderr)
        self.proc = proc
        self.sinks = set(sinks)
        self.device = device
        self.record_path = out_path
        self.filters = filters
        self.log = log
        # Fan-out preview during recording: a server-owned task drains the MJPEG pipe so a
        # browser connecting/disconnecting can never fill ffmpeg's stdout buffer and stall
        # (ruin) the recording. Created synchronously so the pipe is already being drained
        # during record_start's 2 s liveness window. Only when BOTH sinks are present — a
        # preview-only session is drained by the browser generator (_mjpeg_stream).
        self._preview_frame = None
        if SINK_PREVIEW in sinks and SINK_RECORD in sinks:
            self._drain_task = asyncio.create_task(self._drain_preview(proc))
        return proc

    async def _drain_preview(self, proc: asyncio.subprocess.Process) -> None:
        """Continuously read the preview MJPEG pipe and publish complete JPEG frames.

        This is the invariant that protects the recording: the pipe is ALWAYS drained by the
        server, independent of any browser viewer, so ffmpeg's ~64 KB stdout buffer can never
        fill and block the shared capture process. Runs until ffmpeg closes stdout (EOF) or
        the task is cancelled by :meth:`stop`; a frame-splitting error drops the buffer but
        never stops the reading loop.
        """
        buf = b""
        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                try:
                    while True:
                        soi = buf.find(_JPEG_SOI)
                        eoi = buf.find(_JPEG_EOI, soi + 2)
                        if soi == -1 or eoi == -1:
                            break
                        self._publish_frame(buf[soi : eoi + 2])
                        buf = buf[eoi + 2 :]
                except Exception:
                    buf = b""  # drop a corrupt buffer, but keep draining the pipe
        finally:
            self._publish_frame(None)  # EOF sentinel: release any lingering subscribers

    def _publish_frame(self, frame: bytes | None) -> None:
        """Deliver a frame to all current subscribers (``None`` = end-of-stream sentinel)."""
        if frame is not None:
            self._preview_frame = frame
        waiters = self._preview_waiters
        self._preview_waiters = []
        for fut in waiters:
            if not fut.done():
                fut.set_result(frame)

    async def subscribe_frames(self):
        """Yield the latest JPEG frames from the fan-out buffer until end-of-stream.

        Never touches ffmpeg's pipe (the drain task owns it), so a caller disconnecting only
        drops this subscriber — it can never stall the capture process or the recording.
        """
        if self._preview_frame is not None:
            yield self._preview_frame
        while True:
            if self._drain_task is None or self._drain_task.done():
                return
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            self._preview_waiters.append(fut)
            frame = await fut
            if frame is None:  # EOF sentinel
                return
            yield frame

    async def restart_with(
        self,
        *,
        device: str,
        sinks: set[str],
        audio_source: str | None = None,
        out_path: str | None = None,
        filters: dict | None = None,
        vcam_device: str | None = None,
        max_width: int | None = None,
        fps: int | None = None,
    ) -> asyncio.subprocess.Process:
        """Change the active sink set (or live filters) by restarting the single owner.

        ffmpeg's output topology and filtergraph are static, so adding/removing a sink
        (e.g. starting a recording while previewing) or changing the Speak Cam filters
        means stop + start (~1 s). Still one opener throughout.
        """
        return await self.start(
            device=device, sinks=sinks, audio_source=audio_source, out_path=out_path,
            filters=filters, vcam_device=vcam_device, max_width=max_width, fps=fps,
        )

    async def stop(self) -> asyncio.subprocess.Process | None:
        """Terminate the current session, if any. Idempotent.

        A recording session is stopped with SIGINT so ffmpeg finalizes the MKV container
        cleanly; otherwise a plain terminate is enough. The stderr log handle is closed
        but the file is left on disk for the caller to read (recording failure detail);
        the caller is responsible for unlinking it.
        """
        proc = self.proc
        log = self.log
        drain = self._drain_task
        was_recording = self.is_recording()
        self.proc = None
        self.sinks = set()
        self.device = None
        self.filters = None
        self.log = None
        self._drain_task = None
        # Stop the fan-out drain and release any preview subscribers (EOF sentinel). Use
        # gather(return_exceptions=True) so the task's own CancelledError is captured (not
        # re-raised here) while a cancellation of *this* coroutine still propagates.
        if drain is not None and not drain.done():
            drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)
        self._publish_frame(None)
        if proc is not None and proc.returncode is None:
            if was_recording:
                proc.send_signal(signal.SIGINT)
                timeout = 10.0
            else:
                proc.terminate()
                timeout = 2.0
            # A preview ffmpeg wedges on a full stdout pipe once its reader (the browser)
            # disconnects: it catches SIGTERM but the auto-restarted write() never reaches
            # the exit check, so only SIGKILL frees the camera. Teardown often runs *inside*
            # a task being cancelled by that same disconnect, so shield the wait (so the
            # cancellation doesn't abort it) and force-kill on either timeout OR cancel —
            # otherwise proc.kill() is skipped and the device stays held forever.
            try:
                await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
            except asyncio.CancelledError:
                proc.kill()
                raise
        if log is not None:
            log.close()
        return proc


class JobRegistry:
    """In-memory registry of background analysis jobs.

    A long analysis (2-3 h audio => 30-90 min) cannot live in a synchronous HTTP
    request; ``POST /api/analyze`` creates a job here and returns immediately,
    while progress is streamed over SSE.
    """

    # Per-stage weights, used to turn (stage, fraction) into a monotonic global %.
    STAGE_WEIGHTS = [
        ("extract", 0.05), ("split", 0.05), ("transcribe", 0.70),
        ("metrics", 0.02), ("feedback", 0.16), ("save", 0.02),
    ]

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}

    def global_pct(self, stage: str, frac: float) -> float:
        """Map (stage, stage-fraction) to a global percentage 0..100."""
        acc = 0.0
        for name, weight in self.STAGE_WEIGHTS:
            if name == stage:
                return round((acc + weight * max(0.0, min(1.0, frac))) * 100, 1)
            acc += weight
        return round(acc * 100, 1)

    def is_active(self) -> bool:
        """True while any job is still running."""
        return any(j["status"] == "running" for j in self.jobs.values())


# Process-local singletons shared across routers.
capture = CaptureSession()
jobs = JobRegistry()
