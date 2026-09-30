"""Tests for services/otp.py: the wrong-guess counter must survive resending a
code, count per account rather than per code, and expire on its own window
(OTP_FAIL_WINDOW_SECONDS) instead of the much shorter code lifetime.

Before this, send_otp() reset the counter on every resend; since resends were
only rate-limited per IP, an attacker with a password session could keep
requesting fresh codes to get a fresh guessing budget each time, or simply
switch IP to reset the per-IP limiter itself.
"""
import pytest
from fastapi_mail import FastMail

from services import otp


@pytest.fixture(autouse=True)
def _redis(monkeypatch, fake_redis):
    monkeypatch.setattr(otp, "get_redis", lambda: fake_redis)
    # send_otp() builds a real ConnectionConfig and calls FastMail for real;
    # give it valid-looking dummy credentials and stub the actual network call
    # so these tests exercise the OTP logic without hitting Gmail's SMTP.
    monkeypatch.setenv("MAIL_USERNAME", "test@example.com")
    monkeypatch.setenv("MAIL_PASSWORD", "test-password")
    monkeypatch.setenv("MAIL_FROM", "test@example.com")

    async def _no_real_email(self, *_args, **_kwargs):
        pass
    monkeypatch.setattr(FastMail, "send_message", _no_real_email)


async def _stored_code(fake_redis, user_id="user-1"):
    return (await fake_redis.get(f"otp:{user_id}")).decode()


async def test_correct_code_is_accepted(fake_redis):
    await otp.send_otp("user-1", "user@example.com")
    code = await _stored_code(fake_redis)
    assert await otp.verify_otp("user-1", code) is True


async def test_wrong_code_is_rejected_and_counted(fake_redis):
    await otp.send_otp("user-1", "user@example.com")
    assert await otp.verify_otp("user-1", "000000") is False
    assert await fake_redis.get("otp_fail_count:user-1") == b"1"


async def test_resending_a_code_does_not_reset_the_fail_counter(fake_redis):
    await otp.send_otp("user-1", "user@example.com")
    await otp.verify_otp("user-1", "000000")
    await otp.verify_otp("user-1", "000000")
    assert await fake_redis.get("otp_fail_count:user-1") == b"2"

    # a resend (as an attacker with only a password session can trigger) must
    # not hand back a fresh guessing budget
    await otp.send_otp("user-1", "user@example.com")
    assert await fake_redis.get("otp_fail_count:user-1") == b"2"


async def test_the_account_is_locked_out_after_max_attempts_even_across_resends(fake_redis):
    for _ in range(otp.MAX_OTP_ATTEMPTS):
        # a fresh code each time, exactly like a real attacker requesting resends would
        await otp.send_otp("user-1", "user@example.com")
        await otp.verify_otp("user-1", "000000")

    # the real code from the last send is rejected too: the account is locked, not just the code
    real_code = await _stored_code(fake_redis)
    assert await otp.verify_otp("user-1", real_code) is False


async def test_a_correct_guess_clears_the_fail_counter(fake_redis):
    await otp.send_otp("user-1", "user@example.com")
    await otp.verify_otp("user-1", "000000")
    code = await _stored_code(fake_redis)
    assert await otp.verify_otp("user-1", code) is True

    assert await fake_redis.get("otp_fail_count:user-1") is None


async def test_the_fail_counter_is_scoped_per_account(fake_redis):
    await otp.send_otp("user-1", "a@example.com")
    await otp.send_otp("user-2", "b@example.com")
    for _ in range(otp.MAX_OTP_ATTEMPTS):
        await otp.verify_otp("user-1", "000000")

    code2 = await _stored_code(fake_redis, "user-2")
    assert await otp.verify_otp("user-2", code2) is True


async def test_the_fail_counter_gets_a_one_hour_ttl_on_the_first_wrong_guess_only(fake_redis):
    await otp.send_otp("user-1", "user@example.com")
    await otp.verify_otp("user-1", "000000")
    assert fake_redis.ttl_of("otp_fail_count:user-1") == otp.OTP_FAIL_WINDOW_SECONDS

    fake_redis._ttls["otp_fail_count:user-1"] = None  # prove the 2nd failure doesn't touch it again
    await otp.verify_otp("user-1", "111111")
    assert fake_redis.ttl_of("otp_fail_count:user-1") is None


async def test_verifying_with_no_code_ever_sent_fails_without_touching_the_counter(fake_redis):
    assert await otp.verify_otp("user-1", "123456") is False
    assert await fake_redis.get("otp_fail_count:user-1") is None
