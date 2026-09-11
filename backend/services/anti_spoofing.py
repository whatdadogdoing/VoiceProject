import io
import os
import wave

import numpy as np
import onnxruntime as ort

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "weights", "aasist_l.onnx")
_NB_SAMP = 64600  # fixed input length the pretrained weights were trained on (~4.04s @ 16kHz)

_session: ort.InferenceSession | None = None


def load_model() -> None:
    global _session
    if _session is None:
        options = ort.SessionOptions()
        # Capped rather than left to auto-detect all cores: analyze() runs
        # concurrently with speaker_verification.embed() (see voice_auth.py's
        # asyncio.gather), and letting both claim every core on a 2-core
        # laptop causes the same kind of thread-pool contention already
        # worked around for numba/torch in speaker_verification.py.
        options.intra_op_num_threads = 2
        _session = ort.InferenceSession(_MODEL_PATH, sess_options=options, providers=["CPUExecutionProvider"])


def _pad(x: np.ndarray, max_len: int = _NB_SAMP) -> np.ndarray:
    if x.shape[0] >= max_len:
        return x[:max_len]
    num_repeats = max_len // x.shape[0] + 1
    return np.tile(x, num_repeats)[:max_len]


def _wav_to_float32(wav_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    pcm16 = np.frombuffer(raw, dtype=np.int16)
    return pcm16.astype(np.float32) / 32768.0


def analyze(wav_bytes: bytes) -> float:
    load_model()

    x = _pad(_wav_to_float32(wav_bytes))[np.newaxis, :]  # (1, NB_SAMP)
    logits = _session.run(["logits"], {"waveform": x})[0]

    # trained with label 0=spoof, 1=bonafide (AASIST/ASVspoof2019 convention)
    exp = np.exp(logits - np.max(logits, axis=1, keepdims=True))
    probs = exp / np.sum(exp, axis=1, keepdims=True)

    return float(probs[0, 0])
