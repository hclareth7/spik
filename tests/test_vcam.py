"""Tests for the Speak Cam virtual-camera endpoints (web/routers/vcam.py).

Mirrors the noise/preview test style: routes are plain functions read config.MODE at call
time, so we monkeypatch it and drive the coroutines with asyncio.run (no pytest-asyncio).
The impure boundaries of CaptureSession (probe + subprocess spawn) are stubbed so no ffmpeg
runs and no camera/loopback is opened.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from spik import config
from web import state
from web.capture_pipeline import build_filter_chain
from web.routers import vcam
from web.routers.vcam import vcam_set_filters, vcam_start, vcam_status, vcam_stop


class _FakeAliveProc:
    """Still-running process: wait() times out => start treats it as OK."""
    returncode = None

    async def wait(self):
        raise asyncio.TimeoutError

    def terminate(self): pass
    def kill(self): pass
    def send_signal(self, sig): pass


@pytest.fixture(autouse=True)
def _reset_capture():
    def _clear():
        state.capture.proc = None
        state.capture.sinks = set()
        state.capture.device = None
        state.capture.filters = None
        state.capture.log = None
    _clear()
    yield
    _clear()


# Full neutral filter kwargs. Calling the endpoints directly (not via FastAPI) leaves any
# un-passed Query() param as a sentinel object, so tests that reach filter validation must
# pass every value — same pattern as record_start's tests.
_NEUTRAL_KW = {
    "brightness": 0.0, "contrast": 1.0, "gamma": 1.0,
    "saturation": 1.0, "sharpness": 0.0, "denoise": "off",
}


def _patch_capture(monkeypatch, proc, captured):
    """Stub probe + subprocess spawn so vcam_start/set_filters never launch ffmpeg."""
    monkeypatch.setattr(state, "probe_formats", lambda dev: "mjpeg : 1280x720")

    async def _fake_exec(*cmd, **k):
        captured["cmd"] = list(cmd)
        return proc

    monkeypatch.setattr(state.asyncio, "create_subprocess_exec", _fake_exec)


# ---------------------------------------------------------------------------
# Off-host: status soft-returns without touching the filesystem; mutations 503
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["server", "appliance"])
def test_status_short_circuits_off_host(monkeypatch, mode):
    monkeypatch.setattr(config, "MODE", mode)
    res = vcam_status()
    assert res["active"] is False and res["available"] is False


@pytest.mark.parametrize("mode", ["server", "appliance"])
def test_start_blocked_off_host(monkeypatch, mode):
    monkeypatch.setattr(config, "MODE", mode)
    with pytest.raises(HTTPException) as e:
        asyncio.run(vcam_start(source="/dev/video4"))
    assert e.value.status_code == 503


@pytest.mark.parametrize("mode", ["server", "appliance"])
def test_stop_blocked_off_host(monkeypatch, mode):
    monkeypatch.setattr(config, "MODE", mode)
    with pytest.raises(HTTPException) as e:
        asyncio.run(vcam_stop())
    assert e.value.status_code == 503


@pytest.mark.parametrize("mode", ["server", "appliance"])
def test_set_filters_blocked_off_host(monkeypatch, mode):
    monkeypatch.setattr(config, "MODE", mode)
    with pytest.raises(HTTPException) as e:
        asyncio.run(vcam_set_filters())
    assert e.value.status_code == 503


# ---------------------------------------------------------------------------
# Anti-feedback + provisioning guards (local mode)
# ---------------------------------------------------------------------------
def test_start_rejects_loopback_as_source(monkeypatch):
    monkeypatch.setattr(config, "MODE", "local")
    with pytest.raises(HTTPException) as e:
        asyncio.run(vcam_start(source=config.VCAM_DEVICE))
    assert e.value.status_code == 400


def test_start_rejects_bad_source(monkeypatch):
    monkeypatch.setattr(config, "MODE", "local")
    with pytest.raises(HTTPException) as e:
        asyncio.run(vcam_start(source="/etc/passwd"))
    assert e.value.status_code == 400


def test_start_reports_missing_loopback(monkeypatch):
    monkeypatch.setattr(config, "MODE", "local")
    # Source exists but the loopback device is not provisioned.
    monkeypatch.setattr(vcam.Path, "exists", lambda self: str(self) != config.VCAM_DEVICE)
    monkeypatch.setattr(vcam, "_vcam_available", lambda: False)
    with pytest.raises(HTTPException) as e:
        asyncio.run(vcam_start(source="/dev/video4"))
    assert e.value.status_code == 400
    assert "install.sh" in e.value.detail


# ---------------------------------------------------------------------------
# Filter validation (local mode) — out-of-range / unknown preset -> 400
# ---------------------------------------------------------------------------
def _prime_running_vcam():
    """Pretend a Speak Cam session is live so set-filters passes its is_vcam() gate."""
    state.capture.proc = _FakeAliveProc()
    state.capture.sinks = {"vcam"}
    state.capture.device = "/dev/video4"


def test_set_filters_rejects_out_of_range(monkeypatch):
    monkeypatch.setattr(config, "MODE", "local")
    _prime_running_vcam()
    with pytest.raises(HTTPException) as e:
        asyncio.run(vcam_set_filters(**{**_NEUTRAL_KW, "brightness": 5.0}))  # range -0.3..0.3
    assert e.value.status_code == 400


def test_set_filters_rejects_bad_denoise(monkeypatch):
    monkeypatch.setattr(config, "MODE", "local")
    _prime_running_vcam()
    with pytest.raises(HTTPException) as e:
        asyncio.run(vcam_set_filters(**{**_NEUTRAL_KW, "denoise": "nuke"}))
    assert e.value.status_code == 400


def test_set_filters_requires_running_vcam(monkeypatch):
    monkeypatch.setattr(config, "MODE", "local")
    # No session running.
    with pytest.raises(HTTPException) as e:
        asyncio.run(vcam_set_filters())
    assert e.value.status_code == 400


# ---------------------------------------------------------------------------
# Happy path (local mode) — start feeds the loopback with the filter chain
# ---------------------------------------------------------------------------
def test_start_feeds_loopback_with_filters(monkeypatch):
    monkeypatch.setattr(config, "MODE", "local")
    monkeypatch.setattr(vcam, "_vcam_available", lambda: True)
    monkeypatch.setattr(vcam.Path, "exists", lambda self: True)
    captured = {}
    _patch_capture(monkeypatch, _FakeAliveProc(), captured)

    res = asyncio.run(vcam_start(
        **{**_NEUTRAL_KW, "source": "/dev/video4", "brightness": 0.1, "denoise": "light"}))
    assert res["active"] is True
    assert res["device"] == config.VCAM_DEVICE
    # The built command writes the filtered branch to the loopback device.
    assert config.VCAM_DEVICE in captured["cmd"]
    assert "hqdn3d=2:1.5:3:3" in " ".join(captured["cmd"])
    assert state.capture.is_vcam()


def test_set_filters_maps_to_chain():
    # Constructor-level: a filter dict becomes the expected -vf string.
    chain = build_filter_chain({"brightness": 0.2, "denoise": "light", "sharpness": 0.5})
    assert chain.startswith("hqdn3d=2:1.5:3:3,eq=brightness=0.2")
    assert "unsharp=5:5:0.5" in chain and chain.endswith("format=yuv420p")
