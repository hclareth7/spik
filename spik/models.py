"""Data types shared across modules.

Simple dataclasses are used so metrics are easy to test without depending on the
heavy libraries (Whisper, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Word:
    """A word with its timestamps (in seconds)."""

    text: str
    start: float
    end: float


@dataclass
class Transcript:
    """Transcription result, normalized and backend-independent."""

    language: str
    words: list[Word] = field(default_factory=list)
    text: str = ""

    @property
    def duration(self) -> float:
        """Speech duration: from the start of the 1st word to the end of the last."""
        if not self.words:
            return 0.0
        return max(w.end for w in self.words) - min(w.start for w in self.words)
