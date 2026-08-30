"""In-process warm cache for WhisperX models (ASR + wav2vec2 alignment).

Loading the WhisperX ASR (automatic speech recognition) model and the per-language
alignment models is the dominant cost of a short/medium analysis — and previously it
happened on EVERY job. Analysis jobs run strictly one-at-a-time (see the single-job lock in
:class:`web.state.JobRegistry`), so a single process-wide model can be safely reused across
jobs. This module holds those models, loaded once and guarded by a lock so a startup warmup
and the first real job never double-load.

Only the single-shot path (short/medium audio) uses this cache. The long-audio chunked path
runs in separate PROCESSES (ProcessPoolExecutor) that cannot share the parent's warm model.

whisperx/torch are imported lazily inside the functions so the rest of the package (and the
test suite) imports without the heavy dependencies installed.
"""

from __future__ import annotations

import threading

# (model_name, threads) -> loaded ASR model.
_asr_models: dict[tuple[str, int], object] = {}
# language code ("es"/"en") -> (align_model, metadata).
_align_models: dict[str, tuple] = {}
# Warm MediaPipe landmarkers (Phase 3 vision). Loaded once, reused across jobs (safe because
# analysis jobs run one-at-a-time — see the single-job lock in web.state.JobRegistry).
_face_landmarker: object | None = None
_pose_landmarker: object | None = None
_lock = threading.Lock()


def _apply_thread_env(threads: int) -> None:
    """Pin CPU thread counts before torch is used (alignment runs on torch).

    ``setdefault`` respects an OMP_NUM_THREADS already exported by the operator/container.
    """
    import os  # noqa: PLC0415

    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    try:
        import torch  # noqa: PLC0415

        torch.set_num_threads(threads)
    except Exception:  # pragma: no cover - torch optional / already configured
        pass


def get_asr_model(model_name: str, threads: int):
    """Return a cached WhisperX ASR model for (model_name, threads), loading once.

    Double-checked locking: the hot path reads without the lock; concurrent first-callers
    serialize on the lock and only the first one loads (the rest see the populated cache).
    """
    key = (model_name, threads)
    model = _asr_models.get(key)
    if model is not None:
        return model
    with _lock:
        model = _asr_models.get(key)  # re-check inside the lock
        if model is None:
            import whisperx  # noqa: PLC0415

            _apply_thread_env(threads)
            model = whisperx.load_model(
                model_name, "cpu", compute_type="int8", threads=threads,
            )
            _asr_models[key] = model
    return model


def get_align_model(lang: str):
    """Return a cached ``(align_model, metadata)`` for ``lang``, loading once per language."""
    cached = _align_models.get(lang)
    if cached is not None:
        return cached
    with _lock:
        cached = _align_models.get(lang)  # re-check inside the lock
        if cached is None:
            import whisperx  # noqa: PLC0415

            cached = whisperx.load_align_model(language_code=lang, device="cpu")
            _align_models[lang] = cached
    return cached


def get_face_landmarker():
    """Return the cached MediaPipe FaceLandmarker, building it once (double-checked lock).

    Construction (and the mediapipe import) lives in ``vision.py`` and is called lazily here,
    so this module stays importable without the ``[vision]`` extra.
    """
    global _face_landmarker
    if _face_landmarker is not None:
        return _face_landmarker
    with _lock:
        if _face_landmarker is None:
            from . import vision  # noqa: PLC0415

            _face_landmarker = vision._build_face_landmarker()
    return _face_landmarker


def get_pose_landmarker():
    """Return the cached MediaPipe PoseLandmarker (lite), building it once."""
    global _pose_landmarker
    if _pose_landmarker is not None:
        return _pose_landmarker
    with _lock:
        if _pose_landmarker is None:
            from . import vision  # noqa: PLC0415

            _pose_landmarker = vision._build_pose_landmarker()
    return _pose_landmarker


def warm(model_name: str, threads: int, langs: tuple[str, ...] = ("es", "en")) -> None:
    """Preload the ASR model and the alignment models for ``langs``. Idempotent.

    Also warms the MediaPipe landmarkers when vision is enabled and the extra is installed,
    so the first analysis does not pay the model-load/download cost. Any vision failure
    (missing extra, offline download) is swallowed — it must not abort warmup.
    """
    get_asr_model(model_name, threads)
    for lang in langs:
        try:
            get_align_model(lang)
        except Exception:  # pragma: no cover - a missing language must not abort warmup
            pass
    from . import config  # noqa: PLC0415

    if config.VISION_ENABLED:
        try:
            get_face_landmarker()
            get_pose_landmarker()
        except Exception:  # pragma: no cover - vision optional; degrade to verbal-only
            pass


def is_warm(model_name: str) -> bool:
    """True if any ASR model for ``model_name`` is loaded (used by tests/health checks)."""
    return any(name == model_name for (name, _threads) in _asr_models)


def reset() -> None:
    """Drop all cached models (test hygiene)."""
    global _face_landmarker, _pose_landmarker
    with _lock:
        _asr_models.clear()
        _align_models.clear()
        _face_landmarker = None
        _pose_landmarker = None
