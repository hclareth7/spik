"""Tests for wiring the nonverbal (vision) stage into the analysis pipeline.

The heavy stages (audio extraction, WhisperX transcription) are monkeypatched; ``vision.analyze``
is monkeypatched to a dict (or to raise). We assert that the nonverbal metrics are merged into
the stored/returned metrics blob, threaded into the feedback call, and that a vision failure
degrades gracefully to verbal-only with ``vision_error`` set. No cv2/mediapipe/network.
"""

from __future__ import annotations

import pytest

from spik import config, feedback, report, vision
from spik.models import Transcript, Word


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """A ready-to-run pipeline pointed at tmp, with the heavy stages faked."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "sessions.db")
    monkeypatch.setattr(config, "VISION_ENABLED", True)

    video = tmp_path / "clip.mkv"
    video.write_bytes(b"x")
    # Pre-create the WAV so run_analysis skips audio extraction (and won't unlink it).
    video.with_suffix(".wav").write_bytes(b"x")

    fake_transcript = Transcript(
        language="en",
        words=[Word("hello", 0.0, 0.5), Word("world", 0.6, 1.2)],
        text="hello world",
    )
    monkeypatch.setattr(report.transcribe, "transcribe", lambda *a, **k: fake_transcript)
    return video


def test_vision_metrics_merged_and_passed_to_feedback(pipeline, monkeypatch):
    nonverbal = {"eye_contact_ratio": 0.8, "smile_ratio": 0.5, "notes": []}
    monkeypatch.setattr(vision, "analyze", lambda v: nonverbal)

    captured = {}

    def fake_generate(text, metrics, model=None, vision_metrics=None):
        captured["vision_metrics"] = vision_metrics
        return feedback.Feedback(
            overall_score=80, summary="ok", strengths=[], improvements=[],
            rewrites=[], next_session_goals=[], model="test-model",
        )

    monkeypatch.setattr(feedback, "generate", fake_generate)

    result = report.run_analysis(pipeline, with_feedback=True)

    # Threaded into feedback.
    assert captured["vision_metrics"] == nonverbal
    # Present on the result and merged into the metrics blob.
    assert result.vision_metrics == nonverbal
    assert result.vision_error is None
    blob = result.metrics.to_dict()
    blob_with_nv = {**blob, "nonverbal": nonverbal}
    # Verify persistence merged it (history reads it the same way).
    from spik import store
    rows = store.history()
    assert rows and "nonverbal" in rows[0]["metrics_json"]
    assert result.metrics.language == "en"
    # Sanity: the merged blob is what the router would emit.
    assert blob_with_nv["nonverbal"]["smile_ratio"] == 0.5


def test_vision_failure_degrades_to_verbal_only(pipeline, monkeypatch):
    def _boom(video):
        raise RuntimeError("mediapipe not installed")

    monkeypatch.setattr(vision, "analyze", _boom)

    result = report.run_analysis(pipeline, with_feedback=False)

    # Analysis still completed with verbal metrics; vision error surfaced, not raised.
    assert result.vision_metrics is None
    assert "mediapipe not installed" in result.vision_error
    assert result.metrics.word_count == 2
    from spik import store
    rows = store.history()
    assert rows and "nonverbal" not in rows[0]["metrics_json"]


def test_vision_disabled_skips_stage(pipeline, monkeypatch):
    monkeypatch.setattr(config, "VISION_ENABLED", False)
    called = {"n": 0}
    monkeypatch.setattr(vision, "analyze", lambda v: called.__setitem__("n", called["n"] + 1))

    result = report.run_analysis(pipeline, with_feedback=False)

    assert called["n"] == 0
    assert result.vision_metrics is None
    assert result.vision_error is None
