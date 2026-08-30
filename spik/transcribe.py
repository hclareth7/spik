"""Transcription with WhisperX -> per-word timestamps, bilingual (es/en).

Two paths:
  - **single-shot** (short audio, < CHUNK_THRESHOLD_S): a single model, as before.
  - **concurrent chunked** (long audio, 2-3 h): the WAV is split into silence-aware chunks
    (audio.plan_chunks) that are transcribed+aligned in parallel PROCESSES
    (ProcessPoolExecutor). Each chunk's timestamps are shifted back to absolute time and
    stitched (`_stitch`), so verbal.analyze computes WPM/pauses/fillers correctly.

The heavy dependencies (whisperx, torch) are imported inside the functions so the rest of the
package can be imported and tested without having them installed.
"""

from __future__ import annotations

import multiprocessing as mp
import tempfile
from concurrent.futures import ProcessPoolExecutor
from collections import Counter
from pathlib import Path
from typing import Callable

from . import audio, config
from .models import Transcript, Word

# Progress callback signature: (stage, fraction 0..1).
ProgressCb = Callable[[str, float], None]


def transcribe(
    audio_path: Path,
    model_name: str | None = None,
    language: str | None = None,
    progress_cb: ProgressCb | None = None,
) -> Transcript:
    """Transcribe a WAV and return a normalized Transcript with per-word timestamps.

    - `model_name`: Whisper model ("small"/"medium"/"large-v3"). Default: config.WHISPER_MODEL.
    - `language`: force the language ("es"/"en"). If None, WhisperX auto-detects it.
    - `progress_cb`: optional; called with (stage, fraction) to report progress.

    Runs on CPU with compute_type int8 (no NVIDIA GPU on this machine). With a GPU,
    change device="cuda" and compute_type="float16".
    """
    model_name = model_name or config.WHISPER_MODEL
    audio_path = Path(audio_path)

    # Decide the path based on duration.
    try:
        duration = audio.probe_duration(audio_path)
    except Exception:  # pragma: no cover - if ffprobe fails, fall back to single-shot
        duration = 0.0

    if duration >= config.CHUNK_THRESHOLD_S:
        return _transcribe_chunked(audio_path, model_name, language, duration, progress_cb)
    return _transcribe_single(audio_path, model_name, language, progress_cb)


# ============================================================================
# Single-shot path (short audio)
# ============================================================================
def _transcribe_single(
    audio_path: Path,
    model_name: str,
    language: str | None,
    progress_cb: ProgressCb | None,
) -> Transcript:
    import whisperx  # noqa: PLC0415

    from . import model_cache  # noqa: PLC0415

    if progress_cb:
        progress_cb("transcribe", 0.0)
    # Warm cache: the ASR model and per-language alignment models are loaded ONCE per process
    # and reused across jobs (jobs are single-at-a-time), instead of reloaded every analysis.
    threads = config.asr_threads()
    asr = model_cache.get_asr_model(model_name, threads)
    words, text, lang = _run_whisperx(
        whisperx, audio_path, model_name, language,
        threads=threads, batch_size=config.WHISPER_BATCH_SIZE,
        model=asr, align_loader=model_cache.get_align_model,
    )
    if progress_cb:
        progress_cb("transcribe", 1.0)
    return Transcript(language=lang, words=[Word(*w) for w in words], text=text)


# ============================================================================
# Concurrent chunked path (long audio)
# ============================================================================
def _transcribe_chunked(
    audio_path: Path,
    model_name: str,
    language: str | None,
    duration: float,
    progress_cb: ProgressCb | None,
) -> Transcript:
    workers = config.auto_workers()
    threads = config.worker_threads(workers)

    if progress_cb:
        progress_cb("split", 0.0)
    silences = audio.detect_silences(audio_path)
    chunks = audio.plan_chunks(duration, silences, config.WHISPER_CHUNK_S)

    # Ephemeral tmpdir: the sub-WAVs never leave the machine and are deleted on exit.
    with tempfile.TemporaryDirectory(prefix="spik-chunks-") as tmp:
        chunk_paths = audio.split_wav(audio_path, chunks, Path(tmp))
        offsets = [start for start, _ in chunks]
        if progress_cb:
            progress_cb("split", 1.0)

        total = len(chunk_paths)
        results: dict[int, tuple[list[tuple[str, float, float]], str, str]] = {}
        done = 0

        # 'spawn' avoids the known fork() + torch/OpenMP deadlocks in subprocesses.
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=ctx,
            initializer=_init_worker, initargs=(model_name, threads),
        ) as pool:
            futs = {
                pool.submit(_transcribe_chunk_task, i, str(p), language): i
                for i, p in enumerate(chunk_paths)
            }
            for fut in _as_completed(futs):
                idx = futs[fut]
                results[idx] = fut.result()
                done += 1
                if progress_cb:
                    progress_cb("transcribe", done / total)

    ordered = [results[i] for i in range(total)]
    return _stitch(ordered, offsets)


