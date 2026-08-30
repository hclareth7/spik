"""Build the single-owner ffmpeg capture command (Option A).

Historically spik opened the camera from two places — ``capture/record.sh`` (recording)
and ``web/routers/preview.py`` (MJPEG preview) — which fought over the single-open V4L2
device. This module builds ONE ffmpeg command that opens the real camera exactly once and
tees the decoded/copied frames to several sinks (recording file, browser MJPEG preview, …).

Everything here is a PURE function returning an argument list (never ``shell=True``); the
only function that touches the device is :func:`probe_formats`. Keeping the command builder
pure makes the codec-selection logic (ported verbatim from ``record.sh``) unit-testable
without launching ffmpeg or owning a camera.

Security: device/source strings must already be regex-validated by the caller
(``web.validation``); this module only formats them into an argument list.
"""

from __future__ import annotations

import re

from web.utils import run

# Sinks the single pipeline can emit to simultaneously.
SINK_PREVIEW = "preview"   # MJPEG multipart to the browser (stdout pipe)
SINK_RECORD = "record"     # MKV file with muxed audio
SINK_VCAM = "vcam"         # filtered frames written to the v4l2loopback device (Speak Cam)

_SIZE_RE = re.compile(r"\d+x\d+")

# --- Live filter controls (Speak Cam) ---
# UI slider -> ffmpeg param, with the valid numeric range and the neutral (no-op) value.
# vcam.py validates browser input against these BEFORE any value reaches an argument list;
# the values that arrive here are already numbers formatted by us, never raw browser strings.
FILTER_RANGES: dict[str, tuple[float, float]] = {
    "brightness": (-0.3, 0.3),   # eq brightness
    "contrast": (0.5, 1.8),      # eq contrast
    "gamma": (0.5, 2.0),         # eq gamma
    "saturation": (0.0, 2.5),    # eq saturation
    "sharpness": (0.0, 1.5),     # unsharp luma_amount (omitted when 0)
}
FILTER_NEUTRAL: dict[str, float] = {
    "brightness": 0.0, "contrast": 1.0, "gamma": 1.0,
    "saturation": 1.0, "sharpness": 0.0,
}
# Noise reduction (hqdn3d) presets: off / light / strong. "off" omits the filter entirely.
DENOISE_PRESETS: dict[str, str | None] = {
    "off": None,
    "light": "2:1.5:3:3",
    "strong": "6:4:9:6",
}
DENOISE_DEFAULT = "off"

# "Podcast studio" default look the Speak Cam starts with (a polished, natural grade tuned
# to a typical webcam: gentle brightness/gamma lift so the subject isn't muddy, mild
# contrast/saturation for depth and healthy skin tones, moderate sharpen for an HD feel, and
# light denoise to clean sensor grain without the plastic smear of "strong"). Every value is
# inside FILTER_RANGES / DENOISE_PRESETS. The frontend seeds the sliders with these; users
# can still dial back to FILTER_NEUTRAL. Keep this in sync with DEFAULT_FILTERS in the UI.
FILTER_DEFAULT: dict[str, float | str] = {
    "brightness": 0.05, "contrast": 1.15, "gamma": 1.06,
    "saturation": 1.25, "sharpness": 0.7, "denoise": "light",
}


def _fmt(value: float) -> str:
    """Format a filter number compactly for ffmpeg (drops a trailing ``.0``)."""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def build_filter_chain(
    filters: dict | None, *, include_format: bool = True, scale: str | None = None,
) -> str:
    """Build the ``-vf`` chain string for the Speak Cam filters (pure, testable).

    Chain order is **scale -> denoise -> color -> sharpen -> format**: denoise before sharpen so
    we don't amplify noise, and color before sharpen so edges are predictable. Missing keys
    fall back to the neutral value, so a partial dict is safe.

    ``scale`` (e.g. ``scale=min(1280\\,iw):-2``) is inserted FIRST so the expensive denoise/
    sharpen run on the already-downscaled frame — the key latency win for the virtual camera.
    The preview branch passes ``scale=None`` (it appends its own scale after the filters).

    ``include_format`` appends ``format=yuv420p`` (needed for the v4l2 loopback output, which
    webcams and video-call apps expect); the MJPEG preview branch sets it False.
    """
    f: dict = {**FILTER_NEUTRAL, "denoise": DENOISE_DEFAULT}
    if filters:
        f.update(filters)

    parts: list[str] = []
    if scale:
        parts.append(scale)
    preset = DENOISE_PRESETS.get(f["denoise"])
    if preset:
        parts.append(f"hqdn3d={preset}")
    parts.append(
        "eq=brightness={b}:contrast={c}:gamma={g}:saturation={s}".format(
            b=_fmt(f["brightness"]), c=_fmt(f["contrast"]),
            g=_fmt(f["gamma"]), s=_fmt(f["saturation"]),
        )
    )
    if float(f["sharpness"]) > 0:
        parts.append(f"unsharp=5:5:{_fmt(f['sharpness'])}")
    if include_format:
        parts.append("format=yuv420p")
    return ",".join(parts)


