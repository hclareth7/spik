"""Tests for the startup model warmup (web.main lifespan).

The warmup runs in a background daemon thread so it never blocks the server. Here we call the
warmup helpers directly (joining the returned thread for determinism) and drive the FastAPI
lifespan context to assert it yields without blocking or raising.
"""

from __future__ import annotations

import asyncio

from spik import config, model_cache
from web import main


def test_warmup_disabled_returns_no_thread(monkeypatch):
    monkeypatch.setattr(config, "WARMUP", False)
    assert main._start_warmup() is None


def test_warmup_calls_model_cache_warm(monkeypatch):
    monkeypatch.setattr(config, "WARMUP", True)
    monkeypatch.setattr(config, "WHISPER_MODEL", "medium")
    monkeypatch.setattr(config, "asr_threads", lambda: 3)
    calls = []
    monkeypatch.setattr(model_cache, "warm", lambda name, threads, **k: calls.append((name, threads)))

    t = main._start_warmup()
    assert t is not None
    t.join(timeout=5)
    assert calls == [("medium", 3)]


def test_warmup_swallows_errors(monkeypatch):
    monkeypatch.setattr(config, "WARMUP", True)

    def _boom(*a, **k):
        raise RuntimeError("whisperx not installed")

    monkeypatch.setattr(model_cache, "warm", _boom)
    t = main._start_warmup()
    assert t is not None
    t.join(timeout=5)  # must not raise into the caller / crash the thread pool


def test_lifespan_yields_without_blocking(monkeypatch):
    monkeypatch.setattr(config, "WARMUP", True)
    monkeypatch.setattr(model_cache, "warm", lambda *a, **k: None)

    async def _drive():
        async with main._lifespan(main.app):
            return True

    assert asyncio.run(_drive()) is True
