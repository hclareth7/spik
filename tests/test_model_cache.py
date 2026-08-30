"""Tests for the warm in-process model cache (no real whisperx/torch).

A fake `whisperx` module is injected into ``sys.modules`` so the lazy ``import whisperx``
inside model_cache resolves to counters we can assert on. The cache is reset before each test.
"""

from __future__ import annotations

import sys
import threading
import types

import pytest

from spik import model_cache


@pytest.fixture
def fake_whisperx(monkeypatch):
    """Inject a fake whisperx module with load counters; reset the cache around the test."""
    mod = types.ModuleType("whisperx")
    mod.asr_loads = 0
    mod.align_loads = []  # one entry per load, with the language code

    def load_model(name, device, compute_type=None, threads=None):
        mod.asr_loads += 1
        return {"name": name, "threads": threads}

    def load_align_model(language_code, device):
        mod.align_loads.append(language_code)
        return (f"align:{language_code}", {"lang": language_code})

    mod.load_model = load_model
    mod.load_align_model = load_align_model

    monkeypatch.setitem(sys.modules, "whisperx", mod)
    model_cache.reset()
    yield mod
    model_cache.reset()


def test_asr_model_loaded_once(fake_whisperx):
    a = model_cache.get_asr_model("medium", 4)
    b = model_cache.get_asr_model("medium", 4)
    assert a is b
    assert fake_whisperx.asr_loads == 1


def test_asr_model_keyed_by_name_and_threads(fake_whisperx):
    model_cache.get_asr_model("medium", 4)
    model_cache.get_asr_model("medium", 8)  # different threads -> separate load
    model_cache.get_asr_model("small", 4)   # different name -> separate load
    assert fake_whisperx.asr_loads == 3


def test_align_model_cached_per_language(fake_whisperx):
    model_cache.get_align_model("es")
    model_cache.get_align_model("es")
    model_cache.get_align_model("en")
    assert fake_whisperx.align_loads == ["es", "en"]


def test_warm_is_idempotent(fake_whisperx):
    model_cache.warm("medium", 4, langs=("es", "en"))
    model_cache.warm("medium", 4, langs=("es", "en"))
    assert fake_whisperx.asr_loads == 1
    assert fake_whisperx.align_loads == ["es", "en"]
    assert model_cache.is_warm("medium") is True
    assert model_cache.is_warm("large-v3") is False


def test_concurrent_first_callers_load_once(fake_whisperx):
    """N threads hitting an empty cache must trigger exactly one load (double-checked lock)."""
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()  # maximize the race on the empty cache
        model_cache.get_asr_model("medium", 4)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert fake_whisperx.asr_loads == 1
