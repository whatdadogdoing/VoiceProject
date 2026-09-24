import importlib
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from services.token import SECRET, create_access_token, decode_access_token


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


def test_refuses_to_start_with_a_very_short_secret(monkeypatch):
    with pytest.raises(RuntimeError):
        _import_token_module(monkeypatch, "short")


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
