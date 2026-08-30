"""Prosody (voice): pitch, energy, monotony. — Phase 2 (stub).

To be implemented with praat-parselmouth + librosa:
- pitch/F0 and its variation -> detect monotony
- intensity/energy
- tempo and silences

Install the deps with:  pip install -e ".[prosody]"
"""

from __future__ import annotations

from pathlib import Path


def analyze(audio: Path) -> dict:  # pragma: no cover - Phase 2 stub
    """Placeholder. Returns an empty dict until Phase 2 is implemented."""
    raise NotImplementedError("Prosody is implemented in Phase 2 (see plan).")
