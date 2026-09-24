"""Tests for the voiceprint-adaptation wiring in routers/voice_auth.py.

verify() only caches the just-verified sample for later adaptation when the
match is confident (risk_engine.should_adapt_voiceprint); verify_otp_endpoint()
adapts the stored voiceprint only if such a sample was cached. As in
test_enroll.py, the ML stack, audio decoding and e-mail sending are stubbed
before the router is imported, Redis and Postgres are faked, and the real
verify()/verify_otp_endpoint() logic runs unmodified.
"""
import importlib
import sys
import types

import pytest
from starlette.requests import Request

from services import risk_engine

PHRASE = "Hôm nay trời nắng đẹp và gió mát."


class _FakeUpload:
    async def read(self):
        return b"fake-audio-bytes"


class _PassthroughLimiter:
    def limit(self, *_args, **_kwargs):
        return lambda fn: fn


def _request():
    return Request({"type": "http", "headers": [], "client": ("172.21.0.1", 1234)})


@pytest.fixture
def voice_auth(monkeypatch, fake_redis):
    stubs = {
        "services.speaker_verification": types.SimpleNamespace(
            embed=lambda wav: [0.1, 0.2],
            cosine_similarity=lambda a, b: 0.0,
            adapt_embedding=lambda enrolled, sample: [9.9, 9.9],
        ),
        "services.anti_spoofing": types.SimpleNamespace(analyze=lambda wav: 0.1),
        "services.phrase_check": types.SimpleNamespace(check_phrase=lambda wav, phrase: True),
        "services.audio": types.SimpleNamespace(to_wav_pcm16=lambda b: b, is_too_quiet=lambda wav: False),
        "services.otp": types.SimpleNamespace(send_otp=None, verify_otp=None),
        "services.rate_limiter": types.SimpleNamespace(limiter=_PassthroughLimiter()),
    }
    for name, stub in stubs.items():
        monkeypatch.setitem(sys.modules, name, stub)
    monkeypatch.delitem(sys.modules, "routers.voice_auth", raising=False)
    module = importlib.import_module("routers.voice_auth")

    calls = types.SimpleNamespace(updated=[], logged=[])

    async def async_true(*_a, **_k):
        return True

    async def get_user_embedding(user_id):
        return [0.1, 0.2]

    async def update_voiceprint_embedding(user_id, embedding):
        calls.updated.append((user_id, embedding))

    async def log_attempt(*args, **kwargs):
        calls.logged.append(args)

    async def evaluate(user_id, voiceprint_score, spoof_score, context):
        return "mfa_required", None, {}

    monkeypatch.setattr(module, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(module, "has_active_consent", async_true)
    monkeypatch.setattr(module, "get_user_embedding", get_user_embedding)
    monkeypatch.setattr(module, "update_voiceprint_embedding", update_voiceprint_embedding)
    monkeypatch.setattr(module, "log_attempt", log_attempt)
    monkeypatch.setattr(module, "is_known_device", async_true)
    monkeypatch.setattr(module, "is_known_ip", async_true)
    monkeypatch.setattr(module, "evaluate", evaluate)
    monkeypatch.setattr(module, "check_phrase", lambda wav, phrase: True)
    monkeypatch.setattr(module, "verify_otp", async_true)

    yield module, calls

    sys.modules.pop("routers.voice_auth", None)


async def _verify_with_score(module, monkeypatch, fake_redis, score):
    monkeypatch.setattr(module, "cosine_similarity", lambda a, b: score)
    await fake_redis.set("verify_phrase:u1", PHRASE)
    return await module.verify(request=_request(), user_id="u1", audio=_FakeUpload())


async def test_confident_match_caches_sample_and_adapts_after_otp(voice_auth, monkeypatch, fake_redis):
    module, calls = voice_auth

    result = await _verify_with_score(module, monkeypatch, fake_redis, 0.92)
    assert result["decision"] == "mfa_required"
    assert await fake_redis.exists("voice_passed:u1")
    assert await fake_redis.exists("voice_sample_embedding:u1")

    await module.verify_otp_endpoint(request=_request(), body=module.OtpVerifyRequest(code="123456"), user_id="u1")
    assert calls.updated == [("u1", [9.9, 9.9])]


async def test_borderline_match_passes_but_does_not_move_the_template(voice_auth, monkeypatch, fake_redis):
    # Regression test: adaptation used to run for every successful verify, so a
    # sample that only just cleared the 0.7 bar could drag the stored voiceprint
    # toward itself and make the next attempt easier.
    module, calls = voice_auth
    borderline = 0.74
    assert 0.7 <= borderline < risk_engine.ADAPT_MIN_SCORE

    result = await _verify_with_score(module, monkeypatch, fake_redis, borderline)
    assert result["decision"] == "mfa_required"
    assert await fake_redis.exists("voice_passed:u1")
    assert not await fake_redis.exists("voice_sample_embedding:u1")

    response = await module.verify_otp_endpoint(
        request=_request(), body=module.OtpVerifyRequest(code="123456"), user_id="u1"
    )
    assert "access_token" in response
    assert calls.updated == []
