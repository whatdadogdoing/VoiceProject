"""Tests for the input-level normalization in services/anti_spoofing.py.

AASIST-L's spoof score swings with loudness alone (the same real recording went
from 0.024 to 0.943 with +6 dB), so analyze() scales speech to one fixed level
before scoring. These use synthetic signals and a fake ONNX session: no model
file is loaded.
"""
import io
import wave

import numpy as np
import pytest

from services import anti_spoofing as aa


def _speechlike(seconds=5.0, amplitude=0.05, lead_pause_s=0.0, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(16000 * seconds)).astype(np.float32) * amplitude
    x[: int(16000 * lead_pause_s)] *= 0.01  # a pause before the person starts talking
    return x


def _wav(x):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def test_level_measurement_ignores_pauses():
    talking = _speechlike(seconds=4.0)
    with_pause = np.concatenate([_speechlike(seconds=1.5, amplitude=0.0005), talking])

    assert aa._speech_level_dbfs(with_pause) == pytest.approx(aa._speech_level_dbfs(talking), abs=1.0)


def test_different_loudness_ends_at_the_same_level():
    quiet = _speechlike(amplitude=0.02)
    loud = quiet * 4  # +12 dB

    q = aa._speech_level_dbfs(aa._normalize_level(quiet))
    l = aa._speech_level_dbfs(aa._normalize_level(loud))

    assert q == pytest.approx(aa.LEVEL_TARGET_DBFS, abs=0.5)
    assert l == pytest.approx(aa.LEVEL_TARGET_DBFS, abs=0.5)


def test_silence_and_too_short_audio_are_left_alone():
    silence = np.zeros(16000, dtype=np.float32)
    short = _speechlike(seconds=0.02)

    assert np.array_equal(aa._normalize_level(silence), silence)
    assert np.array_equal(aa._normalize_level(short), short)


def test_boost_is_capped_so_near_silence_is_not_turned_into_loud_noise():
    faint = _speechlike(amplitude=10 ** (-78 / 20))  # about -78 dBFS, above the silence cutoff

    boosted = aa._speech_level_dbfs(aa._normalize_level(faint))
    original = aa._speech_level_dbfs(faint)

    assert boosted - original == pytest.approx(aa._MAX_BOOST_DB, abs=0.5)
    assert boosted < aa.LEVEL_TARGET_DBFS - 5


def test_a_very_loud_take_is_cut_all_the_way_to_the_target():
    # Speech near -5 dBFS (recorded right at the mic) needs about 35 dB of cut. An
    # earlier version clipped the gain at +/-30 dB and left such a take at about
    # -35 dBFS, inside the range where genuine speech starts being flagged.
    hot = _speechlike(amplitude=0.5)
    level_in = aa._speech_level_dbfs(hot)
    assert level_in - aa.LEVEL_TARGET_DBFS > aa._MAX_BOOST_DB   # needs more than 30 dB of cut

    assert aa._speech_level_dbfs(aa._normalize_level(hot)) == pytest.approx(aa.LEVEL_TARGET_DBFS, abs=0.5)


def test_normalized_audio_never_exceeds_full_scale():
    spiky = _speechlike(amplitude=0.001)
    spiky[::4000] = 0.9  # a few loud clicks that the gain would push past 1.0

    out = aa._normalize_level(spiky)

    assert np.abs(out).max() <= 1.0


def test_analyze_feeds_the_model_the_same_level_whatever_the_input_gain(monkeypatch):
    seen = []

    class FakeSession:
        def run(self, outputs, feeds):
            seen.append(feeds["waveform"][0].copy())
            return [np.zeros((1, 2), dtype=np.float32)]

    monkeypatch.setattr(aa, "_session", FakeSession())
    base = _speechlike(seconds=5.0, amplitude=0.02)

    aa.analyze(_wav(base))
    aa.analyze(_wav(base * 4))  # the same recording, 12 dB louder

    quiet, loud = (aa._speech_level_dbfs(x) for x in seen)
    assert quiet == pytest.approx(loud, abs=0.5)
    assert quiet == pytest.approx(aa.LEVEL_TARGET_DBFS, abs=0.5)
