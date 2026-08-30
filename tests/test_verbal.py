"""Tests de las métricas verbales (código puro, sin dependencias pesadas)."""

from __future__ import annotations

from spik.models import Transcript, Word
from spik.verbal import analyze, detect_fillers


def _words(pairs: list[tuple[str, float, float]]) -> list[Word]:
    return [Word(text=t, start=s, end=e) for t, s, e in pairs]


def test_wpm_and_word_count():
    # 4 palabras en 2 s (de t=0 a t=2) => 4 / (2/60) = 120 WPM
    words = _words([("hola", 0.0, 0.5), ("como", 0.5, 1.0),
                    ("estas", 1.0, 1.5), ("tu", 1.5, 2.0)])
    m = analyze(Transcript(language="es", words=words))
    assert m.word_count == 4
    assert m.duration_s == 2.0
    assert m.wpm == 120.0


def test_detect_fillers_spanish_unigram_and_phrase():
    words = _words([
        ("eh", 0.0, 0.2), ("bueno", 0.2, 0.5),
        ("o", 0.5, 0.6), ("sea", 0.6, 0.8),   # frase "o sea"
        ("hola", 0.8, 1.0),
    ])
    hits = detect_fillers(words, "es")
    phrases = sorted(h.phrase for h in hits)
    assert phrases == ["bueno", "eh", "o sea"]


def test_detect_fillers_english():
    words = _words([
        ("um", 0.0, 0.2), ("you", 0.2, 0.4), ("know", 0.4, 0.6),
        ("hello", 0.6, 0.9),
    ])
    hits = detect_fillers(words, "en")
    assert sorted(h.phrase for h in hits) == ["um", "you know"]


def test_long_pauses_counted():
    # gap de 1.0 s entre la 1ª y la 2ª palabra (> umbral 0.8)
    words = _words([("uno", 0.0, 0.5), ("dos", 1.5, 2.0)])
    m = analyze(Transcript(language="es", words=words))
    assert m.long_pause_count == 1
    assert m.total_pause_s == 1.0


def test_empty_transcript_does_not_crash():
    m = analyze(Transcript(language="es", words=[]))
    assert m.word_count == 0
    assert m.wpm == 0.0
    assert m.filler_count == 0


def test_unsupported_language_returns_no_fillers():
    words = _words([("bonjour", 0.0, 0.5), ("euh", 0.5, 0.8)])
    assert detect_fillers(words, "fr") == []