def probe_formats(device: str) -> str:
    """Return ffmpeg's ``-list_formats`` output for ``device`` (the pixel formats it exposes).

    This is the ONLY function here that opens the device. It mirrors the probe already used
    by ``preview.py`` and ``devices.py`` (``ffmpeg -f v4l2 -list_formats all``); ffmpeg prints
    the format table to stderr, so both streams are returned joined.
    """
    proc = run(["ffmpeg", "-hide_banner", "-f", "v4l2", "-list_formats", "all", "-i", device])
    return f"{proc.stdout}\n{proc.stderr}"


def _pick_size(formats: str, pattern: str, max_width: int | None = None) -> str | None:
    """Largest ``WxH`` resolution listed on lines matching ``pattern`` (case-insensitive).

    Ports ``record.sh::pick_size``: grep lines by format, extract every ``NxM`` token, and
    keep the biggest by width then height. Returns ``None`` when nothing matches.

    When ``max_width`` is set (the virtual-camera path), prefer the largest mode whose width is
    ``<= max_width`` so ffmpeg decodes a smaller frame outright; if the sensor advertises no such
    mode, fall back to the biggest available (the caller's ``scale`` filter still downsizes it).
    """
    rx = re.compile(pattern, re.IGNORECASE)
    sizes: list[tuple[int, int, str]] = []
    for line in formats.splitlines():
        if rx.search(line):
            for token in _SIZE_RE.findall(line):
                w, h = token.split("x")
                sizes.append((int(w), int(h), token))
    if not sizes:
        return None
    sizes.sort()
    if max_width is not None:
        capped = [s for s in sizes if s[0] <= max_width]
        if capped:
            return capped[-1][2]
    return sizes[-1][2]


def pick_input_args(
    formats: str, device: str, max_width: int | None = None,
) -> tuple[list[str], list[str]]:
    """Choose the camera input args and the recording encoder args from probed ``formats``.

    Ported verbatim from ``record.sh`` (max quality, min CPU):
      1) H.264 on the sensor -> ``-c:v copy``            (0 CPU, ideal)
      2) MJPEG on the sensor -> ``-c:v copy``            (0 CPU, good quality)
      3) Raw (YUYV, …)       -> ``libx264 -preset veryfast -crf 18`` (re-encodes on CPU)

    ``max_width`` caps the requested input resolution (virtual-camera path) so ffmpeg decodes a
    smaller frame; record/preview pass ``None`` to keep the full-resolution copy path intact.

    Returns ``(input_args, enc_args)`` — both plain argument lists.
    """
    if re.search(r"\bh264\b", formats, re.IGNORECASE):
        size = _pick_size(formats, r"\bh264\b", max_width)
        input_args = ["-f", "v4l2", "-input_format", "h264"]
        input_args += (["-video_size", size] if size else []) + ["-i", device]
        enc_args = ["-c:v", "copy"]
    elif re.search(r"\bmjpeg\b", formats, re.IGNORECASE):
        size = _pick_size(formats, r"\bmjpeg\b", max_width)
        input_args = ["-f", "v4l2", "-input_format", "mjpeg"]
        input_args += (["-video_size", size] if size else []) + ["-i", device]
        enc_args = ["-c:v", "copy"]
    else:
        size = _pick_size(formats, r"\d+x\d+", max_width)
        input_args = ["-f", "v4l2"] + (["-video_size", size] if size else []) + ["-i", device]
        enc_args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p"]
    return input_args, enc_args


