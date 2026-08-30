"""Tests for camera-preview teardown via the single-owner capture session.

The preview holds the camera open via the shared ``state.capture`` ffmpeg subprocess.
Because a StreamingResponse does not reliably observe the client disconnect, teardown must
be explicit: CaptureSession.stop terminates the process and POST /video/preview/stop drives
it. These tests use a fake process (no real ffmpeg/camera) and drive the coroutines with
asyncio.run (no pytest-asyncio dep).
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from spik import config
from web import state
from web.routers.preview import preview, preview_stop
from web.state import _JPEG_EOI, _JPEG_SOI


def _jpeg(payload: bytes) -> bytes:
    """A minimal complete JPEG blob (SOI … EOI) for the frame-splitter tests."""
    return _JPEG_SOI + payload + _JPEG_EOI


class _FakeStdout:
    """Scripted stand-in for ``proc.stdout``: returns chunks, then EOF or blocks forever."""

    def __init__(self, chunks: list[bytes], block_after: bool = False) -> None:
        self._chunks = list(chunks)
        self._block_after = block_after
        self._blocked = asyncio.Event()  # never set: simulates a live pipe with no more data

    async def read(self, _n: int = -1) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        if self._block_after:
            await self._blocked.wait()  # stays alive until the drain task is cancelled
        return b""  # EOF


class _FakeProc:
    """Minimal stand-in for asyncio.subprocess.Process: alive until terminated."""

    def __init__(self, stdout_chunks: list[bytes] | None = None,
                 block_after: bool = False) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.signalled: int | None = None
        self.stdout = _FakeStdout(stdout_chunks or [], block_after)

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def send_signal(self, sig: int) -> None:
        self.signalled = sig
        self.returncode = -int(sig)

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


@pytest.fixture(autouse=True)
def _reset_capture():
    def _reset() -> None:
        state.capture.proc = None
        state.capture.sinks = set()
        state.capture.log = None
        state.capture.device = None
        state.capture.filters = None
        state.capture._drain_task = None
        state.capture._preview_frame = None
        state.capture._preview_waiters = []

    _reset()
    yield
    _reset()


async def _consume_one(cap) -> bytes | None:
    """Subscribe and return the first frame (or None if the stream ends first)."""
    async for frame in cap.subscribe_frames():
        return frame
    return None


def test_stop_terminates_and_clears():
    proc = _FakeProc()
    state.capture.proc = proc
    asyncio.run(state.capture.stop())
    assert proc.terminated
    assert state.capture.proc is None


def test_stop_is_idempotent_when_nothing_running():
    # No process registered: stop() must not raise.
    asyncio.run(state.capture.stop())
    assert state.capture.proc is None


def test_stop_endpoint_local_stops_preview(monkeypatch):
    monkeypatch.setattr(config, "MODE", "local")
    proc = _FakeProc()
    state.capture.proc = proc
    res = asyncio.run(preview_stop())
    assert res == {"stopped": True}
    assert proc.terminated
    assert state.capture.proc is None


def test_stop_endpoint_does_not_kill_active_recording(monkeypatch):
    monkeypatch.setattr(config, "MODE", "local")
    proc = _FakeProc()
    state.capture.proc = proc
    state.capture.sinks = {"record"}  # a recording is in progress
    res = asyncio.run(preview_stop())
    assert res == {"stopped": True}
    # The recording must survive a preview-stop.
    assert state.capture.proc is proc
    assert not proc.terminated


def test_stop_endpoint_blocked_in_server_mode(monkeypatch):
    monkeypatch.setattr(config, "MODE", "server")
    with pytest.raises(HTTPException) as e:
        asyncio.run(preview_stop())
    assert e.value.status_code == 503


def test_stop_force_kills_wedged_proc_when_cancelled():
    """Regression: a preview ffmpeg wedges on a full stdout pipe once the browser
    disconnects — it catches SIGTERM but never exits, so only SIGKILL frees the camera.
    Teardown frequently runs inside the task the *same* disconnect is cancelling; stop()
    must still force-kill (not skip proc.kill()) so the device is released.
    """

    class _WedgedProc(_FakeProc):
        """SIGTERM is caught but ignored (pipe stall); only kill() ends wait()."""

        def __init__(self) -> None:
            super().__init__()
            self._done = asyncio.Event()

        def terminate(self) -> None:
            self.terminated = True  # caught but ignored — process stays alive

        def kill(self) -> None:
            super().kill()
            self._done.set()

        async def wait(self) -> int:
            await self._done.wait()
            return self.returncode if self.returncode is not None else 0

    async def scenario():
        proc = _WedgedProc()
        state.capture.proc = proc
        task = asyncio.ensure_future(state.capture.stop())
        await asyncio.sleep(0)  # let stop() reach the wait on the wedged proc
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return proc

    proc = asyncio.run(scenario())
    assert proc.terminated  # SIGTERM was attempted first
    assert proc.killed  # …then SIGKILL fired despite cancellation
    assert state.capture.proc is None


# ---------------------------------------------------------------------------
# Live preview fan-out during recording (record + preview share one ffmpeg)
# ---------------------------------------------------------------------------
def test_drain_splits_frames_across_chunks_and_keeps_latest():
    """_drain_preview reassembles JPEGs (even split across reads) and keeps the newest."""

    async def scenario():
        # Frame A arrives split across two reads; frame B whole; then EOF.
        a = _jpeg(b"AAA")
        b = _jpeg(b"BBB")
        proc = _FakeProc(stdout_chunks=[a[:3], a[3:], b, b""])
        await state.capture._drain_preview(proc)
        return state.capture._preview_frame

    assert asyncio.run(scenario()) == _jpeg(b"BBB")


def test_subscriber_receives_published_frame():
    """A fan-out subscriber gets the frame the drain task publishes (browser preview path)."""

    async def scenario():
        proc = _FakeProc(stdout_chunks=[_jpeg(b"AAA")], block_after=True)
        state.capture.proc = proc
        state.capture.sinks = {"record", "preview"}
        state.capture._drain_task = asyncio.create_task(state.capture._drain_preview(proc))
        got = await asyncio.wait_for(_consume_one(state.capture), timeout=1.0)
        state.capture._drain_task.cancel()
        await asyncio.gather(state.capture._drain_task, return_exceptions=True)
        return got

    assert asyncio.run(scenario()) == _jpeg(b"AAA")


def test_subscriber_cancel_never_stalls_drain_or_touches_proc():
    """The core hazard invariant: a browser disconnecting (subscriber cancelled) must not
    stop the drain task or terminate/kill the shared capture process (which would ruin the
    recording)."""

    async def scenario():
        proc = _FakeProc(block_after=True)  # live pipe, no frames yet
        state.capture.proc = proc
        state.capture.sinks = {"record", "preview"}
        state.capture._drain_task = asyncio.create_task(state.capture._drain_preview(proc))
        sub = asyncio.ensure_future(_consume_one(state.capture))
        await asyncio.sleep(0)  # let the subscriber register a waiter
        sub.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sub
        alive = not state.capture._drain_task.done()
        state.capture._drain_task.cancel()
        await asyncio.gather(state.capture._drain_task, return_exceptions=True)
        return alive, proc

    alive, proc = asyncio.run(scenario())
    assert alive  # drain kept running after the subscriber was cancelled
    assert state.capture.proc is proc  # process untouched
    assert not proc.terminated and not proc.killed


def test_stop_during_recording_cancels_drain_sigints_and_releases_subscribers():
    """stop() on a recording cancels the drain, SIGINTs ffmpeg (clean MKV), and wakes any
    fan-out subscriber with the end-of-stream sentinel instead of leaving it hung."""

    async def scenario():
        proc = _FakeProc(block_after=True)
        state.capture.proc = proc
        state.capture.sinks = {"record"}  # is_recording() -> SIGINT path
        state.capture._drain_task = asyncio.create_task(state.capture._drain_preview(proc))
        sub = asyncio.ensure_future(_consume_one(state.capture))
        await asyncio.sleep(0)  # subscriber registers a waiter
        await state.capture.stop()
        released = await asyncio.wait_for(sub, timeout=1.0)  # must not hang
        return released, proc

    released, proc = asyncio.run(scenario())
    assert released is None  # sentinel ended the stream (no frame delivered)
    assert proc.signalled == signal.SIGINT
    assert state.capture.proc is None


def test_preview_route_uses_fanout_while_recording_without_reopening_camera(monkeypatch):
    """While recording, /video/preview.mjpeg serves the fan-out and never calls start()
    (opening the single-open camera twice would fail / kill the recording)."""
    monkeypatch.setattr(config, "MODE", "local")
    monkeypatch.setattr(Path, "exists", lambda self: True)  # device presence check
    proc = _FakeProc(block_after=True)
    state.capture.proc = proc
    state.capture.sinks = {"record", "preview"}

    started = {"called": False}

    async def _fail_start(*_a, **_k):
        started["called"] = True

    monkeypatch.setattr(state.capture, "start", _fail_start)

    resp = asyncio.run(preview(device="/dev/video4"))
    assert isinstance(resp, StreamingResponse)
    assert started["called"] is False
