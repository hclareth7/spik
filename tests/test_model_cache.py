"""Tests for the warm in-process model cache (no real whisperx/torch).

A fake `whisperx` module is injected into ``sys.modules`` so the lazy ``import whisperx``
inside model_cache resolves to counters we can assert on. The cache is reset before each test.
"""

from __future__ import annotations

import sys
import threading
import types

import pytest

from spik import config, model_cache, vision


@pytest.fixture
def fake_whisperx(monkeypatch):
    """Inject a fake whisperx module with load counters; reset the cache around the test.

    Vision warmup is disabled here so ``warm()`` never touches MediaPipe/network — the
    landmarker cache has its own tests below with fake builders.
    """
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
    monkeypatch.setattr(config, "VISION_ENABLED", False)
    model_cache.reset()
    yield mod
    model_cache.reset()


@pytest.fixture
def fake_landmarkers(monkeypatch):
    """Replace the MediaPipe landmarker builders with counters (no mediapipe/network)."""
    counters = {"face": 0, "pose": 0}

    def build_face():
        counters["face"] += 1
        return f"face-landmarker-{counters['face']}"

    def build_pose():
        counters["pose"] += 1
        return f"pose-landmarker-{counters['pose']}"

    monkeypatch.setattr(vision, "_build_face_landmarker", build_face)
    monkeypatch.setattr(vision, "_build_pose_landmarker", build_pose)
    model_cache.reset()
    yield counters
    model_cache.reset()


def test_landmarkers_loaded_once(fake_landmarkers):
    a = model_cache.get_face_landmarker()
    b = model_cache.get_face_landmarker()
    p = model_cache.get_pose_landmarker()
    model_cache.get_pose_landmarker()
    assert a is b
    assert a == "face-landmarker-1"
    assert p == "pose-landmarker-1"
    assert fake_landmarkers == {"face": 1, "pose": 1}


def test_warm_warms_vision_when_enabled(fake_whisperx, fake_landmarkers, monkeypatch):
    monkeypatch.setattr(config, "VISION_ENABLED", True)
    model_cache.warm("medium", 4, langs=("es",))
    assert fake_landmarkers == {"face": 1, "pose": 1}


def test_warm_skips_vision_when_disabled(fake_whisperx, fake_landmarkers):
    # fake_whisperx already sets VISION_ENABLED=False.
    model_cache.warm("medium", 4, langs=("es",))
    assert fake_landmarkers == {"face": 0, "pose": 0}


def test_warm_swallows_vision_errors(fake_whisperx, monkeypatch):
    monkeypatch.setattr(config, "VISION_ENABLED", True)

    def _boom():
        raise RuntimeError("mediapipe missing")

    monkeypatch.setattr(vision, "_build_face_landmarker", _boom)
    monkeypatch.setattr(vision, "_build_pose_landmarker", _boom)
    # Must not raise — a vision failure degrades to verbal-only.
    model_cache.warm("medium", 4, langs=("es",))
    assert fake_whisperx.asr_loads == 1


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
