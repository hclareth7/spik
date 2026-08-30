"""Local device enumeration: cameras (/dev/video*) and audio sources (mics)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query

from spik import config
from web import deps
from web.utils import run
from web.validation import _VIDEO_DEV_RE

router = APIRouter()


@router.get("/api/devices")
def list_devices() -> dict:
    """Lista cámaras (/dev/video*) y fuentes de audio (micrófonos) locales."""
    deps.require_local()
    cameras = []
    for dev in sorted(Path("/dev").glob("video*")):
        name_file = Path(f"/sys/class/video4linux/{dev.name}/name")
        name = name_file.read_text().strip() if name_file.exists() else dev.name
        # Mark the "Speak Cam" loopback as virtual: the frontend must NOT offer it as a
        # capture *source* (opening it while we write to it would feed back on itself); it's
        # listed only informationally as an OUTPUT. Mirrors how mics flag ``clean``.
        virtual = str(dev) == config.VCAM_DEVICE and name == config.VCAM_LABEL
        cameras.append({"device": str(dev), "name": name, "virtual": virtual})

    sources = []
    proc = run(["pactl", "list", "sources", "short"])
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[1]
        # Omitimos los ".monitor" (salidas de altavoces), no son micrófonos.
        if name.endswith(".monitor"):
            continue
        sources.append({
            "name": name,
            "clean": name == "speak_clean_mic",  # nuestra fuente virtual filtrada
        })
    return {"cameras": cameras, "sources": sources}


@router.get("/api/camera-info")
def camera_info(device: str = Query(...)) -> dict:
    """Formatos/resoluciones soportadas por la cámara (vía ffmpeg; v4l2-ctl no está instalado)."""
    deps.require(bool(_VIDEO_DEV_RE.match(device)), "dispositivo de video inválido")
    deps.require(Path(device).exists(), f"{device} no existe")
    # ffmpeg escribe los formatos soportados en stderr con -list_formats.
    proc = run(["ffmpeg", "-hide_banner", "-f", "v4l2", "-list_formats", "all", "-i", device])
    lines = [ln.strip() for ln in proc.stderr.splitlines() if "]" in ln and ("fps" in ln or "Raw" in ln or "Compressed" in ln)]
    return {"device": device, "formats": lines or ["(instala v4l-utils para detalles: dnf install v4l-utils)"]}
