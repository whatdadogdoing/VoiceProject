import io
import wave

import numpy as np
from pydub import AudioSegment

TOO_QUIET_DBFS = -40.0


def to_wav_pcm16(audio_bytes: bytes) -> bytes:
    audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
    audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    buf = io.BytesIO()
    audio.export(buf, format="wav")
    return buf.getvalue()


def _rms_dbfs(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    pcm16 = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if pcm16.size == 0:
        return -float("inf")
    rms = np.sqrt(np.mean(pcm16 ** 2))
    if rms <= 0:
        return -float("inf")
    return 20 * np.log10(rms / 32768.0)


def is_too_quiet(wav_bytes: bytes) -> bool:
    return _rms_dbfs(wav_bytes) < TOO_QUIET_DBFS
