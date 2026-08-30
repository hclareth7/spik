"""Tests de las funciones puras del troceado/cosido (sin whisperx ni ffmpeg).

`spik.audio.plan_chunks` y `spik.transcribe._stitch` no dependen de librerías pesadas, así que
se testean directamente. Cubren la lógica crítica de la ruta concurrente para audios largos:
elegir cortes en el silencio y re-desplazar timestamps a tiempo absoluto al coser los trozos.
"""

from __future__ import annotations

import types

from spik.audio import plan_chunks
from spik.transcribe import _run_whisperx, _stitch


# ============================================================================
# _run_whisperx — fuente del modelo de alineación (prioridad align_cache >
# align_loader > carga fresca). Fake de whisperx + modelo, sin dependencias pesadas.
# ============================================================================
def _fake_whisperx():
    """whisperx falso: cuenta cuántas veces se carga un modelo de alineación fresco."""
    wx = types.SimpleNamespace()
    wx.load_align_calls = []

    def load_audio(_path):
        return "audio"

    def load_align_model(language_code, device):
        wx.load_align_calls.append(language_code)
        return (f"fresh:{language_code}", {"src": "fresh"})

    def align(segments, align_model, meta, audio_data, device, return_char_alignments=False):
        # Devuelve una palabra que codifica qué modelo de alineación se usó.
        return {"segments": [{"text": "hola", "words": [
            {"word": align_model, "start": 0.0, "end": 0.5},
        ]}]}

    wx.load_audio = load_audio
    wx.load_align_model = load_align_model
    wx.align = align
    return wx


def _fake_model(lang="es"):
    """Modelo ASR falso: transcribe() devuelve un segmento y el idioma detectado."""
    def transcribe(audio_data, batch_size=None, language=None):
        return {"language": language or lang, "segments": [{"text": "hola"}]}
    return types.SimpleNamespace(transcribe=transcribe)


def test_run_whisperx_prefers_align_loader_over_fresh_load():
    wx = _fake_whisperx()
    loader_calls = []

    def align_loader(lang):
        loader_calls.append(lang)
        return (f"warm:{lang}", {"src": "warm"})

    words, text, lang = _run_whisperx(
        wx, "x.wav", "medium", "es", threads=4, batch_size=8,
        model=_fake_model("es"), align_loader=align_loader,
    )
    assert lang == "es"
    assert loader_calls == ["es"]           # se usó el loader cacheado
    assert wx.load_align_calls == []        # NO se cargó un modelo fresco
    assert words[0][0] == "warm:es"         # la alineación usó el modelo del loader


def test_run_whisperx_align_cache_takes_priority_and_populates():
    wx = _fake_whisperx()
    cache = {}
    # 1ª llamada: la caché está vacía -> carga fresca y la puebla.
    _run_whisperx(wx, "x.wav", None, "en", threads=None, batch_size=8,
                  model=_fake_model("en"), align_cache=cache)
    assert wx.load_align_calls == ["en"]
    assert "en" in cache
    # 2ª llamada: la caché ya tiene "en" -> no vuelve a cargar.
    words, _, _ = _run_whisperx(wx, "x.wav", None, "en", threads=None, batch_size=8,
                                model=_fake_model("en"), align_cache=cache)
    assert wx.load_align_calls == ["en"]    # sin cargas adicionales
    assert words[0][0] == "fresh:en"


# ============================================================================
# plan_chunks — elección de puntos de corte (pura)
# ============================================================================
def test_plan_chunks_empty_for_nonpositive_duration():
    assert plan_chunks(0.0, [], 600) == []
    assert plan_chunks(-5.0, [], 600) == []


def test_plan_chunks_single_when_shorter_than_target():
    # Audio más corto que el objetivo -> un solo trozo, sin overhead de partir.
    assert plan_chunks(500.0, [], 600) == [(0.0, 500.0)]
    assert plan_chunks(600.0, [], 600) == [(0.0, 600.0)]


def test_plan_chunks_falls_back_to_exact_boundaries_without_silence():
    # Sin silencios cerca, corta en las fronteras exactas (target, 2·target, ...).
    chunks = plan_chunks(1800.0, [], 600)
    assert chunks == [(0.0, 600.0), (600.0, 1200.0), (1200.0, 1800.0)]


def test_plan_chunks_absorbs_small_tail_into_last_chunk():
    # Con una cola < tolerancia (target/2) no crea un trozo diminuto: el último lo absorbe
    # (aquí 1500 con target 600 -> dos trozos, el último de 900 s, no tres).
    chunks = plan_chunks(1500.0, [], 600)
    assert chunks == [(0.0, 600.0), (600.0, 1500.0)]


def test_plan_chunks_cuts_at_nearest_silence_midpoint():
    # Silencio [610, 620] -> punto medio 615, dentro de ±300 de la frontera 600 -> corta en 615.
    chunks = plan_chunks(1200.0, [(610.0, 620.0)], 600)
    assert chunks == [(0.0, 615.0), (615.0, 1200.0)]


def test_plan_chunks_ignores_silence_outside_tolerance():
    # Silencio en 100 (mid=100) está a >300 de la frontera 600 -> se ignora, corta en 600.
    chunks = plan_chunks(1200.0, [(95.0, 105.0)], 600)
    assert chunks == [(0.0, 600.0), (600.0, 1200.0)]


def test_plan_chunks_are_contiguous_and_cover_full_duration():
    duration = 4000.0
    silences = [(590.0, 600.0), (1250.0, 1260.0), (1790.0, 1810.0)]
    chunks = plan_chunks(duration, silences, 600)
    # Contiguos: el fin de cada trozo es el inicio del siguiente.
    for (_, end), (nxt_start, _) in zip(chunks, chunks[1:]):
        assert end == nxt_start
    # Cubren [0, duration] y son monótonos crecientes.
    assert chunks[0][0] == 0.0
    assert chunks[-1][1] == duration
    for start, end in chunks:
        assert end > start


# ============================================================================
# _stitch — re-desplaza timestamps a tiempo absoluto y concatena (pura)
# ============================================================================
def test_stitch_applies_offsets_to_absolute_time():
    chunk0 = ([("hola", 0.0, 0.5), ("mundo", 0.5, 1.0)], "hola mundo", "es")
    chunk1 = ([("otra", 0.0, 0.4), ("vez", 0.4, 0.9)], "otra vez", "es")
    t = _stitch([chunk0, chunk1], offsets=[0.0, 600.0])

    assert [w.text for w in t.words] == ["hola", "mundo", "otra", "vez"]
    # El 2º trozo arranca en el offset 600.
    assert t.words[2].start == 600.0
    assert t.words[2].end == 600.4
    assert t.words[3].end == 600.9
    # Timestamps monótonos crecientes tras coser.
    starts = [w.start for w in t.words]
    assert starts == sorted(starts)


def test_stitch_concatenates_text_in_order():
    chunks = [([], "primera parte", "es"), ([], "segunda parte", "es")]
    t = _stitch(chunks, offsets=[0.0, 600.0])
    assert t.text == "primera parte segunda parte"


def test_stitch_language_is_majority_vote():
    chunks = [([], "a", "es"), ([], "b", "en"), ([], "c", "es")]
    t = _stitch(chunks, offsets=[0.0, 600.0, 1200.0])
    assert t.language == "es"


def test_stitch_empty_yields_empty_transcript():
    t = _stitch([], offsets=[])
    assert t.words == []
    assert t.text == ""
    assert t.language == "en"  # default sensato cuando no hay nada que votar
