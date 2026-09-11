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