def _preview_output(filter_prefix: str = "", fps: int = 24) -> list[str]:
    """Map the (once-decoded) video to an MJPEG multipart stream on stdout (``pipe:1``).

    Matches the previous standalone preview: scale to 1280 wide, quality 3. When a
    ``filter_prefix`` is given (Speak Cam ON) it is prepended so the in-app preview shows the
    SAME filtered image the virtual camera emits — WYSIWYG with what Zoom/Meet will see.

    ``fps`` caps the preview frame rate: Checker preview uses 24, but during a recording the
    preview shares the recording ffmpeg (which now decodes + MJPEG-encodes for it), so a lower
    rate reduces contention on the single shared v4l2 input and protects the recording.
    """
    vf = "scale=1280:-2:flags=lanczos"
    if filter_prefix:
        vf = f"{filter_prefix},{vf}"
    return ["-map", "0:v",
            "-vf", vf, "-r", str(fps),
            "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]


def _record_output(enc_args: list[str], out_path: str) -> list[str]:
    """Map video (copy/x264) + audio (input 1) to the MKV file, audio as FLAC mono 48 kHz."""
    return ["-map", "0:v", "-map", "1:a", *enc_args,
            "-ac", "1", "-ar", "48000", "-c:a", "flac", out_path]


def _vcam_output(filter_chain: str, vcam_device: str, fps: int | None = None) -> list[str]:
    """Map the (once-decoded) video through the filter chain to the v4l2loopback device.

    ``yuv420p`` is the pixel format webcams universally expose and every video-call app
    accepts. This is a THIRD ``-map`` output of the same single-owner ffmpeg, so it shares
    the one real-camera open — writing to the loopback can never cause a "device busy".

    ``fps`` caps the frames written to the loopback (real-time hygiene): without it, uncapped
    multi-MB raw writes let latency accumulate. Placed on the OUTPUT (not input ``-framerate``),
    which the driver may not negotiate exactly.
    """
    rate = ["-r", str(fps)] if fps else []
    return ["-map", "0:v", "-vf", filter_chain, *rate,
            "-f", "v4l2", "-pix_fmt", "yuv420p", vcam_device]


def build_capture_cmd(
    *,
    device: str,
    sinks: set[str],
    formats: str,
    out_path: str | None = None,
    audio_source: str | None = None,
    filters: dict | None = None,
    vcam_device: str | None = None,
    max_width: int | None = None,
    fps: int | None = None,
) -> list[str]:
    """Assemble the full single-owner ffmpeg command for the requested ``sinks``.

    The camera (input 0) is opened ONCE. When the ``record`` sink is present, the pulse
    audio source is added as input 1 and muxed only into the recording file — the preview
    branch stays video-only, exactly as before. When the ``vcam`` sink is present, a filtered
    branch is written to ``vcam_device`` (the v4l2loopback "Speak Cam"), and the preview
    branch mirrors those same filters so the in-app preview is WYSIWYG with the virtual cam.

    Raises ``ValueError`` on inconsistent requests (no sinks; a record sink without an
    output path / audio source; a vcam sink without a device) so callers fail fast instead
    of spawning a broken ffmpeg.
    """
    if not sinks:
        raise ValueError("at least one sink is required")
    if SINK_RECORD in sinks and (out_path is None or audio_source is None):
        raise ValueError("the record sink requires out_path and audio_source")
    if SINK_VCAM in sinks and vcam_device is None:
        raise ValueError("the vcam sink requires vcam_device")

    # The virtual-camera path runs alone ({vcam}); only there do we cap input size and apply
    # low-latency flags, so record/preview keep the full-resolution copy path unchanged.
    vcam_only = SINK_VCAM in sinks and SINK_RECORD not in sinks
    input_args, enc_args = pick_input_args(
        formats, device, max_width=max_width if vcam_only else None,
    )

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if vcam_only:
        # Reduce demuxer buffering on the live source and cut decode reorder delay (harmless for
        # B-frame-less webcam h264). These are INPUT flags, so they precede input_args (the -i).
        cmd += ["-fflags", "nobuffer", "-flags", "low_delay"]
    cmd += input_args
    if SINK_RECORD in sinks:
        # Audio becomes input 1, so the record output can -map 1:a.
        cmd += ["-f", "pulse", "-i", audio_source]  # type: ignore[list-item]
        cmd += _record_output(enc_args, out_path)   # type: ignore[arg-type]
    if SINK_VCAM in sinks:
        # Downscale FIRST (before the heavy denoise/sharpen) and never upscale a smaller sensor.
        # The comma inside min() is escaped: the whole chain is a single -vf argument.
        scale = f"scale=min({max_width}\\,iw):-2" if max_width else None
        chain = build_filter_chain(filters, scale=scale)
        cmd += _vcam_output(chain, vcam_device, fps)  # type: ignore[arg-type]
    if SINK_PREVIEW in sinks:
        # Speak Cam ON -> mirror the vcam filters into the preview (WYSIWYG); otherwise raw.
        prefix = build_filter_chain(filters, include_format=False) if SINK_VCAM in sinks else ""
        # During a recording the preview shares the recording ffmpeg; cap it lower to keep the
        # extra decode+encode from starving the shared v4l2 input (which would drop frames on
        # the recording too). Checker preview (no record sink) keeps the smooth 24 fps.
        preview_fps = 12 if SINK_RECORD in sinks else 24
        cmd += _preview_output(prefix, preview_fps)
    return cmd
