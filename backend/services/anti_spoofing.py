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


# AASIST-L's score depends heavily on how loud the input is, independent of who
# is speaking or whether it is synthetic: on the same real recording, raising
# the level by 6 dB moved the spoof score from 0.024 to 0.521 and from 0.165 to
# 0.943. Without this step, whether a genuine user is rejected (and, after 3
# rejections, locked out for good) would depend on how close they sit to the mic.
# Speech is therefore scaled to one fixed level before scoring, and the lower
# the level, the lower the spoof score, so the target has to sit low enough
# that real speech clears the threshold but not so low that clones do too.
# A first choice of -32 dBFS (from desktop-recorder audio) still flagged 2 of
# 13 genuine recordings made in the app's own browser recorder (scores up to
# 0.99), and those were the quiet ones the normalization had boosted. Sweeping
# 13 in-app genuine recordings and 10 voice clones showed a stable plateau
# from -39 to -42 dBFS: 0/13 genuine flagged and 10/10 clones flagged. -40 sits
# in the middle of it: the worst genuine score was 0.159 (0.34 under the 0.5
# threshold) and the weakest clone 0.585. Above -37 genuine speech starts being
# flagged; below -43 clones start slipping through (8/10 caught at -44).
# Caveats: one speaker, small samples, clones were fed in directly rather than
# replayed through a speaker, and the target was picked on the same data it was
# checked on -- a starting point to re-verify, not a validated constant.
#
# Not the same quantity as services/audio.TOO_QUIET_DBFS, which is also -40: that
# is the RMS of the whole file, this is the 90th percentile of 50 ms frame RMS,
# which sits higher because pauses drag the whole-file RMS down (real browser
# takes measured about -28 dBFS RMS but -23 dBFS on this scale). The two are
# independent thresholds that happen to share a number.
LEVEL_TARGET_DBFS = -40.0
_LEVEL_FRAME = 800  # 50 ms at 16 kHz
# Only amplification is capped: boosting near-silence would invent something that
# looks like speech. Attenuation creates no signal, so it is left uncapped -- a
# take recorded very close to the mic (speech near -5 dBFS) needs about 35 dB of
# cut, and clipping that at 30 dB would land it at -35 dBFS, inside the range
# where genuine speech starts being flagged.
_MAX_BOOST_DB = 30.0


def _speech_level_dbfs(x: np.ndarray) -> float:
    """Loudness of the speech in `x` (float, full scale = 1.0): the 90th
    percentile of 50 ms frame RMS, so leading/trailing pauses don't drag it down."""
    if x.shape[0] < _LEVEL_FRAME:
        return float("-inf")
    frames = x[: x.shape[0] // _LEVEL_FRAME * _LEVEL_FRAME].reshape(-1, _LEVEL_FRAME)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return float(np.percentile(20 * np.log10(rms + 1e-9), 90))


def _normalize_level(x: np.ndarray, target_dbfs: float = LEVEL_TARGET_DBFS) -> np.ndarray:
    level = _speech_level_dbfs(x)
    if not np.isfinite(level) or level < -80.0:
        return x  # silence or too short to measure; leave it alone
    gain_db = min(target_dbfs - level, _MAX_BOOST_DB)
    return np.clip(x * 10 ** (gain_db / 20), -1.0, 1.0).astype(np.float32)


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

    x = _pad(_normalize_level(_wav_to_float32(wav_bytes)))[np.newaxis, :]  # (1, NB_SAMP)
    logits = _session.run(["logits"], {"waveform": x})[0]

    # trained with label 0=spoof, 1=bonafide (AASIST/ASVspoof2019 convention)
    exp = np.exp(logits - np.max(logits, axis=1, keepdims=True))
    probs = exp / np.sum(exp, axis=1, keepdims=True)

    return float(probs[0, 0])
