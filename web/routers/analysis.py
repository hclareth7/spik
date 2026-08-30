"""Background analysis jobs (reuse the existing pipeline) with SSE progress."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from web import deps, state

router = APIRouter()


async def _run_analysis_job(job_id: str, path: Path, feedback: bool) -> None:
    from spik import report  # import perezoso (WhisperX es pesado)

    job = state.jobs.jobs[job_id]

    def cb(stage: str, frac: float) -> None:
        # Llamado desde el hilo de trabajo; escribir en el dict es seguro para nuestro uso.
        job["stage"] = stage
        job["pct"] = state.jobs.global_pct(stage, frac)

    try:
        result = await asyncio.to_thread(
            report.run_analysis, path, feedback, None, None, False, cb,
        )
        fb = result.feedback
        job["result"] = {
            "session_id": result.session_id,
            "transcript": result.transcript_text,
            "metrics": result.metrics.to_dict(),
            "feedback": fb.to_dict() if fb else None,
            "feedback_error": result.feedback_error,
        }
        job["status"] = "done"
        job["pct"] = 100.0
    except Exception as e:  # noqa: BLE001 - el detalle se reporta al frontend
        job["status"] = "error"
        job["error"] = str(e)


@router.post("/api/analyze")
async def analyze(video: str = Query(...), feedback: bool = Query(True)) -> dict:
    """Arranca el análisis en segundo plano y devuelve un job_id (no bloquea)."""
    path = Path(video)
    deps.require(path.exists(), f"No existe el archivo: {video}")
    deps.require(not state.jobs.is_active(), "Ya hay un análisis en curso. Espera a que termine.")

    import uuid  # noqa: PLC0415

    job_id = uuid.uuid4().hex[:12]
    state.jobs.jobs[job_id] = {"status": "running", "stage": "extract", "pct": 0.0,
                               "result": None, "error": None}
    asyncio.create_task(_run_analysis_job(job_id, path, feedback))
    return {"job_id": job_id}


async def _job_event_stream(job_id: str):
    """Emite el progreso del job por SSE hasta que termine (o falle)."""
    while True:
        job = state.jobs.jobs.get(job_id)
        if job is None:
            yield f"data: {json.dumps({'status': 'error', 'error': 'job desconocido'})}\n\n"
            return
        payload = {"status": job["status"], "stage": job["stage"], "pct": job["pct"]}
        if job["status"] == "done":
            payload["result"] = job["result"]
            yield f"data: {json.dumps(payload)}\n\n"
            return
        if job["status"] == "error":
            payload["error"] = job["error"]
            yield f"data: {json.dumps(payload)}\n\n"
            return
        yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(0.5)


@router.get("/api/analyze/events/{job_id}")
async def analyze_events(job_id: str):
    return StreamingResponse(_job_event_stream(job_id), media_type="text/event-stream")


@router.get("/api/analyze/result/{job_id}")
def analyze_result(job_id: str) -> dict:
    """Resultado final del job (para reconexión o fallback por polling)."""
    job = state.jobs.jobs.get(job_id)
    deps.require(job is not None, "job desconocido")
    return {"status": job["status"], "stage": job["stage"], "pct": job["pct"],
            "result": job["result"], "error": job["error"]}
