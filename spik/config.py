"""Central configuration: paths, models and filler-word dictionaries.

Everything user-tunable lives here or in environment variables (.env).
No hardcoded secrets: the Claude key is read from ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load the .env HERE (once, before reading the variables). Since this module is imported by
# both the CLI and the web server, both end up with the SAME configuration. Previously only
# cli.py loaded the .env, so the web server started without SPIK_VERTEX_PROJECT and feedback
# failed with "internal server error" (RuntimeError in feedback.py). override=False respects
# variables already present in the environment (e.g. those injected by docker-compose/Traefik).
load_dotenv(override=False)


def _env(name: str, default: str | None = None) -> str | None:
    """Read SPIK_<name> with fallback to SPEAK_<name> (historical name) and then the default.

    The project was renamed from "speak" to "spik"; we accept both prefixes so as not to break
    existing .env files that still use SPEAK_*.
    """
    return os.getenv(f"SPIK_{name}") or os.getenv(f"SPEAK_{name}") or default


# --- Paths ---
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
# DATA_DIR is overridable (in a container a volume is mounted and the path may change).
DATA_DIR = Path(_env("DATA_DIR") or (PROJECT_DIR / "data"))
DB_PATH = DATA_DIR / "sessions.db"
REPORTS_DIR = DATA_DIR / "reports"

# --- Models (with environment override) ---
# On CPU (no NVIDIA GPU) use "small" or "medium"; with a GPU use "large-v3".
WHISPER_MODEL = _env("WHISPER_MODEL", "medium")
# Claude model for feedback. If empty, the default below is used.
# claude-opus-5 is the recommended default model (claude-api skill, 2026-06).
# On Vertex use the "bare" id (no prefix); check which one you have enabled in
# your Model Garden (e.g. claude-opus-4-5, claude-sonnet-5, ...).
CLAUDE_MODEL = _env("CLAUDE_MODEL") or "claude-opus-5"

# --- Model provider (where Claude runs) ---
# "vertex"    -> Google Cloud Vertex AI (auth via gcloud ADC, no API key)
# "anthropic" -> Claude API directly (auth via ANTHROPIC_API_KEY)
PROVIDER = (_env("PROVIDER", "vertex") or "vertex").lower()

# --- Vertex AI settings (only if PROVIDER == "vertex") ---
# project_id: id of your GCP project.  region: "global" (recommended), "us", "eu" or a region.
VERTEX_PROJECT = _env("VERTEX_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
VERTEX_REGION = _env("VERTEX_REGION", "global")

# --- Run mode of the web GUI ---
# "local"     -> full app on 127.0.0.1 (camera, mic, noise, recording, analysis).
# "server"    -> container behind Traefik: analysis, feedback and history ONLY. All features
#                that need host hardware (/dev/video*, PipeWire) are hidden.
# "appliance" -> PRIVILEGED container for sharing (deploy/docker-compose.share.yaml):
#                capture + record + analysis, but WITHOUT the live noise filter (it needs
#                `systemctl --user`, which does not exist in the container; the filter lives
#                on the host).
MODE = (_env("MODE", "local") or "local").lower()

# --- Virtual camera ("Speak Cam", v4l2loopback) ---
# The virtual webcam device fed by the single-owner capture pipeline (host-only). It is
# provisioned once by camera/install.sh (sudo modprobe v4l2loopback video_nr=10 ...); the
# app NEVER runs modprobe/sudo — it only writes filtered frames to an already-existing node.
# video_nr=10 in the modprobe options must match VCAM_DEVICE here.
VCAM_DEVICE = _env("VCAM_DEVICE", "/dev/video10")
# Friendly card_label the module advertises (read back from /sys/class/video4linux/*/name).
VCAM_LABEL = _env("VCAM_LABEL", "Speak Cam")
# NOTE: VCAM_MAX_WIDTH / VCAM_FPS are defined after _int_env() below (it is declared later).


# ============================================================================
# Transcription performance (long audio: 2-3 h) — all overridable via .env
# ============================================================================
def _int_env(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    """Read a boolean SPIK_<name> ("1/true/yes/on" => True), else the default."""
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- Virtual camera geometry/framerate caps (see the VCAM block above) ---
# Cap the Speak Cam for REAL-TIME output. The heavy filter chain (hqdn3d/eq/unsharp) plus raw
# yuv420p writes to the loopback are CPU-bound at full sensor resolution (e.g. 2560x1440), which
# accumulates latency in Meet/Zoom. Downscaling before the filters and capping fps keeps the feed
# real-time; video-call apps downscale to <=720p anyway. Recording is a separate branch
# (-c:v copy at full size) and is NOT affected by these.
VCAM_MAX_WIDTH = _int_env("VCAM_MAX_WIDTH", 1280)
VCAM_FPS = _int_env("VCAM_FPS", 30)

# --- Web server bind (GUI) ---
# LOOPBACK ONLY for privacy ("todo local"): video/audio must never be reachable from the
# network. The host defaults to 127.0.0.1 and is guarded in web/main.py (a non-loopback host
# is refused). The Wails desktop shell (desktop/) overrides SPIK_PORT with a free localhost
# port it picks at launch, then reverse-proxies the webview to it; SPIK_HOST stays loopback.
WEB_HOST = _env("HOST", "127.0.0.1")
WEB_PORT = _int_env("PORT", 8000)


# WhisperX batch size (batched inference). Previously none was passed.
WHISPER_BATCH_SIZE = _int_env("WHISPER_BATCH_SIZE", 8)
# Number of worker processes to transcribe chunks in parallel. 0 = auto (see auto_workers()).
WHISPER_WORKERS = _int_env("WHISPER_WORKERS", 0)
# Target duration of each chunk, in seconds (cut points are snapped to the nearest silence).
WHISPER_CHUNK_S = _int_env("WHISPER_CHUNK_S", 600)  # 10 min
# Above this duration the chunked+concurrent path kicks in; below it, single-shot.
CHUNK_THRESHOLD_S = _int_env("CHUNK_THRESHOLD_S", 900)  # 15 min
# Estimated RAM per worker (medium int8 model + wav2vec2 + audio). Bounds auto_workers().
_MEM_PER_WORKER_GB = 3.0
# Preload WhisperX models at server startup (background thread) so the FIRST analysis does
# not pay the one-time model-load cost. Disable with SPIK_WARMUP=0.
WARMUP = _bool_env("WARMUP", True)


def auto_workers() -> int:
    """Safe number of worker processes: bounded by CPU and available RAM.

    Each worker loads its own model (~3 GB), so oversubscribing RAM would cause swapping.
    Conservative default (4) if it can't be measured. Overridable with SPIK_WHISPER_WORKERS.
    """
    if WHISPER_WORKERS > 0:
        return WHISPER_WORKERS
    try:
        cores = os.cpu_count() or 4
    except NotImplementedError:  # pragma: no cover
        cores = 4
    # CPU cap: more than ~cores/4 makes no sense (each worker uses several CTranslate2 threads).
    cpu_cap = max(1, cores // 4)
    # Cap by available memory.
    mem_cap = 4
    try:
        # Read MemAvailable from /proc/meminfo (Linux) -> GB.
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    avail_gb = int(line.split()[1]) / 1_048_576  # kB -> GB
                    mem_cap = max(1, int(avail_gb // _MEM_PER_WORKER_GB))
                    break
    except (OSError, ValueError):  # pragma: no cover
        pass
    return max(1, min(cpu_cap, mem_cap, 6))  # hard ceiling of 6 on this class of machine


def worker_threads(workers: int) -> int:
    """CTranslate2 threads per worker, splitting the cores so as not to oversubscribe."""
    try:
        cores = os.cpu_count() or 4
    except NotImplementedError:  # pragma: no cover
        cores = 4
    return max(1, cores // max(1, workers))


def asr_threads() -> int:
    """Threads for the single warm ASR model (single-shot path + startup warmup).

    The warm in-process model and the chunked ProcessPool are mutually exclusive per job, so
    the single model may use all cores. Overridable with SPIK_ASR_THREADS.
    """
    n = _int_env("ASR_THREADS", 0)
    if n > 0:
        return n
    try:
        return os.cpu_count() or 4
    except NotImplementedError:  # pragma: no cover
        return 4

# --- Reference prices (USD per million tokens) to estimate the cost of each session.
# Approximate; adjust according to your contract/region. Only feedback (text) is billed;
# transcription and metrics run locally (free). Key = model prefix.
MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # model (prefix): (input, output)
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (1.0, 5.0),
}


def price_for(model: str) -> tuple[float, float]:
    """(input_price, output_price) in USD/million tokens for the given model."""
    for prefix, price in MODEL_PRICING_USD_PER_MTOK.items():
        if model.startswith(prefix):
            return price
    return (0.0, 0.0)  # unknown -> no cost estimate

# --- Verbal analysis thresholds ---
# A "long" pause (silence between words) counted as hesitation/doubt, in seconds.
LONG_PAUSE_S = 0.8
# Comfortable human speaking-rate range (words per minute) for context.
WPM_COMFORTABLE = (120, 160)

# --- Filler-word dictionaries ---
# Compared in lowercase and without punctuation. Deliberately conservative:
# including very common words (e.g. "no", "so") would produce false positives.
FILLERS_ES: set[str] = {
    "este", "esto", "eh", "em", "mmm", "pues", "digamos",
    "bueno", "verdad", "vale",
    # Added after reviewing real sessions (Claude flagged them and the detector didn't).
    # Note: stored WITHOUT accents and in lowercase because _normalize() strips accents.
    "entonces", "perdon",
}
FILLERS_EN: set[str] = {
    "um", "uh", "er", "erm", "hmm", "like", "actually",
    "basically", "literally",
}
# Multi-word fillers (matched as a sequence of tokens).
FILLER_PHRASES_ES: list[tuple[str, ...]] = [
    ("o", "sea"), ("o", "sea", "que"), ("es", "decir"),
    ("como", "que"), ("no", "sé"), ("¿", "no", "?"),
]
FILLER_PHRASES_EN: list[tuple[str, ...]] = [
    ("you", "know"), ("i", "mean"), ("kind", "of"),
    ("sort", "of"), ("you", "know", "what", "i", "mean"),
]


def fillers_for(language: str) -> tuple[set[str], list[tuple[str, ...]]]:
    """Return (unigram_fillers, phrase_fillers) for the given language.

    `language` is the Whisper code ("es", "en", ...). Unsupported languages return
    empty sets (no fillers detected, the analysis is not broken).
    """
    if language.startswith("es"):
        return FILLERS_ES, FILLER_PHRASES_ES
    if language.startswith("en"):
        return FILLERS_EN, FILLER_PHRASES_EN
    return set(), []
