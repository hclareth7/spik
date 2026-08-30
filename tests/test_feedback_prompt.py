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


def test_user_content_omits_nonverbal_block_when_absent():
    user_content = _build_user_content("hi", {"wpm": 130})
    assert "NONVERBAL METRICS" not in user_content


def test_user_content_includes_nonverbal_block_when_present():
    nv = {"eye_contact_ratio": 0.8, "smile_ratio": 0.42}
    user_content = _build_user_content("hi", {"wpm": 130}, vision_metrics=nv)
    assert "NONVERBAL METRICS" in user_content
    assert "eye_contact_ratio" in user_content
    assert "0.42" in user_content
    # The nonverbal block must precede the transcript.
    assert user_content.index("NONVERBAL METRICS") < user_content.index("== TRANSCRIPT ==")


def test_system_prompt_mentions_nonverbal_areas():
    # The model must know it may emit gestures/face improvements from nonverbal metrics.
    assert "gestures" in SYSTEM_PROMPT
    assert "face" in SYSTEM_PROMPT
