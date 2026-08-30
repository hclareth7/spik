"""Audio extraction and chunking, via ffmpeg/ffprobe.

For long audio (2-3 h) the WAV is split into **silence-aware** chunks (so words are not cut)
that are then transcribed in parallel (see transcribe.py). The pure functions (`plan_chunks`)
are testable without ffmpeg.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


def ensure_ffmpeg() -> None:
    """Fail early and clearly if ffmpeg is not installed."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg not found in PATH. Install it (e.g. `sudo dnf install ffmpeg`)."
        )


def extract_audio(video: Path, out: Path | None = None) -> Path:
    """Extract audio to mono 16 kHz PCM WAV (optimal format for Whisper).

    Returns the WAV path. If `out` is None, uses the same name with .wav.
    """
    ensure_ffmpeg()
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(f"Video does not exist: {video}")
    out = Path(out) if out else video.with_suffix(".wav")

    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


# ============================================================================
# Silence-aware chunking (for concurrent transcription)
# ============================================================================
def probe_duration(path: Path) -> float:
    """Duration in seconds of an audio/video file, via ffprobe."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:  # pragma: no cover - rare ffprobe output without duration
        return 0.0


_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)")


def detect_silences(wav: Path, noise_db: int = -30, min_silence_s: float = 0.5) -> list[tuple[float, float]]:
    """Detect silence intervals with ffmpeg's `silencedetect` filter.

    Returns a list of (start, end) in seconds. Used to pick cut points that fall in silence
    and do not split a word.
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav),
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}", "-f", "null", "-"],
        check=False, capture_output=True, text=True,
    )
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for line in proc.stderr.splitlines():
        if "silence_start:" in line:
            try:
                start = float(line.split("silence_start:")[1].strip().split()[0])
            except (IndexError, ValueError):
                start = None
        else:
            m = _SILENCE_END_RE.search(line)
            if m and start is not None:
                silences.append((start, float(m.group(1))))
                start = None
    return silences


def plan_chunks(
    duration_s: float,
    silences: list[tuple[float, float]],
    target_s: float,
) -> list[tuple[float, float]]:
    """Choose cut points to chunk the audio (pure function, testable without ffmpeg).

    For each target boundary (target_s, 2*target_s, ...) it looks for the silence whose
    midpoint is closest and cuts there; if there's no nearby silence (within ±target_s/2), it
    cuts at the exact boundary. Returns contiguous (start, end) chunks covering [0, duration_s].
    """
    if duration_s <= 0:
        return []
    if duration_s <= target_s:
        return [(0.0, duration_s)]

    # Silence midpoints as cut candidates.
    mids = sorted((s + e) / 2.0 for s, e in silences if e > s)
    tolerance = target_s / 2.0

    cuts: list[float] = []
    boundary = target_s
    while boundary < duration_s - tolerance:
        # Silence candidate closest to the boundary.
        best = None
        best_dist = tolerance
        for mid in mids:
            if mid <= (cuts[-1] if cuts else 0.0):
                continue
            dist = abs(mid - boundary)
            if dist < best_dist:
                best, best_dist = mid, dist
        cut = best if best is not None else boundary
        # Avoid degenerate chunks (non-monotonic cut).
        if cut > (cuts[-1] if cuts else 0.0) + 1.0:
            cuts.append(round(cut, 3))
        boundary += target_s

    # Build the contiguous intervals from the cuts.
    chunks: list[tuple[float, float]] = []
    prev = 0.0
    for cut in cuts:
        chunks.append((prev, cut))
        prev = cut
    chunks.append((prev, duration_s))
    return chunks


def split_wav(wav: Path, chunks: list[tuple[float, float]], out_dir: Path) -> list[Path]:
    """Cut the WAV into sub-WAVs according to `chunks`, returning their paths in order.

    Re-encoded to PCM (sample-accurate cutting); Whisper's audio is mono 16 kHz, so the cost
    is trivial.
    """
    ensure_ffmpeg()
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, (start, end) in enumerate(chunks):
        out = out_dir / f"chunk_{i:03d}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(wav), "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)],
            check=True, capture_output=True,
        )
        paths.append(out)
    return paths
