import io
import logging
import os
import wave

import numpy as np

# uvicorn configures this logger at INFO; a module-level logging.getLogger(__name__)
# would silently drop these lines.
logger = logging.getLogger("uvicorn.error")

# Where the speech models live. Under docker-compose this is a named volume
# (MODELS_DIR=/opt/models), not the bind-mounted ./backend: reading these
# 76-460 MB files through the Windows -> VM bind mount has hung the process
# indefinitely (uninterruptible disk wait, never returned) three times in one
# day. A plain local run without MODELS_DIR uses services/weights.
_MODELS_DIR = os.environ.get("MODELS_DIR", os.path.join(os.path.dirname(__file__), "weights"))

# Speech-to-text for the challenge phrase runs on the audio the server actually
# received -- never on a client-supplied transcript, which a caller could simply
# leave empty or fabricate to skip the check.
#
# Two recognizers, cheap one first (see services/phrase_check.py). Measured on
# this 2-core CPU against recordings made in the app's own browser recorder:
#   Vosk (Vietnamese, both model sizes)   passed 5/17 at the 0.6 word-match bar,
#                                         0/9 on the app's raw recordings; wider
#                                         decoder search and a better resampler
#                                         barely moved it, and turning the
#                                         browser's own noise suppression on made
#                                         the audio worse -- so Vosk was dropped
#   Whisper base, beam 5       ~3.2 s, confirmed 14/19 (2 of 7 real submissions)
#   PhoWhisper-base, beam 5    ~3.4 s, confirmed 19/19 -- the fast tier. It is
#                              Whisper fine-tuned on 844 h of Vietnamese (VinAI,
#                              BSD-3-Clause), converted once by
#                              scripts/fetch_phowhisper.py at a pinned revision
#   Whisper small, beam 1      ~10 s, confirmed 19/19 -- the second opinion (a
#                              different model, and Whisper always processes a
#                              30 s window, so clip length barely changes its cost)
# temperature=0 disables faster-whisper's re-decode-at-higher-temperature retries
# and without_timestamps skips timestamp tokens: about 20-30% faster on "small"
# with identical accuracy.
_TIERS = {
    "fast": {"dir": "phowhisper-base-ct2", "fallback": "base", "beam": 5},
    "accurate": {"dir": "faster-whisper-small", "fallback": "small", "beam": 1},
}
_models: dict = {}


def _model_source(tier: str) -> str:
    """The copy in MODELS_DIR when it is there; otherwise the stock Whisper size,
    which faster-whisper downloads into MODELS_DIR, so a fresh clone still works."""
    spec = _TIERS[tier]
    local = os.path.join(_MODELS_DIR, spec["dir"])
    if os.path.isdir(local):
        return local
    if tier == "fast":
        logger.warning(
            "%s not found under %s: falling back to stock Whisper %s, which confirms far fewer "
            "genuine readings on the first pass, so most logins wait for the slow recognizer. "
            "Run scripts/fetch_phowhisper.py once to install PhoWhisper.",
            spec["dir"], _MODELS_DIR, spec["fallback"],
        )
    return spec["fallback"]


def _load(tier: str):
    if tier not in _models:
        from faster_whisper import WhisperModel  # heavy; imported only when first needed
        _models[tier] = WhisperModel(
            _model_source(tier),
            device="cpu", compute_type="int8", cpu_threads=2, download_root=_MODELS_DIR,
        )
    return _models[tier]


def load_models() -> None:
    """Load both models up front (called at startup): otherwise the first sample
    after every restart pays several extra seconds, and a model-file problem would
    surface in the middle of a user's request instead of where it is visible."""
    for tier in _TIERS:
        _load(tier)


def _transcribe(tier: str, wav_bytes: bytes) -> str:
    model = _load(tier)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        pcm16 = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    audio = pcm16.astype(np.float32) / 32768.0
    segments, _ = model.transcribe(
        audio, language="vi", beam_size=_TIERS[tier]["beam"], condition_on_previous_text=False,
        temperature=0.0, without_timestamps=True,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe_fast(wav_bytes: bytes) -> str:
    """Vietnamese speech-to-text on 16kHz mono PCM16 WAV bytes: PhoWhisper-base, ~3 s."""
    return _transcribe("fast", wav_bytes)


def transcribe_accurate(wav_bytes: bytes) -> str:
    """Slower second opinion: Whisper small, ~10 s."""
    return _transcribe("accurate", wav_bytes)