def _as_completed(futs):
    """Wrap concurrent.futures.as_completed (local import so tests run without whisperx)."""
    from concurrent.futures import as_completed  # noqa: PLC0415
    return as_completed(futs)


# --- Per-worker-process state (loaded once and reused across chunks) ---
_WORKER: dict = {}


def _init_worker(model_name: str, threads: int) -> None:
    """ProcessPool initializer: load the Whisper model ONCE per process."""
    import whisperx  # noqa: PLC0415

    _WORKER["whisperx"] = whisperx
    _WORKER["model"] = whisperx.load_model(
        model_name, "cpu", compute_type="int8", threads=threads,
    )
    _WORKER["align"] = {}  # cache of alignment models by language


def _transcribe_chunk_task(
    index: int, chunk_path: str, language: str | None,
) -> tuple[list[tuple[str, float, float]], str, str]:
    """Transcribe+align a chunk. Returns (words, text, lang) with RELATIVE timestamps."""
    whisperx = _WORKER["whisperx"]
    model = _WORKER["model"]
    words, text, lang = _run_whisperx(
        whisperx, Path(chunk_path), None, language,
        threads=None, batch_size=config.WHISPER_BATCH_SIZE,
        model=model, align_cache=_WORKER["align"],
    )
    return words, text, lang


# ============================================================================
# WhisperX core shared by both paths
# ============================================================================
def _run_whisperx(
    whisperx,
    audio_path: Path,
    model_name: str | None,
    language: str | None,
    threads: int | None,
    batch_size: int,
    model=None,
    align_cache: dict | None = None,
    align_loader=None,
) -> tuple[list[tuple[str, float, float]], str, str]:
    """Run transcription + alignment and return (words, text, lang) -- relative timestamps.

    `words` are PICKLABLE (text, start, end) tuples (to cross the process boundary).

    Alignment-model source, in priority order: ``align_cache`` (per-process dict, used by the
    ProcessPool workers) -> ``align_loader`` (a caching callable, used by the warm single-shot
    path) -> ``whisperx.load_align_model`` (fresh load).
    """
    if model is None:
        model = whisperx.load_model(
            model_name, "cpu", compute_type="int8", threads=threads or 4,
        )
    audio_data = whisperx.load_audio(str(audio_path))
    result = model.transcribe(audio_data, batch_size=batch_size, language=language)
    detected = result.get("language", language or "en")

    # Alignment -> per-word timestamps (reuse the alignment model if cached).
    if align_cache is not None and detected in align_cache:
        align_model, meta = align_cache[detected]
    elif align_loader is not None:
        align_model, meta = align_loader(detected)
    else:
        align_model, meta = whisperx.load_align_model(language_code=detected, device="cpu")
        if align_cache is not None:
            align_cache[detected] = (align_model, meta)

    aligned = whisperx.align(
        result["segments"], align_model, meta, audio_data, "cpu",
        return_char_alignments=False,
    )

    words: list[tuple[str, float, float]] = []
    texts: list[str] = []
    for seg in aligned.get("segments", []):
        texts.append(seg.get("text", "").strip())
        for w in seg.get("words", []):
            if "start" in w and "end" in w and w.get("word"):
                words.append((w["word"].strip(), float(w["start"]), float(w["end"])))
    return words, " ".join(t for t in texts if t), detected


def _stitch(
    chunk_results: list[tuple[list[tuple[str, float, float]], str, str]],
    offsets: list[float],
) -> Transcript:
    """Stitch the chunks: shift timestamps to absolute time and concatenate (pure function).

    `chunk_results[i]` = (relative_words, text, language) of chunk i; `offsets[i]` = its
    absolute start time in the original audio. The final language is the majority one.
    """
    all_words: list[Word] = []
    texts: list[str] = []
    langs: list[str] = []
    for (words, text, lang), offset in zip(chunk_results, offsets):
        langs.append(lang)
        if text:
            texts.append(text)
        for w_text, start, end in words:
            all_words.append(Word(text=w_text, start=start + offset, end=end + offset))

    language = Counter(langs).most_common(1)[0][0] if langs else "en"
    return Transcript(language=language, words=all_words, text=" ".join(texts))
