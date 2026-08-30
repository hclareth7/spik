"""Tests for the feedback client reuse + streaming (no network).

Fakes stand in for the Anthropic/Vertex client: streaming returns a final message whose text
is the JSON we parse; a client without ``.stream`` exercises the blocking fallback. The module
level cached client is reset per test.
"""

from __future__ import annotations

import json
import types

import pytest

from spik import feedback

_VALID = {
    "overall_score": 82,
    "summary": "Buen ritmo, cierre claro.",
    "strengths": ["estructura"],
    "improvements": [{"area": "verbal", "issue": "muletillas", "suggestion": "pausa"}],
    "rewrites": [{"original": "eh, bueno", "improved": "En resumen,"}],
    "next_session_goals": ["bajar muletillas"],
}


def _msg(text: str, stop_reason: str = "end_turn", explanation: str = ""):
    """Build a fake final message mirroring the Anthropic SDK Message shape."""
    block = types.SimpleNamespace(type="text", text=text)
    usage = types.SimpleNamespace(input_tokens=120, output_tokens=45)
    stop_details = types.SimpleNamespace(explanation=explanation) if explanation else None
    return types.SimpleNamespace(
        content=[block], stop_reason=stop_reason, stop_details=stop_details, usage=usage,
    )


class _StreamCtx:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class _StreamingClient:
    """Client that supports messages.stream (the normal path)."""

    def __init__(self, message):
        self._message = message
        self.messages = types.SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        return _StreamCtx(self._message)


class _BlockingOnlyClient:
    """Client WITHOUT messages.stream -> must fall back to messages.create."""

    def __init__(self, message):
        self._message = message
        self.create_calls = 0
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.create_calls += 1
        return self._message


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    monkeypatch.setattr(feedback, "_client", None)
    yield


def test_streaming_parses_feedback(monkeypatch):
    client = _StreamingClient(_msg(json.dumps(_VALID)))
    monkeypatch.setattr(feedback, "_make_client", lambda: client)

    fb = feedback.generate("hola mundo", {"language": "es"}, model="claude-opus-5")
    assert fb.overall_score == 82
    assert fb.model == "claude-opus-5"
    assert fb.input_tokens == 120 and fb.output_tokens == 45


def test_client_built_once(monkeypatch):
    client = _StreamingClient(_msg(json.dumps(_VALID)))
    builds = []

    def _make():
        builds.append(1)
        return client

    monkeypatch.setattr(feedback, "_make_client", _make)
    feedback.generate("a", {"language": "es"})
    feedback.generate("b", {"language": "es"})
    assert sum(builds) == 1  # cached across calls


def test_blocking_fallback_when_no_stream(monkeypatch):
    client = _BlockingOnlyClient(_msg(json.dumps(_VALID)))
    monkeypatch.setattr(feedback, "_make_client", lambda: client)

    fb = feedback.generate("hola", {"language": "es"})
    assert fb.overall_score == 82
    assert client.create_calls == 1


def test_refusal_raises(monkeypatch):
    client = _StreamingClient(_msg("", stop_reason="refusal", explanation="no"))
    monkeypatch.setattr(feedback, "_make_client", lambda: client)

    with pytest.raises(RuntimeError, match="refused"):
        feedback.generate("hola", {"language": "es"})
