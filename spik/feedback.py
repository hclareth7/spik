"""Feedback layer: turns transcript + metrics into actionable coaching with Claude.

Privacy: ONLY text (transcript) and the metrics JSON are sent. Video/audio never
leaves the machine.

Evaluation rubric (encoded in the prompt):
- Pyramid Principle (Minto): main idea first, then the supporting detail.
- STAR method (Situation-Task-Action-Result) for narrative answers.
- Toastmasters-style public-speaking criteria: clarity, pacing, fillers, energy.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass

from . import config

# Schema of the structured response we ask Claude for.
FEEDBACK_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "Overall communication score for this session (0-100).",
        },
        "summary": {"type": "string", "description": "Summary in 2-3 sentences."},
        "strengths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete strengths observed.",
        },
        "improvements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "enum": ["verbal", "prosody", "gestures", "face", "structure"],
                    },
                    "issue": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["area", "issue", "suggestion"],
                "additionalProperties": False,
            },
        },
        "rewrites": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "original": {"type": "string"},
                    "improved": {"type": "string"},
                },
                "required": ["original", "improved"],
                "additionalProperties": False,
            },
            "description": "Rewrite of unclear sentences (Minto Pyramid / STAR).",
        },
        "next_session_goals": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-3 measurable goals for the next recording.",
        },
    },
    "required": [
        "overall_score", "summary", "strengths",
        "improvements", "rewrites", "next_session_goals",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are an expert coach in oral communication and presentations, bilingual (Spanish and English).
You evaluate a person who wants to move from a basic level to a professional/advanced one.

Evaluate using these rubrics:
- Pyramid Principle (Minto): the main idea comes first; then the supporting detail.
- STAR method (Situation, Task, Action, Result) for narrative answers.
- Public-speaking criteria (Toastmasters): clarity, pacing, filler control, energy,
  structure and connection with the audience.

When NONVERBAL metrics are provided (gestures, facial expression, posture, eye-contact
proxy — all measured locally), use them too: emit "gestures" and/or "face" improvements when
the numbers warrant it. Be precise about what they mean: "eye contact" is a HEAD-ORIENTATION
proxy (how often the head faced the camera), "expression" is observable blendshape movement
(smile/brow/blink) and NOT emotion, and "posture" is a shoulder/head heuristic. Frame these as
observable behaviors, never as inferred feelings. If a nonverbal ratio is based on few detected
frames (low face/pose detection), treat it as low-confidence and say so instead of over-claiming.

Be specific, concrete and actionable. Cite examples from the transcript when possible.
Be honest but constructive. Answer in the same language as the transcript.\
"""


@dataclass
class Feedback:
    """Structured feedback of a session."""

    overall_score: int
    summary: str
    strengths: list[str]
    improvements: list[dict]
    rewrites: list[dict]
    next_session_goals: list[str]
    model: str
    # Feedback token usage (the only part that is billed; the rest is local).
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        """Estimated cost of this call in USD, per config.price_for(model)."""
        pin, pout = config.price_for(self.model)
        return (self.input_tokens * pin + self.output_tokens * pout) / 1_000_000

    def to_dict(self) -> dict:
        return {
            "overall_score": self.overall_score,
            "summary": self.summary,
            "strengths": self.strengths,
            "improvements": self.improvements,
            "rewrites": self.rewrites,
            "next_session_goals": self.next_session_goals,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


# Shape of the JSON we request via prompt (native structured_outputs is usually vetoed by the
# organization policy on Vertex for partner models). We instruct it and parse by hand.
_JSON_SHAPE = """\
{
  "overall_score": <integer 0-100>,
  "summary": "<summary in 2-3 sentences>",
  "strengths": ["<strength>", ...],
  "improvements": [
    {"area": "verbal|prosody|gestures|face|structure", "issue": "<what>", "suggestion": "<how>"}
  ],
  "rewrites": [{"original": "<original sentence>", "improved": "<improved sentence>"}],
  "next_session_goals": ["<measurable goal>", ...]
}\
"""


def _build_user_content(
    transcript_text: str, metrics: dict, vision_metrics: dict | None = None,
) -> str:
    """Assemble the user message with metrics + transcript (text only).

    ``vision_metrics`` (nonverbal: gestures/expression/posture/eye-contact, computed locally)
    is included as an extra JSON block when present. Only NUMBERS are sent — never frames.
    """
    nonverbal_block = ""
    if vision_metrics:
        nonverbal_block = (
            "== NONVERBAL METRICS (gestures/face/posture/eye-contact, computed locally) ==\n"
            f"{json.dumps(vision_metrics, ensure_ascii=False, indent=2)}\n\n"
        )
    return (
        "Analyze this communication practice session.\n\n"
        "== OBJECTIVE METRICS (computed locally) ==\n"
        f"{json.dumps(metrics, ensure_ascii=False, indent=2)}\n\n"
        f"{nonverbal_block}"
        "== TRANSCRIPT ==\n"
        f"{transcript_text}\n\n"
        "Give feedback following the rubrics. Prioritize the 2-3 highest-impact changes.\n\n"
        "Respond ONLY with a valid JSON object (no extra text, no markdown blocks "
        "```), with exactly this shape:\n"
        f"{_JSON_SHAPE}"
    )


def _extract_json(text: str) -> dict:
    """Parse the JSON from the response, tolerating markdown fences or wrapping text."""
    text = text.strip()
    if text.startswith("```"):
        # strip ```json ... ``` or ``` ... ```
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # last resort: trim to the first '{' and last '}'
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start : end + 1])
        raise


