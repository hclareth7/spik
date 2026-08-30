"""spik CLI. Usage: `spik analyze data/my-video.mkv`."""

from __future__ import annotations

from pathlib import Path

import typer
from dotenv import load_dotenv

app = typer.Typer(help="Personal communication coach from self-recordings.")

load_dotenv()  # load ANTHROPIC_API_KEY and overrides from .env


@app.command()
def analyze(
    video: Path = typer.Argument(..., exists=True, help="Path to the recorded video."),
    feedback: bool = typer.Option(True, help="Generate feedback with Claude (requires API key)."),
    model: str = typer.Option(None, "--whisper-model", help="Whisper model (small/medium/large-v3)."),
    language: str = typer.Option(None, "--language", help="Force language (es/en). Auto-detects if omitted."),
    keep_audio: bool = typer.Option(False, help="Keep the extracted WAV."),
) -> None:
    """Analyze a recording and show the report."""
    from . import report

    typer.echo(f"Analyzing {video} ...")
    result = report.run_analysis(
        video,
        with_feedback=feedback,
        whisper_model=model,
        language=language,
        keep_audio=keep_audio,
    )
    report.render(result)
    typer.echo(f"\nSession saved (id={result.session_id}).")


@app.command()
def demo() -> None:
    """Show a sample report (no camera or Vertex) to see the format."""
    from .models import Transcript, Word
    from .report import SessionResult, render
    from .verbal import analyze

    # Synthetic transcript: ~30 s speaking in Spanish, with fillers and one long pause.
    raw = (
        "bueno eh hola a todos este hoy les voy a hablar sobre o sea el proyecto "
        "en el que eh estuve trabajando digamos durante las ultimas semanas y "
        "bueno la verdad es que este fue un reto muy interesante"
    )
    tokens = raw.split()
    words, t = [], 0.5
    for tok in tokens:
        dur = 0.28
        words.append(Word(text=tok, start=round(t, 2), end=round(t + dur, 2)))
        gap = 1.2 if tok == "digamos" else 0.12  # a long pause after "digamos"
        t += dur + gap

    metrics = analyze(Transcript(language="es", words=words, text=raw))
    result = SessionResult(
        video_path=Path("demo/ejemplo.mkv"),
        transcript_text=raw,
        metrics=metrics,
        feedback=None,
        session_id=None,
    )
    typer.echo("── Sample report (synthetic data) ──\n")
    render(result)
    typer.echo(
        "\n[demo] With a real recording and Vertex configured, Claude's feedback would also "
        "appear here (score, improvements, rewrites, goals)."
    )


@app.command()
def history(limit: int = typer.Option(20, help="Number of sessions to show.")) -> None:
    """Show the session history (progress trend)."""
    from rich.console import Console
    from rich.table import Table

    from . import store

    rows = store.history(limit=limit)
    if not rows:
        typer.echo("No sessions yet. Run `spik analyze <video>`.")
        raise typer.Exit()

    import json

    table = Table(title="Session history", header_style="bold")
    for col in ("Date", "Language", "Dur(s)", "WPM", "Fillers/min", "Score", "Cost(USD)"):
        table.add_column(col)
    total_cost = 0.0
    for r in reversed(rows):  # chronological ascending to see the trend
        cost = None
        if r.get("feedback_json"):
            try:
                cost = json.loads(r["feedback_json"]).get("cost_usd")
            except (json.JSONDecodeError, AttributeError):
                cost = None
        if cost:
            total_cost += cost
        table.add_row(
            r["created_at"][:19],
            r["language"] or "-",
            f"{r['duration_s']:.0f}" if r["duration_s"] else "-",
            f"{r['wpm']:.0f}" if r["wpm"] else "-",
            f"{r['fillers_per_min']:.1f}" if r["fillers_per_min"] is not None else "-",
            str(r["overall_score"]) if r["overall_score"] is not None else "-",
            f"${cost:.4f}" if cost else "-",
        )
    console = Console()
    console.print(table)
    console.print(f"[dim]Total accumulated cost (feedback on Vertex): ~${total_cost:.4f} USD. "
                  f"Transcription and metrics: local, $0.[/dim]")


if __name__ == "__main__":
    app()
