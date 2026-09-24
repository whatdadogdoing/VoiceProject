import io
import os
import wave

import numpy as np

# Where the speech models live. Under docker-compose this is a named volume
# (MODELS_DIR=/opt/models), not the bind-mounted ./backend: reading these
# 140-460 MB files through the Windows -> VM bind mount has hung the process
# indefinitely (uninterruptible disk wait, never returned) three times in one
# day. A plain local run without MODELS_DIR uses services/weights.
_MODELS_DIR = os.environ.get("MODELS_DIR", os.path.join(os.path.dirname(__file__), "weights"))

# Speech-to-text for the challenge phrase runs on the audio the server actually
# received -- never on a client-supplied transcript, which a caller could simply
# leave empty or fabricate to skip the check.
#
# Two Whisper sizes, cheap one first (see services/phrase_check.py). Measured on
# this 2-core CPU against recordings made in the app's own browser recorder:
#   Vosk (Vietnamese, both model sizes)   passed 5/17 at the 0.6 word-match bar,
#                                         0/9 on the app's raw recordings; wider
#                                         decoder search and a better resampler
#                                         barely moved it, and turning the
#                                         browser's own noise suppression on made
#                                         the audio worse -- so Vosk was dropped
#   Whisper base, beam 5   ~3 s per clip, passed 12/15
#   Whisper small, beam 1  ~10 s per clip, passed ~all (Whisper always processes
#                          a 30 s window, so clip length barely changes its cost)
# temperature=0 disables faster-whisper's re-decode-at-higher-temperature retries
# and without_timestamps skips timestamp tokens: about 20-30% faster on "small"
# with identical accuracy.
_BEAM = {"base": 5, "small": 1}
_models: dict = {}


def _load(size: str):
    if size not in _models:
        from faster_whisper import WhisperModel  # heavy; imported only when first needed
        # Use the copy in MODELS_DIR when it is there; otherwise let faster-whisper
        # download it into MODELS_DIR, so a fresh clone still works.
        local = os.path.join(_MODELS_DIR, f"faster-whisper-{size}")
        _models[size] = WhisperModel(
            local if os.path.isdir(local) else size,
            device="cpu", compute_type="int8", cpu_threads=2, download_root=_MODELS_DIR,
        )
    return _models[size]


def load_models() -> None:
    """Load both models up front (called at startup): otherwise the first sample
    after every restart pays several extra seconds, and a model-file problem would
    surface in the middle of a user's request instead of where it is visible."""
    for size in _BEAM:
        _load(size)


def _transcribe(size: str, wav_bytes: bytes) -> str:
    model = _load(size)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        pcm16 = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    audio = pcm16.astype(np.float32) / 32768.0
    segments, _ = model.transcribe(
        audio, language="vi", beam_size=_BEAM[size], condition_on_previous_text=False,
        temperature=0.0, without_timestamps=True,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe_fast(wav_bytes: bytes) -> str:
    """Vietnamese speech-to-text on 16kHz mono PCM16 WAV bytes: Whisper base, ~3 s."""
    return _transcribe("base", wav_bytes)


def transcribe_accurate(wav_bytes: bytes) -> str:
    """Slower, more accurate second opinion: Whisper small, ~10 s."""
    return _transcribe("small", wav_bytes)
