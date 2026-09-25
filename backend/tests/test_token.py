import importlib
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from services.token import SECRET, create_access_token, decode_access_token, decode_access_token_with_iat


def test_the_token_records_when_it_was_issued():
    # logout revokes by comparing this against a cutoff (see test_logout.py)
    before = int(datetime.now(timezone.utc).timestamp())
    token = create_access_token("user-1", ["voice", "otp"])
    user_id, issued_at = decode_access_token_with_iat(token)
    assert user_id == "user-1"
    assert before <= issued_at <= before + 2


def test_a_token_without_iat_counts_as_issued_at_zero():
    legacy = pyjwt.encode(
        {
            "sub": "user-1",
            "auth_methods": ["voice", "otp"],
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        SECRET,
        algorithm="HS256",
    )
    assert decode_access_token_with_iat(legacy) == ("user-1", 0)


def test_round_trip_voice_otp():
    token = create_access_token("user-123", ["voice", "otp"])
    assert decode_access_token(token) == "user-123"


def test_round_trip_recovery_otp():
    token = create_access_token("user-456", ["recovery", "otp"])
    assert decode_access_token(token) == "user-456"


def test_rejects_voice_without_otp():
    # A full auth flow always requires OTP -- voice alone must not mint a
    # usable access token.
    token = create_access_token("user-789", ["voice"])
    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token(token)


def test_rejects_otp_without_voice_or_recovery():
    token = create_access_token("user-000", ["otp"])
    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token(token)


def test_rejects_bad_signature():
    forged = pyjwt.encode(
        {
            "sub": "attacker",
            "auth_methods": ["voice", "otp"],
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        "wrong-secret",
        algorithm="HS256",
    )
    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token(forged)


def test_rejects_expired_token():
    expired = pyjwt.encode(
        {
            "sub": "user-1",
            "auth_methods": ["voice", "otp"],
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token(expired)


def _import_token_module(monkeypatch, secret):
    """Import services.token afresh with JWT_SECRET set (or unset when None).
    monkeypatch restores both the environment and sys.modules afterwards."""
    if secret is None:
        monkeypatch.delenv("JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRET", secret)
    monkeypatch.delitem(sys.modules, "services.token", raising=False)
    return importlib.import_module("services.token")


def test_refuses_to_start_without_a_secret(monkeypatch):
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        _import_token_module(monkeypatch, None)


def test_refuses_to_start_with_an_empty_secret(monkeypatch):
    with pytest.raises(RuntimeError):
        _import_token_module(monkeypatch, "")


def test_refuses_to_start_with_the_placeholder_from_env_example(monkeypatch):
    # the value in .env.example is public, so tokens signed with it prove nothing
    with pytest.raises(RuntimeError):
        _import_token_module(monkeypatch, "replace-with-a-random-secret")


@pytest.mark.parametrize("secret", ["short", "x" * 31, "é" * 15])   # the last is 15 characters, 30 bytes
def test_refuses_to_start_with_a_secret_under_32_bytes(monkeypatch, secret):
    # RFC 7518 section 3.2: an HS256 key is at least as long as the hash output
    with pytest.raises(RuntimeError, match="32 bytes"):
        _import_token_module(monkeypatch, secret)


@pytest.mark.parametrize("secret", ["x" * 32, "é" * 16])   # the last is 16 characters but 32 bytes
def test_a_secret_of_exactly_32_bytes_is_accepted(monkeypatch, secret):
    assert _import_token_module(monkeypatch, secret).SECRET == secret


def test_the_suggested_command_makes_a_secret_that_passes_the_guard(monkeypatch):
    import secrets
    _import_token_module(monkeypatch, secrets.token_urlsafe(48))


def test_the_placeholder_in_env_example_matches_the_one_the_code_rejects():
    # if someone edits .env.example without updating the check (or the reverse),
    # the guard silently stops protecting the placeholder case
    path = pathlib.Path(__file__).resolve().parents[2] / ".env.example"
    if not path.exists():
        pytest.skip(".env.example is outside backend/, which is all the container mounts")
    from services.token import PLACEHOLDER_SECRET
    assert f"JWT_SECRET={PLACEHOLDER_SECRET}" in path.read_text(encoding="utf-8")


def test_starts_with_a_real_secret(monkeypatch):
    module = _import_token_module(monkeypatch, "a-perfectly-good-random-secret-value")
    assert module.SECRET == "a-perfectly-good-random-secret-value"
