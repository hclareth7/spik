"""Orchestration of a session's analysis pipeline and report rendering.

Flow (Phase 1): video -> extract audio -> transcribe -> verbal metrics
                 -> (optional) Claude feedback -> save -> report.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import audio, config, store, transcribe, verbal
from .feedback import Feedback
from .verbal import VerbalMetrics

# Progress callback: (stage, fraction 0..1). Used by the GUI for the progress bar.
ProgressCb = Callable[[str, float], None]

# Valid project slug (same criterion as the GUI): letters, digits, hyphen and underscore.
_PROJECT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _project_for(video: Path) -> str:
    """Derive a video's project from its location under DATA_DIR.

    `DATA_DIR/<slug>/<file>` -> `<slug>`; a file directly in the DATA_DIR root (or outside
    it) -> 'default'. Keeps recording and analysis decoupled: there's no need to pass the
    project through the job/SSE, it is inferred from the folder.
    """
    try:
        data_root = config.DATA_DIR.resolve()
        parent = video.resolve().parent
        if parent == data_root:
            return "default"
        if parent.parent == data_root and _PROJECT_RE.match(parent.name):
            return parent.name
    except OSError:
        pass
    return "default"


@dataclass
class SessionResult:
    video_path: Path
    transcript_text: str
    metrics: VerbalMetrics
    feedback: Feedback | None
    session_id: int | None
    # If feedback was skipped due to a provider failure (e.g. missing ANTHROPIC_API_KEY),
    # the reason lands here; the analysis does NOT fail: local metrics are saved anyway.
    feedback_error: str | None = None
    # Nonverbal metrics (Phase 3, local MediaPipe). None if vision is disabled/unavailable.
    vision_metrics: dict | None = None
    # If the vision stage failed (missing [vision] extra, MediaPipe error), the reason lands
    # here; the analysis still completes with verbal metrics + feedback (graceful degradation).
    vision_error: str | None = None


def run_analysis(
    video: Path,
    with_feedback: bool = True,
    whisper_model: str | None = None,
    language: str | None = None,
    keep_audio: bool = False,
    progress_cb: ProgressCb | None = None,
) -> SessionResult:
    """Run the full pipeline on a video and persist the session.

    `progress_cb(stage, fraction)` is optional and lets the GUI show live progress
    (stages: extract -> transcribe -> metrics -> feedback -> save).
    """
    video = Path(video)

    def _p(stage: str, frac: float) -> None:
        if progress_cb:
            progress_cb(stage, frac)

    # 1) Audio (reuse the WAV if it already exists next to the video).
    _p("extract", 0.0)
    wav = video.with_suffix(".wav")
    created_wav = False
    if not wav.exists():
        wav = audio.extract_audio(video)
        created_wav = True
    _p("extract", 1.0)

    try:
        # 2) Transcription with per-word timestamps (chunked+concurrent if long).
        transcript = transcribe.transcribe(
            wav, model_name=whisper_model, language=language, progress_cb=progress_cb,
        )

        # 3) Verbal metrics (pure).
        _p("metrics", 0.0)
        metrics = verbal.analyze(transcript)
        _p("metrics", 1.0)

        # 3b) Nonverbal metrics (optional, local MediaPipe: gestures/expression/posture/gaze).
        # Degrades gracefully like feedback: a missing [vision] extra or a MediaPipe error
        # never breaks the analysis — verbal metrics + feedback are still produced. Frames are
        # read locally and discarded; only numbers persist ("todo local").
        vision_metrics: dict | None = None
        vision_error: str | None = None
        if config.VISION_ENABLED:
            from . import vision  # lazy import (optional cv2/mediapipe)

            _p("vision", 0.0)
            try:
                vision_metrics = vision.analyze(video)
            except Exception as e:  # noqa: BLE001 - degrade to verbal-only, don't break analysis
                vision_error = str(e)
                print(f"[spik] vision skipped (verbal metrics only): {e}", file=sys.stderr)
            _p("vision", 1.0)

        # 4) Feedback (optional; only text/metrics are sent).
        # Degrades gracefully: if the provider is not configured (e.g. a friend without
        # ANTHROPIC_API_KEY) or rejects the request, the analysis is NOT lost — local metrics
        # are saved and the reason is recorded. So "no key = metrics only" (see README).
        fb: Feedback | None = None
        feedback_error: str | None = None
        if with_feedback:
            from . import feedback as feedback_mod  # lazy import (optional SDK)

            _p("feedback", 0.0)
            try:
                fb = feedback_mod.generate(
                    transcript.text, metrics.to_dict(), vision_metrics=vision_metrics,
                )
            except Exception as e:  # noqa: BLE001 - degrade to metrics-only, don't break the analysis
                feedback_error = str(e)
                print(f"[spik] feedback skipped (local metrics only): {e}", file=sys.stderr)
            _p("feedback", 1.0)

        # 5) Persist. Nonverbal metrics ride inside the free-form metrics blob (no DB migration).
        _p("save", 0.0)
        metrics_blob = metrics.to_dict()
        if vision_metrics:
            metrics_blob["nonverbal"] = vision_metrics
        row = store.SessionRow(
            created_at=datetime.now(timezone.utc).isoformat(),
            video_path=str(video),
            language=metrics.language,
            duration_s=metrics.duration_s,
            wpm=metrics.wpm,
            filler_count=metrics.filler_count,
            fillers_per_min=metrics.fillers_per_min,
            overall_score=fb.overall_score if fb else None,
            metrics=metrics_blob,
            feedback=fb.to_dict() if fb else None,
            project=_project_for(video),
        )
        session_id = store.save(row)
        _p("save", 1.0)

        return SessionResult(
            video_path=video,
            transcript_text=transcript.text,
            metrics=metrics,
            feedback=fb,
            session_id=session_id,
            feedback_error=feedback_error,
            vision_metrics=vision_metrics,
            vision_error=vision_error,
        )
    finally:
        if created_wav and not keep_audio and wav.exists():
            wav.unlink()


def render(result: SessionResult) -> None:
    """Print the report to the terminal with rich."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()
    m = result.metrics

    # --- Objective metrics ---
    table = Table(title="Verbal metrics", show_header=True, header_style="bold")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    lo, hi = m.wpm_comfortable
    wpm_note = "✓" if lo <= m.wpm <= hi else f"(comfortable: {lo}-{hi})"
    table.add_row("Language", m.language)
    table.add_row("Duration", f"{m.duration_s:.0f} s")
    table.add_row("Words", str(m.word_count))
    table.add_row("Rate (WPM)", f"{m.wpm:.0f} {wpm_note}")
    table.add_row("Fillers", f"{m.filler_count} ({m.fillers_per_min:.1f}/min)")
    table.add_row("Long pauses", str(m.long_pause_count))
    table.add_row("Silence", f"{m.pause_ratio * 100:.0f}%")
    console.print(table)

    if m.filler_breakdown:
        top = ", ".join(f"{k} ({v})" for k, v in m.filler_breakdown.items())
        console.print(f"[dim]Fillers detected:[/dim] {top}")

    # --- Nonverbal metrics (local, if the vision stage ran) ---
    nv = result.vision_metrics
    if nv:
        nvt = Table(title="Nonverbal metrics (local)", show_header=True, header_style="bold")
        nvt.add_column("Metric")
        nvt.add_column("Value", justify="right")
        nvt.add_row("Face detected", f"{nv['face_detected_ratio'] * 100:.0f}%")
        nvt.add_row("Eye contact (head-frontal proxy)", f"{nv['eye_contact_ratio'] * 100:.0f}%")
        nvt.add_row("Smiling", f"{nv['smile_ratio'] * 100:.0f}%")
        nvt.add_row("Flat affect", f"{nv['flat_affect_ratio'] * 100:.0f}%")
        nvt.add_row("Blink rate", f"{nv['blink_rate_per_min']:.1f}/min")
        nvt.add_row("Head stability", f"{nv['head_stability'] * 100:.0f}%")
        nvt.add_row("Posture upright (proxy)", f"{nv['posture_upright_ratio'] * 100:.0f}%")
        nvt.add_row("Gestures", f"{nv['gesture_rate_per_min']:.1f}/min")
        nvt.add_row("Hands visible", f"{nv['hands_visible_ratio'] * 100:.0f}%")
        console.print(nvt)
        if nv.get("notes"):
            console.print(f"[dim]Notes:[/dim] {', '.join(nv['notes'])}")
    elif result.vision_error:
        console.print(f"\n[yellow]Nonverbal analysis skipped: {result.vision_error}[/yellow]")

    # --- Claude feedback ---
    fb = result.feedback
    if fb is None:
        console.print("\n[yellow]No Claude feedback (use --feedback and configure Vertex in .env).[/yellow]")
        return

    console.print(
        Panel(f"[bold]Score: {fb.overall_score}/100[/bold]\n\n{fb.summary}",
              title=f"Feedback ({fb.model})", border_style="green")
    )

    if fb.strengths:
        console.print("\n[bold green]Strengths[/bold green]")
        for s in fb.strengths:
            console.print(f"  ✓ {s}")

    if fb.improvements:
        console.print("\n[bold yellow]To improve[/bold yellow]")
        for imp in fb.improvements:
            console.print(f"  • [{imp['area']}] {imp['issue']}")
            console.print(f"    [dim]→ {imp['suggestion']}[/dim]")

    if fb.rewrites:
        console.print("\n[bold]Suggested rewrites[/bold]")
        for rw in fb.rewrites:
            console.print(f"  [red]- {rw['original']}[/red]")
            console.print(f"  [green]+ {rw['improved']}[/green]")

    if fb.next_session_goals:
        console.print("\n[bold cyan]Goals for the next session[/bold cyan]")
        for g in fb.next_session_goals:
            console.print(f"  → {g}")

    # --- Usage (only feedback is billed; transcription and metrics are local) ---
    total = fb.input_tokens + fb.output_tokens
    if total:
        cost = fb.cost_usd
        cost_str = f"~${cost:.4f} USD" if cost else "(model price not configured)"
        console.print(
            f"\n[dim]Tokens for this session: {fb.input_tokens} input + "
            f"{fb.output_tokens} output = {total}. Estimated cost on Vertex: {cost_str}. "
            f"(Transcription and metrics: local, $0.)[/dim]"
        )
