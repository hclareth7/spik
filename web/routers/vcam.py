"""Speak Cam — virtual camera (v4l2loopback) with live light/color filters, host-only.

Mirrors ``noise.py`` (the "Speak Clean Mic" toggle): status soft-returns off-host, and every
mutating endpoint is hard-gated by ``deps.require_host_session()`` (503 outside 'local').

Runtime never runs ``modprobe``/``sudo``: it only *writes* filtered frames to the already
provisioned ``/dev/video10`` (see ``camera/install.sh``). The single-owner ffmpeg
(``state.capture``) opens the real camera once and feeds the loopback as a third ``-map``
output, so there is never a "device busy". The loopback allows many readers, so Zoom/Meet/OBS
read the virtual cam concurrently — and it keeps running even when the spik tab is closed
(``vcam/start`` uses only the ``vcam`` sink, no browser preview pipe to stall on).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from spik import config
from web import deps, state
from web.capture_pipeline import DENOISE_PRESETS, FILTER_NEUTRAL, FILTER_RANGES, SINK_VCAM
from web.validation import _VIDEO_DEV_RE

router = APIRouter()


def _vcam_available() -> bool:
    """True when the loopback node exists AND advertises our label (device provisioned).

    Degrades gracefully when the kernel module is missing / still rebuilding after a kernel
    update (akmod), so the card can say "run camera/install.sh" instead of crashing.
    """
    dev = config.VCAM_DEVICE
    if not Path(dev).exists():
        return False
    name_file = Path(f"/sys/class/video4linux/{Path(dev).name}/name")
    return name_file.is_file() and name_file.read_text().strip() == config.VCAM_LABEL


def _validated_filters(
    brightness: float, contrast: float, gamma: float,
    saturation: float, sharpness: float, denoise: str,
) -> dict:
    """Validate every filter value is numeric and in range BEFORE it reaches an arg list.

    Values arrive already coerced to float by FastAPI; here we enforce the ranges declared in
    ``capture_pipeline.FILTER_RANGES`` (400 on violation) so no out-of-range or unknown value
    is ever formatted into the ffmpeg command.
    """
    vals = {
        "brightness": brightness, "contrast": contrast, "gamma": gamma,
        "saturation": saturation, "sharpness": sharpness,
    }
    out: dict = {}
    for key, (lo, hi) in FILTER_RANGES.items():
        v = vals[key]
        deps.require(lo <= v <= hi, f"{key} out of range [{lo}, {hi}]")
        out[key] = float(v)
    deps.require(denoise in DENOISE_PRESETS, "invalid denoise preset (off/light/strong)")
    out["denoise"] = denoise
    return out


@router.get("/api/vcam/status")
def vcam_status() -> dict:
    # The virtual camera only exists in the local host app (it needs the host kernel module
    # and to be the single opener of the real camera). Off-host: everything false, no FS touch.
    if not deps.host_session_available():
        return {"active": False, "available": False, "device": config.VCAM_DEVICE, "filters": None}
    return {
        "active": state.capture.is_vcam(),
        "available": _vcam_available(),
        "device": config.VCAM_DEVICE,
        "filters": state.capture.filters if state.capture.is_vcam() else None,
    }


@router.post("/api/vcam/start")
async def vcam_start(
    source: str = Query("/dev/video4"),
    brightness: float = Query(FILTER_NEUTRAL["brightness"]),
    contrast: float = Query(FILTER_NEUTRAL["contrast"]),
    gamma: float = Query(FILTER_NEUTRAL["gamma"]),
    saturation: float = Query(FILTER_NEUTRAL["saturation"]),
    sharpness: float = Query(FILTER_NEUTRAL["sharpness"]),
    denoise: str = Query("off"),
) -> dict:
    # Synchronous gates first (mode/validation must raise before any await).
    deps.require_host_session()
    deps.require(bool(_VIDEO_DEV_RE.match(source)), "invalid video device")
    # Anti-feedback guard: opening the loopback as a SOURCE while writing to it would loop.
    deps.require(source != config.VCAM_DEVICE, "the source cannot be the virtual camera itself")
    deps.require(Path(source).exists(), f"{source} does not exist")
    deps.require(_vcam_available(),
                 "Speak Cam device not found. Run: sudo bash camera/install.sh")
    filters = _validated_filters(brightness, contrast, gamma, saturation, sharpness, denoise)

    # Single-owner ffmpeg: open the real camera once, write filtered frames to the loopback.
    # No preview sink -> no unread pipe to stall on; the feed survives the spik tab closing.
    proc = await state.capture.start(
        device=source, sinks={SINK_VCAM},
        filters=filters, vcam_device=config.VCAM_DEVICE,
        max_width=config.VCAM_MAX_WIDTH, fps=config.VCAM_FPS,
    )
    # If ffmpeg dies immediately (source busy, loopback vanished, …) report it now.
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass  # still alive => virtual camera running
    else:
        await state.capture.stop()
        raise HTTPException(
            status_code=500,
            detail="Could not start Speak Cam (is the source camera in use, or is "
                   f"{config.VCAM_DEVICE} present? try: sudo bash camera/install.sh).",
        )
    return {"active": True, "device": config.VCAM_DEVICE, "filters": filters}


@router.post("/api/vcam/stop")
async def vcam_stop() -> dict:
    deps.require_host_session()
    if state.capture.is_vcam():
        await state.capture.stop()
    return {"active": False}


@router.post("/api/vcam/set-filters")
async def vcam_set_filters(
    brightness: float = Query(FILTER_NEUTRAL["brightness"]),
    contrast: float = Query(FILTER_NEUTRAL["contrast"]),
    gamma: float = Query(FILTER_NEUTRAL["gamma"]),
    saturation: float = Query(FILTER_NEUTRAL["saturation"]),
    sharpness: float = Query(FILTER_NEUTRAL["sharpness"]),
    denoise: str = Query("off"),
) -> dict:
    deps.require_host_session()
    deps.require(state.capture.is_vcam(), "Speak Cam is not running.")
    filters = _validated_filters(brightness, contrast, gamma, saturation, sharpness, denoise)
    # ffmpeg cannot hot-swap a filtergraph on a live v4l2 output, so re-apply by restarting the
    # single owner (~1 s). The frontend debounces slider moves to keep restarts infrequent.
    source = state.capture.device
    await state.capture.restart_with(
        device=source, sinks={SINK_VCAM},
        filters=filters, vcam_device=config.VCAM_DEVICE,
        max_width=config.VCAM_MAX_WIDTH, fps=config.VCAM_FPS,
    )
    return {"active": True, "device": config.VCAM_DEVICE, "filters": filters}
