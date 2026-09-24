from services import risk_engine

BASE_CONTEXT = {"hour_of_day": 12, "is_new_device": False, "is_new_ip": False}


def _patch_counts(monkeypatch, failures=0, fraud=0):
    seen = {}

    async def fake_failures(user_id, minutes=10, exclude_reasons=()):
        seen["exclude_reasons"] = frozenset(exclude_reasons)
        return failures

    async def fake_fraud(user_id, minutes=30):
        return fraud

    monkeypatch.setattr(risk_engine, "count_recent_failures", fake_failures)
    monkeypatch.setattr(risk_engine, "count_recent_fraud_failures", fake_fraud)
    return seen


def _patch_redis(monkeypatch, fake_redis):
    monkeypatch.setattr(risk_engine, "get_redis", lambda: fake_redis)


async def test_happy_path_mfa_required(monkeypatch, fake_redis):
    _patch_counts(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    decision, reason, meta = await risk_engine.evaluate("u1", 0.9, 0.1, BASE_CONTEXT)
    assert (decision, reason) == ("mfa_required", None)


async def test_general_rate_limit(monkeypatch, fake_redis):
    _patch_counts(monkeypatch, failures=risk_engine.MAX_FAILURES)
    _patch_redis(monkeypatch, fake_redis)
    decision, reason, meta = await risk_engine.evaluate("u1", 0.9, 0.1, BASE_CONTEXT)
    assert (decision, reason) == ("rejected", "rate_limit_exceeded")


async def test_existing_fraud_lock_blocks_immediately(monkeypatch, fake_redis):
    _patch_counts(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    await fake_redis.set(risk_engine.fraud_lock_key("u1"), "1")
    # even a perfectly clean sample must not get through while locked
    decision, reason, meta = await risk_engine.evaluate("u1", 0.99, 0.0, BASE_CONTEXT)
    assert (decision, reason) == ("rejected", "fraud_lockout")


async def test_first_spoof_detection_counts_down_tries(monkeypatch, fake_redis):
    _patch_counts(monkeypatch, fraud=0)  # this call would be the 1st detection
    _patch_redis(monkeypatch, fake_redis)
    decision, reason, meta = await risk_engine.evaluate("u1", 0.9, 0.9, BASE_CONTEXT)
    assert (decision, reason) == ("rejected", "spoofing_detected")
    assert meta == {"tries_left": risk_engine.MAX_FRAUD_ATTEMPTS - 1}


async def test_third_spoof_detection_trips_persistent_lock(monkeypatch, fake_redis):
    _patch_counts(monkeypatch, fraud=risk_engine.MAX_FRAUD_ATTEMPTS - 1)  # this is the 3rd
    _patch_redis(monkeypatch, fake_redis)
    decision, reason, meta = await risk_engine.evaluate("u1", 0.9, 0.9, BASE_CONTEXT)
    assert (decision, reason) == ("rejected", "fraud_lockout")
    assert await fake_redis.exists(risk_engine.fraud_lock_key("u1"))


async def test_throttle_ignores_audio_quality_failures(monkeypatch, fake_redis):
    # Regression test: a bad mic or a misread phrase used to count toward the
    # 5-in-10-minutes security throttle, so five sloppy takes followed by a
    # good one still came back rate_limit_exceeded.
    seen = _patch_counts(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    await risk_engine.evaluate("u1", 0.9, 0.1, BASE_CONTEXT)
    assert seen["exclude_reasons"] == {"audio_too_quiet", "phrase_mismatch"}


def test_security_relevant_reasons_are_not_excluded_from_the_throttle():
    for reason in ("voiceprint_mismatch", "spoofing_detected", "suspicious_context", "fraud_lockout"):
        assert reason not in risk_engine.QUALITY_FAILURE_REASONS


def test_only_confident_matches_may_adapt_the_voiceprint():
    assert risk_engine.should_adapt_voiceprint(risk_engine.ADAPT_MIN_SCORE)
    assert risk_engine.should_adapt_voiceprint(0.95)
    # a sample that merely cleared the 0.7 match bar must not move the template
    assert not risk_engine.should_adapt_voiceprint(0.71)
    assert not risk_engine.should_adapt_voiceprint(risk_engine.ADAPT_MIN_SCORE - 0.01)


async def test_spoof_threshold_is_inclusive(monkeypatch, fake_redis):
    _patch_counts(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    decision, reason, _ = await risk_engine.evaluate("u1", 0.9, risk_engine.SPOOF_THRESHOLD, BASE_CONTEXT)
    assert (decision, reason) == ("rejected", "spoofing_detected")


async def test_just_below_spoof_threshold_passes(monkeypatch, fake_redis):
    _patch_counts(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    decision, reason, _ = await risk_engine.evaluate("u1", 0.9, risk_engine.SPOOF_THRESHOLD - 0.01, BASE_CONTEXT)
    assert (decision, reason) == ("mfa_required", None)


async def test_voiceprint_mismatch(monkeypatch, fake_redis):
    _patch_counts(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    decision, reason, meta = await risk_engine.evaluate("u1", 0.5, 0.1, BASE_CONTEXT)
    assert (decision, reason) == ("rejected", "voiceprint_mismatch")


# A score that clears the normal match bar but not the stricter one. Derived from
# the constants so these tests keep meaning "between the two bars" if either moves.
BETWEEN_THE_BARS = (risk_engine.MATCH_THRESHOLD + risk_engine.STRICT_MATCH_THRESHOLD) / 2


def test_strict_bar_is_stricter_than_the_normal_bar():
    assert risk_engine.STRICT_MATCH_THRESHOLD > risk_engine.MATCH_THRESHOLD


async def test_suspicious_context_needs_higher_voiceprint_score(monkeypatch, fake_redis):
    _patch_counts(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    # new device AND new ip -> suspicious; a score between the two bars clears the
    # normal one but not the stricter one required under a suspicious context
    context = {"hour_of_day": 12, "is_new_device": True, "is_new_ip": True}
    decision, reason, meta = await risk_engine.evaluate("u1", BETWEEN_THE_BARS, 0.1, context)
    assert (decision, reason) == ("rejected", "suspicious_context")


async def test_suspicious_context_passes_with_high_enough_score(monkeypatch, fake_redis):
    _patch_counts(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    context = {"hour_of_day": 12, "is_new_device": True, "is_new_ip": True}
    decision, reason, meta = await risk_engine.evaluate("u1", 0.9, 0.1, context)
    assert decision == "mfa_required"


async def test_late_night_new_device_alone_is_suspicious(monkeypatch, fake_redis):
    _patch_counts(monkeypatch)
    _patch_redis(monkeypatch, fake_redis)
    context = {"hour_of_day": 3, "is_new_device": True, "is_new_ip": False}
    decision, reason, meta = await risk_engine.evaluate("u1", BETWEEN_THE_BARS, 0.1, context)
    assert (decision, reason) == ("rejected", "suspicious_context")
