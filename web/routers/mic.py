"""Microphone level meter (VU via SSE) and the record-and-listen mic test."""

from __future__ import annotations

import asyncio
import math
import subprocess

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, StreamingResponse

from spik import config
from web import deps
from web.paths import MIC_TEST_WAV
from web.validation import _SOURCE_RE

router = APIRouter()


async def _mic_level_stream(source: str):
    """Muestrea el micro con parec y emite dBFS por Server-Sent Events (~5 Hz)."""
    # Ventana corta (~50 ms => ~20 Hz de refresco) y latencia baja en parec para que el
    # medidor reaccione casi en tiempo real al hablar (antes: 200 ms + buffer por defecto).
    rate, channels, window_s = 16000, 1, 0.05
    frame_bytes = int(rate * channels * 2 * window_s)  # s16le = 2 bytes/muestra
    cmd = ["parec", "--format=s16le", f"--rate={rate}", f"--channels={channels}",
           "--latency-msec=30", "-d", source]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        while True:
            data = await proc.stdout.readexactly(frame_bytes)
            # RMS de las muestras s16le -> dBFS (dB relativo a fondo de escala).
            import array

            samples = array.array("h")
            samples.frombytes(data)
            if samples:
                rms = math.sqrt(sum(s * s for s in samples) / len(samples))
                dbfs = 20 * math.log10(rms / 32768) if rms > 0 else -90.0
            else:
                dbfs = -90.0
            yield f"data: {{\"dbfs\": {dbfs:.1f}}}\n\n"
    except (asyncio.IncompleteReadError, asyncio.CancelledError):
        pass
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()


@router.get("/api/mic-level")
async def mic_level(source: str = Query(...)):
    deps.require_local()
    deps.require(bool(_SOURCE_RE.match(source)), "fuente de audio inválida")
    return StreamingResponse(_mic_level_stream(source), media_type="text/event-stream")


@router.post("/api/mic-test/record")
async def mic_test_record(source: str = Query(...), seconds: float = Query(5.0)) -> dict:
    """Graba `seconds` de `source` a un WAV temporal para escucharlo (comparar filtrado vs no)."""
    deps.require_local()
    deps.require(bool(_SOURCE_RE.match(source)), "fuente de audio inválida")
    seconds = max(1.0, min(20.0, seconds))
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _record() -> bool:
        # parec (captura cruda) -> ffmpeg (escribe WAV, corta a -t segundos).
        # Returns True if ffmpeg finished on its own, False if it had to be killed on timeout
        # (a source that produces NO samples — e.g. suspended/absent — leaves ffmpeg blocked on
        # pipe:0 forever, since -t only fires once input PTS reaches `seconds`).
        rec = subprocess.Popen(
            ["parec", "--format=s16le", "--rate=48000", "--channels=2", "-d", source],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        finished = True
        try:
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-f", "s16le", "-ar", "48000", "-ac", "2", "-t", str(seconds),
                 "-i", "pipe:0", "-y", str(MIC_TEST_WAV)],
                stdin=rec.stdout, timeout=seconds + 15, check=False,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run already killed ffmpeg; the source never produced data.
            finished = False
        finally:
            if rec.poll() is None:
                rec.terminate()
                try:
                    rec.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    rec.kill()
        return finished

    finished = await asyncio.to_thread(_record)
    # A real capture is hundreds of KB (48 kHz stereo s16 ≈ 192 KB/s); a header-only/empty WAV
    # means no audio was captured. Fail with an actionable message instead of returning "ok"
    # with an unplayable clip (or a raw 500 traceback on the ffmpeg-hung path).
    ok = finished and MIC_TEST_WAV.exists() and MIC_TEST_WAV.stat().st_size > 2048
    deps.require(
        ok,
        "No se capturó audio de esa fuente. Revisa que el micrófono seleccionado sea el "
        "correcto y esté activo (si usas el 'Mic Limpio', enciende el filtro de ruido).",
    )
    return {"ok": True, "seconds": seconds}


@router.get("/api/mic-test/audio")
def mic_test_audio() -> FileResponse:
    deps.require(MIC_TEST_WAV.exists(), "Aún no hay grabación de prueba.")
    return FileResponse(MIC_TEST_WAV, media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})
