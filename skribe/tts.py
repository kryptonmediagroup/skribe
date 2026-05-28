"""KittenTTS text-to-speech: voice catalog + cached model loader.

KittenTTS (nano 0.1) is a tiny, CPU-only ONNX model. The model weights and
voice embeddings download from Hugging Face on first use and are cached by
``huggingface_hub`` thereafter. The espeak-ng shared library bundled by
``espeakng_loader`` is wired into phonemizer here, so synthesis does not
depend on a system espeak-ng install.

Playback and threading live in main_window; this module only turns text
into audio samples.
"""
from __future__ import annotations

import re
import sys
import threading
from typing import Optional

# (voice id, human-facing label). The ids are KittenTTS's internal voice
# names; the labels are what Skribe shows in Preferences.
VOICES: list[tuple[str, str]] = [
    ("expr-voice-2-f", "Voice 2 — Female"),
    ("expr-voice-2-m", "Voice 2 — Male"),
    ("expr-voice-3-f", "Voice 3 — Female"),
    ("expr-voice-3-m", "Voice 3 — Male"),
    ("expr-voice-4-f", "Voice 4 — Female"),
    ("expr-voice-4-m", "Voice 4 — Male"),
    ("expr-voice-5-f", "Voice 5 — Female"),
    ("expr-voice-5-m", "Voice 5 — Male"),
]
VOICE_IDS = frozenset(vid for vid, _ in VOICES)
DEFAULT_VOICE = "expr-voice-2-f"
SAMPLE_RATE = 24000

# The model's native rate (speed 1.0) reads a touch slow, so the user's speed
# setting is applied on top of this slightly faster baseline: a setting of 1.0
# renders at _BASELINE_SPEED.
_BASELINE_SPEED = 1.08

# The nano model's text encoder caps out around 512 phoneme tokens; past
# that, onnxruntime raises "invalid expand shape". We chunk text well under
# that. Budgeting by characters is approximate (phoneme density varies, and
# num2words can expand a short number into many phonemes), so _generate_safe
# halves any chunk that still overruns. ~300 chars keeps normal prose near
# ~390 tokens — a comfortable margin.
_MAX_CHARS = 300
# The opening chunk is kept small so the first audio starts as soon as
# possible when streaming; later chunks use the full budget.
_FIRST_MAX_CHARS = 120
# Trailing silence appended to the final chunk so playback fades out instead
# of cutting off on the last phoneme.
_TAIL_SECONDS = 0.5

# KittenTTS.generate() slices its raw output [5000:-10000]. The -10000
# (~0.42s) is aggressive and eats the natural decay of the final word, which
# sounds like an abrupt cutoff. We do the slice ourselves and keep more of
# the tail — trimming only enough to drop the end-of-utterance artifact.
_HEAD_TRIM = 5000
_TAIL_TRIM = 5000

_model = None
_lock = threading.Lock()
_load_failed = False


def is_available() -> bool:
    """True if the KittenTTS package can be imported on this machine."""
    try:
        import kittentts  # noqa: F401
        return True
    except Exception:
        return False


def load_model():
    """Load and cache the KittenTTS model. Returns the model or None.

    Thread-safe and lazy: the first caller pays the cost (a few seconds,
    plus a one-time HF download); later callers reuse the cached session,
    which onnxruntime allows to be shared across threads. A failed load is
    remembered so we don't retry the heavy import on every read.
    """
    global _model, _load_failed
    with _lock:
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            import espeakng_loader
            from phonemizer.backend.espeak.wrapper import EspeakWrapper

            EspeakWrapper.set_library(espeakng_loader.get_library_path())
            try:
                EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
            except Exception:
                import os
                os.environ.setdefault("ESPEAK_DATA_PATH", espeakng_loader.get_data_path())

            from kittentts import KittenTTS

            _model = KittenTTS()
            return _model
        except Exception as exc:
            print(f"KittenTTS unavailable: {exc}", file=sys.stderr)
            _load_failed = True
            return None


def _wrap_words(s: str, max_chars: int) -> list[str]:
    """Split ``s`` on whitespace into pieces no longer than ``max_chars``."""
    out: list[str] = []
    cur = ""
    for word in s.split():
        if cur and len(cur) + 1 + len(word) > max_chars:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        out.append(cur)
    return out


