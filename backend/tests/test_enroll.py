"""Tests for routers/enroll.py's submit_sample: the anti-spoofing gate on
enrollment samples, and the trusted device/IP seeded when enrollment completes.

routers.enroll pulls in the ML stack (Vosk, Resemblyzer, ONNX Runtime) and
ffmpeg-backed audio decoding at import time, none of which a unit test should
need. Those modules are stubbed before the router is imported; Redis and
Postgres are faked. The real submit_sample logic runs unmodified; the phrase
check is stubbed (it has its own tests in test_phrase_check.py).
"""
import importlib
import json
import sys
import types

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from services import risk_engine

PHRASES = [
    "Hôm nay trời nắng đẹp và gió mát.",
    "Tôi thích uống cà phê vào buổi sáng.",
    "Chiếc xe màu đỏ đang đậu trước cổng.",
]


class _FakeUpload:
    async def read(self):
        return b"fake-audio-bytes"


class _PassthroughLimiter:
    def limit(self, *_args, **_kwargs):
        return lambda fn: fn


def _request(headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": ("172.21.0.1", 1234)})


@pytest.fixture
def enroll(monkeypatch, fake_redis):
    stubs = {
        "services.phrase_check": types.SimpleNamespace(check_phrase=lambda wav, phrase: True),
        "services.speaker_verification": types.SimpleNamespace(
            embed=lambda wav: [0.1, 0.2],
            average_embedding=lambda embeddings: embeddings[0],
            ENROLL_SAMPLES_REQUIRED=3,
            ENCODER_ID="test-encoder",
        ),
        "services.anti_spoofing": types.SimpleNamespace(analyze=lambda wav: 0.0),
        "services.audio": types.SimpleNamespace(to_wav_pcm16=lambda b: b),
        "services.rate_limiter": types.SimpleNamespace(limiter=_PassthroughLimiter()),
    }
    for name, stub in stubs.items():
        monkeypatch.setitem(sys.modules, name, stub)
    monkeypatch.delitem(sys.modules, "routers.enroll", raising=False)
    module = importlib.import_module("routers.enroll")

    calls = types.SimpleNamespace(saved=[], saved_models=[], logged=[])

    async def has_consent(user_id):
        return True

    async def has_voiceprint(user_id):
        return False

    async def save_voiceprint(user_id, embedding, model_id, replace_existing=False):
        calls.saved.append((user_id, embedding, replace_existing))
        calls.saved_models.append(model_id)

    async def log_attempt(user_id, voiceprint_score, spoof_score, decision, reason, ip=None, device=None):
        calls.logged.append({
            "user_id": user_id, "voiceprint_score": voiceprint_score, "spoof_score": spoof_score,
            "decision": decision, "reason": reason, "ip": ip, "device": device,
        })

    monkeypatch.setattr(module, "has_active_consent", has_consent)
    monkeypatch.setattr(module, "has_voiceprint", has_voiceprint)
    monkeypatch.setattr(module, "save_voiceprint", save_voiceprint)
    monkeypatch.setattr(module, "log_attempt", log_attempt)
    monkeypatch.setattr(module, "get_redis", lambda: fake_redis)
    # the phrase is always judged correct, whichever sample index the test is on
    monkeypatch.setattr(module, "check_phrase", lambda wav, phrase: True)
    # never let a test write into the real capture directory (backend/eval_data/rejected)
    monkeypatch.setattr(module, "save_rejected", lambda wav, tag, score: None)

    yield module, calls

    sys.modules.pop("routers.enroll", None)


async def _seed_state(fake_redis, embeddings):
    await fake_redis.set("enroll:u1", json.dumps({"embeddings": embeddings, "phrases": PHRASES}))


async def _state(fake_redis):
    return json.loads(await fake_redis.get("enroll:u1"))


async def test_spoofed_sample_is_rejected_and_state_unchanged(enroll, monkeypatch, fake_redis):
    module, calls = enroll
    monkeypatch.setattr(module, "analyze", lambda wav: 0.9)
    await _seed_state(fake_redis, [])

    result = await module.submit_sample(request=_request(), user_id="u1", audio=_FakeUpload())

    assert result["status"] == "spoof_detected"
    assert result["phrase"] == PHRASES[0]
    # the score itself must never reach the client, or it becomes a tuning oracle
    assert set(result) == {"status", "message", "phrase"}
    assert (await _state(fake_redis))["embeddings"] == []
    assert calls.saved == []
    # not logged as an attempt: that would feed the fraud-lockout counter
    assert calls.logged == []


async def test_a_spoof_rejected_sample_is_handed_to_the_opt_in_capture(enroll, monkeypatch, fake_redis):
    module, calls = enroll
    monkeypatch.setattr(module, "analyze", lambda wav: 0.594)
    captured = []
    monkeypatch.setattr(module, "save_rejected", lambda wav, tag, score: captured.append((tag, score)))
    await _seed_state(fake_redis, [])

    await module.submit_sample(request=_request(), user_id="u1", audio=_FakeUpload())

    assert captured == [("enroll-spoof", 0.594)]


async def test_a_clean_sample_is_not_captured(enroll, monkeypatch, fake_redis):
    module, calls = enroll
    captured = []
    monkeypatch.setattr(module, "save_rejected", lambda wav, tag, score: captured.append(tag))
    await _seed_state(fake_redis, [])

    await module.submit_sample(request=_request(), user_id="u1", audio=_FakeUpload())

    assert captured == []


async def _hit_the_spoof_limit(module, monkeypatch, fake_redis):
    """Submit spoofed samples until the per-user enrollment limit is reached;
    returns a list that records every call that actually reached the models."""
    analyzed = []
    monkeypatch.setattr(module, "analyze", lambda wav: analyzed.append(1) or 0.9)
    await _seed_state(fake_redis, [])
    for _ in range(module.ENROLL_SPOOF_LIMIT):
        result = await module.submit_sample(request=_request(), user_id="u1", audio=_FakeUpload())
        assert result["status"] == "spoof_detected"
    return analyzed


async def test_repeated_spoofed_samples_block_further_enrollment(enroll, monkeypatch, fake_redis):
    module, calls = enroll
    analyzed = await _hit_the_spoof_limit(module, monkeypatch, fake_redis)

    with pytest.raises(HTTPException) as blocked:
        await module.submit_sample(request=_request(), user_id="u1", audio=_FakeUpload())

    assert blocked.value.status_code == 429
    # the blocked call was refused before doing any model work
    assert len(analyzed) == module.ENROLL_SPOOF_LIMIT


async def test_the_enrollment_block_is_separate_from_the_verify_fraud_lock(enroll, monkeypatch, fake_redis):
    module, calls = enroll
    await _hit_the_spoof_limit(module, monkeypatch, fake_redis)

    assert await fake_redis.exists("fraud_locked:u1") == 0
    assert calls.logged == []   # still not written to voice_auth_attempts


async def test_clean_samples_are_not_counted(enroll, monkeypatch, fake_redis):
    module, calls = enroll
    await _seed_state(fake_redis, [])

    await module.submit_sample(request=_request(), user_id="u1", audio=_FakeUpload())

    assert await fake_redis.get("enroll_spoof:u1") is None


async def test_the_enrollment_block_only_applies_to_the_user_who_earned_it(enroll, monkeypatch, fake_redis):
    module, calls = enroll
    await _hit_the_spoof_limit(module, monkeypatch, fake_redis)
    monkeypatch.setattr(module, "analyze", lambda wav: 0.0)
    await fake_redis.set("enroll:u2", json.dumps({"embeddings": [], "phrases": PHRASES}))

    result = await module.submit_sample(request=_request(), user_id="u2", audio=_FakeUpload())

    assert result["status"] == "enrolling"


async def test_sample_exactly_at_threshold_is_rejected(enroll, monkeypatch, fake_redis):
    module, calls = enroll
    monkeypatch.setattr(module, "analyze", lambda wav: risk_engine.SPOOF_THRESHOLD)
    await _seed_state(fake_redis, [])

    result = await module.submit_sample(request=_request(), user_id="u1", audio=_FakeUpload())

    assert result["status"] == "spoof_detected"


async def test_clean_sample_is_stored(enroll, monkeypatch, fake_redis):
    module, calls = enroll
    monkeypatch.setattr(module, "analyze", lambda wav: 0.1)
    await _seed_state(fake_redis, [])

    result = await module.submit_sample(request=_request(), user_id="u1", audio=_FakeUpload())

    assert result["status"] == "enrolling"
    assert result["samples_submitted"] == 1
    assert len((await _state(fake_redis))["embeddings"]) == 1
    assert calls.logged == []


async def test_spoofed_final_sample_cannot_complete_enrollment(enroll, monkeypatch, fake_redis):
    module, calls = enroll
    monkeypatch.setattr(module, "analyze", lambda wav: 0.9)
    await _seed_state(fake_redis, [[0.1, 0.2], [0.1, 0.2]])

    result = await module.submit_sample(request=_request(), user_id="u1", audio=_FakeUpload())

    assert result["status"] == "spoof_detected"
    assert calls.saved == []
    assert calls.logged == []
    assert len((await _state(fake_redis))["embeddings"]) == 2


async def test_completing_enrollment_saves_voiceprint_and_seeds_trusted_device(enroll, monkeypatch, fake_redis):
    # Regression test: a freshly enrolled user has no attempt history, so their
    # first verify was always "new device AND new IP" and needed a 0.85 match
    # instead of 0.7. Enrollment now records the device/IP it happened from.
    module, calls = enroll
    monkeypatch.setattr(module, "analyze", lambda wav: 0.1)
    await _seed_state(fake_redis, [[0.1, 0.2], [0.1, 0.2]])

    result = await module.submit_sample(
        request=_request({"x-real-ip": "198.51.100.7", "x-device-id": "dev-abc"}),
        user_id="u1",
        audio=_FakeUpload(),
    )

    assert result["status"] == "enrolled"
    assert len(calls.saved) == 1
    # the voiceprint is stored together with the encoder that produced it
    assert calls.saved_models == ["test-encoder"]
    assert calls.logged == [{
        "user_id": "u1", "voiceprint_score": None, "spoof_score": None,
        "decision": "enrolled", "reason": None, "ip": "198.51.100.7", "device": "dev-abc",
    }]
    assert await fake_redis.get("enroll:u1") is None
