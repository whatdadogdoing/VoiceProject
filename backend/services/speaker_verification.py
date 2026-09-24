import io
import os
import wave

# numba's default threading layer (tbb/omp) can deadlock against torch's own
# thread pool on low-core-count machines; workqueue is the layer that doesn't
# fight torch for threads. Must be set before librosa/numba is imported.
os.environ.setdefault("NUMBA_THREADING_LAYER", "workqueue")

import numpy as np
from resemblyzer import VoiceEncoder, preprocess_wav

# The voiceprint is the average of this many samples. Each challenge phrase gives
# only 2-4 s of speech, so a single embedding is noisy; simulating templates of
# k in-app takes tested on the rest, the share of genuine takes falling under the
# 0.70 bar was 45.5% (k=1), 16.1% (2), 7.8% (3), 3.9% (5), 2.1% (8). Five halves
# the false rejections of three (the false acceptances rise a little at a fixed
# threshold, but separation improves) for two more phrases at enrollment.
ENROLL_SAMPLES_REQUIRED = 5

_encoder: VoiceEncoder | None = None


def load_encoder() -> None:
    global _encoder
    if _encoder is None:
        _encoder = VoiceEncoder("cpu")
        # pays the one-time numba JIT-compile cost at startup instead of on
        # the first real user request
        embed(_silence_wav_bytes())


def _silence_wav_bytes(seconds: float = 1.0, sr: int = 16000) -> bytes:
    rng = np.random.default_rng(0)
    pcm16 = (rng.standard_normal(int(sr * seconds)) * 50).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


def _wav_to_float32(wav_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    pcm16 = np.frombuffer(raw, dtype=np.int16)
    return pcm16.astype(np.float32) / 32768.0


def embed(wav_bytes: bytes) -> list[float]:
    load_encoder()
    wav = preprocess_wav(_wav_to_float32(wav_bytes), source_sr=16000)
    return _encoder.embed_utterance(wav).tolist()


def average_embedding(embeddings: list[list[float]]) -> list[float]:
    vectors = [np.array(e) for e in embeddings]
    normalized = [v / np.linalg.norm(v) for v in vectors]
    centroid = np.mean(normalized, axis=0)
    return (centroid / np.linalg.norm(centroid)).tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb)))


def adapt_embedding(enrolled: list[float], sample: list[float], sample_weight: float = 0.15) -> list[float]:
    """Nudge the stored voiceprint toward a freshly-verified sample, so gradual
    voice change (a cold, fatigue, aging) doesn't require re-enrollment. Small
    sample_weight keeps any single session from shifting the template much."""
    enrolled_v, sample_v = np.array(enrolled), np.array(sample)
    blended = (1 - sample_weight) * enrolled_v + sample_weight * sample_v
    return (blended / np.linalg.norm(blended)).tolist()