def _chunk_text(
    text: str,
    max_chars: int = _MAX_CHARS,
    first_max_chars: int = _FIRST_MAX_CHARS,
) -> list[str]:
    """Break ``text`` into chunks on natural boundaries.

    Splits first on line breaks, then on sentence enders, then (only for an
    overlong single sentence) on whitespace, finally packing the resulting
    units greedily. The first chunk is held under ``first_max_chars`` (so a
    streaming caller can start audio sooner) and the rest under ``max_chars``.
    """
    units: list[str] = []
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        for sentence in re.split(r"(?<=[.!?…])\s+", line):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > max_chars:
                units.extend(_wrap_words(sentence, max_chars))
            else:
                units.append(sentence)

    chunks: list[str] = []
    cur = ""
    for unit in units:
        budget = first_max_chars if not chunks else max_chars
        if cur and len(cur) + 1 + len(unit) > budget:
            chunks.append(cur)
            cur = unit
        else:
            cur = f"{cur} {unit}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def _model_generate(model, text: str, voice: str, speed: float):
    """Run the model like KittenTTS.generate, but trim less off the tail.

    Reaches into the pinned kittentts==0.1.0 internals (_phonemizer,
    _word_index_dictionary, _session, _voices) so we can keep the final
    word's decay that the bundled generate() would discard. Revisit if the
    kittentts version changes.
    """
    import numpy as np

    phonemes = model._phonemizer.phonemize([text])[0]
    symbols = " ".join(re.findall(r"\w+|[^\w\s]", phonemes))
    ids = (
        [0]
        + [model._word_index_dictionary[c] for c in symbols if c in model._word_index_dictionary]
        + [0]
    )
    out = model._session.run(
        None,
        {
            "input_ids": np.array([ids], dtype=np.int64),
            "style": model._voices[voice],
            "speed": np.array([speed], dtype=np.float32),
        },
    )[0]
    return out[_HEAD_TRIM:-_TAIL_TRIM] if _TAIL_TRIM else out[_HEAD_TRIM:]


def _generate_safe(model, text: str, voice: str, speed: float, depth: int = 0):
    """_model_generate with a recursive split fallback for overlong chunks.

    A chunk under _MAX_CHARS can still exceed the model's token limit if its
    phonemes are unusually dense (e.g. spelled-out numbers). On failure we
    halve the chunk on whitespace and retry each half, up to a few levels.
    """
    try:
        return _model_generate(model, text, voice, float(speed))
    except Exception as exc:
        words = text.split()
        if depth >= 5 or len(words) < 2:
            print(f"TTS chunk failed: {exc}", file=sys.stderr)
            return None
        import numpy as np

        mid = len(words) // 2
        left = _generate_safe(model, " ".join(words[:mid]), voice, speed, depth + 1)
        right = _generate_safe(model, " ".join(words[mid:]), voice, speed, depth + 1)
        parts = [p for p in (left, right) if p is not None and len(p)]
        if not parts:
            return None
        return np.concatenate(parts)


def chunk_text(text: str) -> list[str]:
    """Split ``text`` into model-sized chunks on natural boundaries.

    Public wrapper around the chunker; callers that want to stream audio
    chunk-by-chunk (synthesizing one while the previous plays) iterate this
    and feed each piece to synthesize_chunk.
    """
    return _chunk_text(text)


def synthesize_chunk(text: str, voice: str, speed: float) -> Optional["object"]:
    """Synthesize one already-chunked piece to a numpy float32 array, or None.

    ``text`` is expected to be within the model's token limit (use
    chunk_text); _generate_safe still guards the rare dense-phoneme overrun.
    Falls back to DEFAULT_VOICE if ``voice`` isn't a known id.
    """
    model = load_model()
    if model is None:
        return None
    if voice not in VOICE_IDS:
        voice = DEFAULT_VOICE
    return _generate_safe(model, text, voice, speed * _BASELINE_SPEED)


def pad_tail(audio, seconds: float = _TAIL_SECONDS):
    """Append ``seconds`` of silence to a synthesized clip.

    Used on the last chunk so playback fades to silence instead of cutting
    off abruptly on the final phoneme.
    """
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32)
    tail = np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)
    return np.concatenate([arr, tail])