def _make_client():
    """Build the Claude client according to config.PROVIDER.

    - vertex:    AnthropicVertex, auth via Google ADC (gcloud). No API key.
    - anthropic: Anthropic, auth via ANTHROPIC_API_KEY.
    """
    try:
        import anthropic  # noqa: PLC0415  (optional dependency at import time)
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("anthropic is not installed. Install the project deps.") from e

    if config.PROVIDER == "vertex":
        if not config.VERTEX_PROJECT:
            raise RuntimeError(
                "Missing GCP project. Set SPIK_VERTEX_PROJECT (or GOOGLE_CLOUD_PROJECT) "
                "in .env. Authenticate first with `gcloud auth application-default login`."
            )
        try:
            from anthropic import AnthropicVertex  # noqa: PLC0415
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                'Vertex support not installed. Run: pip install "anthropic[vertex]"'
            ) from e
        return AnthropicVertex(project_id=config.VERTEX_PROJECT, region=config.VERTEX_REGION)

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "Missing ANTHROPIC_API_KEY. Copy .env.example to .env and add your key "
            "(never commit it to git), or use SPIK_PROVIDER=vertex."
        )
    return anthropic.Anthropic()


# Process-wide cached client: the AnthropicVertex/Anthropic handshake (ADC token, HTTP pool)
# is built ONCE and reused across analyses, instead of rebuilt on every feedback call.
_client = None
_client_lock = threading.Lock()


def _get_client():
    """Return the cached Claude client, building it once (double-checked locking)."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = _make_client()
    return _client


def _complete(client, kwargs: dict):
    """Run the completion, streaming when available (avoids request timeouts on long output).

    Falls back to a blocking ``messages.create`` if the SDK/provider has no ``.stream`` — the
    metrics-only graceful degradation in report.py still covers any real streaming error.
    """
    try:
        stream = client.messages.stream
    except AttributeError:  # pragma: no cover - very old SDK without streaming
        return client.messages.create(**kwargs)
    with stream(**kwargs) as s:
        return s.get_final_message()


def generate(
    transcript_text: str,
    metrics: dict,
    model: str | None = None,
    vision_metrics: dict | None = None,
) -> Feedback:
    """Generate feedback with Claude via the configured provider (Vertex by default).

    ``vision_metrics`` (optional nonverbal metrics) is folded into the prompt when present.
    Raises RuntimeError with a clear message if configuration or the SDK is missing.
    """
    model = model or config.CLAUDE_MODEL
    client = _get_client()

    # Note: we don't use structured_outputs or thinking; on Vertex the organization policy
    # (allowedPartnerModelFeatures) usually vetoes those features on partner models. We request
    # the JSON via prompt and parse it, which is 100% compatible.
    response = _complete(client, dict(
        model=model,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": _build_user_content(transcript_text, metrics, vision_metrics),
        }],
    ))

    if response.stop_reason == "refusal":  # pragma: no cover - safeguard
        detail = getattr(response.stop_details, "explanation", "") if response.stop_details else ""
        raise RuntimeError(f"The model refused the request: {detail}")

    text = next(b.text for b in response.content if b.type == "text")
    data = _extract_json(text)

    usage = getattr(response, "usage", None)
    return Feedback(
        model=model,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        **data,
    )
