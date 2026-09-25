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
from fastapi import HTTPException
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
            ENCODER_ID="test-encoder",
            EMBEDDING_DIM=2,   # matches the 2-number fake embeddings below
        ),
        "services.anti_spoofing": types.SimpleNamespace(analyze=lambda wav: 0.1),
        "services.phrase_check": types.SimpleNamespace(check_phrase=lambda wav, phrase, code=None: True),
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

    async def get_voiceprint_model_id(user_id):
        return "test-encoder"

    async def update_voiceprint_embedding(user_id, embedding):
        calls.updated.append((user_id, embedding))

    async def log_attempt(*args, **kwargs):
        calls.logged.append(args)

    async def evaluate(user_id, voiceprint_score, spoof_score, context):
        return "mfa_required", None, {}

    monkeypatch.setattr(module, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(module, "has_active_consent", async_true)
    monkeypatch.setattr(module, "get_user_embedding", get_user_embedding)
    monkeypatch.setattr(module, "get_voiceprint_model_id", get_voiceprint_model_id)
    monkeypatch.setattr(module, "update_voiceprint_embedding", update_voiceprint_embedding)
    monkeypatch.setattr(module, "log_attempt", log_attempt)
    monkeypatch.setattr(module, "is_known_device", async_true)
    monkeypatch.setattr(module, "is_known_ip", async_true)
    monkeypatch.setattr(module, "evaluate", evaluate)
    monkeypatch.setattr(module, "check_phrase", lambda wav, phrase, code=None: True)
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


async def test_a_voiceprint_from_another_encoder_asks_for_re_enrollment(voice_auth, monkeypatch, fake_redis):
    # Vectors from a different encoder live in another embedding space; comparing
    # them would give a meaningless score. The user is told to re-enroll instead.
    module, calls = voice_auth

    async def old_encoder(user_id):
        return "some-older-encoder"

    monkeypatch.setattr(module, "get_voiceprint_model_id", old_encoder)
    monkeypatch.setattr(module, "embed", lambda wav: pytest.fail("no model should run for an incompatible voiceprint"))

    result = await _verify_with_score(module, monkeypatch, fake_redis, 0.99)

    assert result == {"decision": "rejected", "reason": "voiceprint_outdated"}
    assert calls.logged == []                      # not a security failure, so not an attempt
    assert not await fake_redis.exists("voice_passed:u1")


async def test_a_voiceprint_of_the_wrong_size_is_refused_not_compared(voice_auth, monkeypatch, fake_redis):
    module, calls = voice_auth

    async def wrong_size(user_id):
        return [0.1, 0.2, 0.3]                     # the encoder now produces 2 numbers

    monkeypatch.setattr(module, "get_user_embedding", wrong_size)

    result = await _verify_with_score(module, monkeypatch, fake_redis, 0.99)

    assert result == {"decision": "rejected", "reason": "voiceprint_outdated"}


async def test_a_voiceprint_from_the_current_encoder_verifies_normally(voice_auth, monkeypatch, fake_redis):
    module, calls = voice_auth

    result = await _verify_with_score(module, monkeypatch, fake_redis, 0.9)

    assert result["decision"] == "mfa_required"


# -- the random code read after the phrase (off unless VERIFY_CODE_ENABLED) ------------------------

CODE = "3390"


def _record_check_phrase(module, monkeypatch, passes=True):
    seen = []

    def check_phrase(wav, phrase, code=None):
        seen.append((phrase, code))
        return passes

    monkeypatch.setattr(module, "check_phrase", check_phrase)
    return seen


async def _issue_phrase(module, monkeypatch):
    async def phrase(user_id):
        return PHRASE

    monkeypatch.setattr(module, "next_verify_phrase", phrase)


async def test_the_prompt_carries_no_code_while_the_switch_is_off(voice_auth, monkeypatch, fake_redis):
    module, calls = voice_auth
    monkeypatch.delenv("VERIFY_CODE_ENABLED", raising=False)
    await _issue_phrase(module, monkeypatch)

    challenge = await module.verify_prompt(request=_request(), user_id="u1")

    assert challenge == {"phrase": PHRASE}
    assert not await fake_redis.exists("verify_code:u1")


async def test_the_prompt_issues_a_code_and_remembers_it_when_the_switch_is_on(voice_auth, monkeypatch, fake_redis):
    module, calls = voice_auth
    monkeypatch.setenv("VERIFY_CODE_ENABLED", "true")
    await _issue_phrase(module, monkeypatch)

    challenge = await module.verify_prompt(request=_request(), user_id="u1")

    assert challenge["phrase"] == PHRASE
    assert len(challenge["code"]) == 4 and challenge["code"].isdigit()
    assert (await fake_redis.get("verify_code:u1")).decode() == challenge["code"]


async def test_verify_checks_the_issued_code_and_uses_it_up(voice_auth, monkeypatch, fake_redis):
    module, calls = voice_auth
    monkeypatch.setenv("VERIFY_CODE_ENABLED", "true")
    seen = _record_check_phrase(module, monkeypatch)
    monkeypatch.setattr(module, "cosine_similarity", lambda a, b: 0.9)
    await fake_redis.set("verify_phrase:u1", PHRASE)
    await fake_redis.set("verify_code:u1", CODE)

    result = await module.verify(request=_request(), user_id="u1", audio=_FakeUpload())

    assert seen == [(PHRASE, CODE)]
    assert result["decision"] == "mfa_required"
    assert not await fake_redis.exists("verify_code:u1")
    assert not await fake_redis.exists("verify_phrase:u1")


async def test_a_wrong_code_is_a_phrase_mismatch_and_still_uses_the_code_up(voice_auth, monkeypatch, fake_redis):
    # one reason for both, so the answer does not tell a replaying attacker which half was wrong
    module, calls = voice_auth
    monkeypatch.setenv("VERIFY_CODE_ENABLED", "true")
    _record_check_phrase(module, monkeypatch, passes=False)
    await fake_redis.set("verify_phrase:u1", PHRASE)
    await fake_redis.set("verify_code:u1", CODE)

    result = await module.verify(request=_request(), user_id="u1", audio=_FakeUpload())

    assert result == {"decision": "rejected", "reason": "phrase_mismatch"}
    assert calls.logged[0][4] == "phrase_mismatch"
    assert not await fake_redis.exists("verify_code:u1")


async def test_verify_needs_a_code_when_the_switch_is_on(voice_auth, monkeypatch, fake_redis):
    # a prompt issued before the switch was turned on has no code: ask for a fresh one
    module, calls = voice_auth
    monkeypatch.setenv("VERIFY_CODE_ENABLED", "true")
    seen = _record_check_phrase(module, monkeypatch)
    await fake_redis.set("verify_phrase:u1", PHRASE)

    with pytest.raises(HTTPException) as caught:
        await module.verify(request=_request(), user_id="u1", audio=_FakeUpload())

    assert caught.value.status_code == 400
    assert seen == []


async def test_no_code_is_expected_while_the_switch_is_off(voice_auth, monkeypatch, fake_redis):
    module, calls = voice_auth
    monkeypatch.delenv("VERIFY_CODE_ENABLED", raising=False)
    seen = _record_check_phrase(module, monkeypatch)
    await fake_redis.set("verify_phrase:u1", PHRASE)
    await fake_redis.set("verify_code:u1", CODE)   # left over from a prompt issued while it was on

    await module.verify(request=_request(), user_id="u1", audio=_FakeUpload())

    assert seen == [(PHRASE, None)]
