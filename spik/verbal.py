"""Verbal metrics: fillers, rate (WPM) and pauses.

Pure code (no heavy dependencies) -> easy to test with synthetic transcripts.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field

from . import config
from .models import Transcript, Word


@dataclass
class FillerHit:
    """A detected filler."""

    phrase: str
    start: float


@dataclass
class VerbalMetrics:
    """Verbal metrics of a session."""

    language: str
    word_count: int
    duration_s: float
    wpm: float                      # words per minute
    filler_count: int
    fillers_per_min: float
    filler_breakdown: dict[str, int]
    long_pause_count: int
    total_pause_s: float
    pause_ratio: float              # silence / total duration (0..1)
    wpm_comfortable: tuple[int, int]

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize(text: str) -> str:
    """lowercase, no accents, no punctuation -> for comparing against dictionaries."""
    text = text.lower().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^\w¿?]", "", text)


def detect_fillers(words: list[Word], language: str) -> list[FillerHit]:
    """Detect single- and multi-word fillers over the token sequence."""
    unigrams, phrases = config.fillers_for(language)
    norm = [_normalize(w.text) for w in words]
    hits: list[FillerHit] = []
    used: set[int] = set()  # indices already consumed by a phrase (avoids double counting)

    # Phrases (multi-token) first, from longest to shortest.
    for phrase in sorted(phrases, key=len, reverse=True):
        plen = len(phrase)
        target = tuple(_normalize(p) for p in phrase)
        for i in range(len(norm) - plen + 1):
            if any(j in used for j in range(i, i + plen)):
                continue
            if tuple(norm[i : i + plen]) == target:
                hits.append(FillerHit(phrase=" ".join(phrase), start=words[i].start))
                used.update(range(i, i + plen))

    # Unigrams.
    for i, tok in enumerate(norm):
        if i in used:
            continue
        if tok in unigrams:
            hits.append(FillerHit(phrase=tok, start=words[i].start))

    hits.sort(key=lambda h: h.start)
    return hits


def analyze_pauses(words: list[Word]) -> tuple[int, float]:
    """Return (number of long pauses, total silence in s) between words."""
    long_pauses = 0
    total_silence = 0.0
    for prev, cur in zip(words, words[1:]):
        gap = cur.start - prev.end
        if gap > 0:
            total_silence += gap
            if gap >= config.LONG_PAUSE_S:
                long_pauses += 1
    return long_pauses, total_silence


def analyze(transcript: Transcript) -> VerbalMetrics:
    """Compute all verbal metrics of a transcript."""
    words = transcript.words
    duration = transcript.duration
    minutes = duration / 60.0 if duration > 0 else 0.0

    hits = detect_fillers(words, transcript.language)
    breakdown: dict[str, int] = {}
    for h in hits:
        breakdown[h.phrase] = breakdown.get(h.phrase, 0) + 1

    long_pauses, total_silence = analyze_pauses(words)

    return VerbalMetrics(
        language=transcript.language,
        word_count=len(words),
        duration_s=round(duration, 2),
        wpm=round(len(words) / minutes, 1) if minutes > 0 else 0.0,
        filler_count=len(hits),
        fillers_per_min=round(len(hits) / minutes, 2) if minutes > 0 else 0.0,
        filler_breakdown=dict(sorted(breakdown.items(), key=lambda kv: -kv[1])),
        long_pause_count=long_pauses,
        total_pause_s=round(total_silence, 2),
        pause_ratio=round(total_silence / duration, 3) if duration > 0 else 0.0,
        wpm_comfortable=config.WPM_COMFORTABLE,
    )
