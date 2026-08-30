"""Prompt-building tests for the feedback layer (no network).

Guards against a bilingual-output regression: the prompts are written in English, but the
model MUST be instructed to answer in the transcript's language so es->es / en->en behavior
is preserved.
"""

from __future__ import annotations

from spik.feedback import SYSTEM_PROMPT, _build_user_content

_SAME_LANGUAGE = "same language as the transcript"


def test_system_prompt_keeps_same_language_instruction():
    assert _SAME_LANGUAGE in SYSTEM_PROMPT


def test_built_prompt_contains_same_language_instruction():
    # The instruction must survive in the material actually sent to the model
    # (system prompt and/or user content).
    user_content = _build_user_content("hello world", {"language": "en", "wpm": 130})
    combined = SYSTEM_PROMPT + "\n" + user_content
    assert _SAME_LANGUAGE in combined


def test_user_content_includes_metrics_and_transcript():
    user_content = _build_user_content("this is the transcript", {"wpm": 145})
    assert "== TRANSCRIPT ==" in user_content
    assert "this is the transcript" in user_content
    assert "145" in user_content
