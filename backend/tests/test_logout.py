"""Logout ends the password session and revokes the post-MFA JWT.

A JWT can't be looked up and deleted like a session, so before this a copy of
it stayed valid for its whole hour after the user had logged out, and it is the
token that opens re-enrollment (overwriting the voiceprint)."""
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from routers import auth as auth_router
from services import auth as auth_service
from services.token import SECRET, create_access_token, decode_access_token_with_iat


@pytest.fixture(autouse=True)
def _redis(monkeypatch, fake_redis):
    monkeypatch.setattr(auth_service, "get_redis", lambda: fake_redis)


def _request(**headers) -> Request:
    return Request({
        "type": "http",
        "headers": [(name.replace("_", "-").lower().encode(), value.encode()) for name, value in headers.items()],
    })


def _enroll_request(token: str) -> Request:
    return _request(authorization=f"Bearer {token}")


async def test_the_jwt_opens_enrollment_until_logout():
    token = create_access_token("user-1", ["voice", "otp"])
    assert await auth_service.get_enroll_user_id(_enroll_request(token)) == "user-1"

    await auth_service.end_login([await auth_service.create_session("user-1")])

    with pytest.raises(HTTPException) as refused:
        await auth_service.get_enroll_user_id(_enroll_request(token))
    assert refused.value.status_code == 401


async def test_logout_deletes_the_password_session():
    session = await auth_service.create_session("user-1")
    assert await auth_service.get_current_user_id(_enroll_request(session)) == "user-1"

    await auth_service.end_login([session])

    with pytest.raises(HTTPException):
        await auth_service.get_current_user_id(_enroll_request(session))


async def test_logout_with_only_the_jwt_revokes_it():
    # the 30-minute session can be gone while the 1-hour JWT is still alive
    token = create_access_token("user-1", ["recovery", "otp"])

    await auth_service.end_login([token])

    with pytest.raises(HTTPException):
        await auth_service.get_enroll_user_id(_enroll_request(token))


async def test_the_session_alone_is_enough_to_revoke_a_jwt_the_client_never_sent():
    # a copy of the JWT taken from the browser is revoked without the logout ever seeing it
    stolen = create_access_token("user-1", ["voice", "otp"])
    session = await auth_service.create_session("user-1")

    await auth_service.end_login([session])

    with pytest.raises(HTTPException):
        await auth_service.get_enroll_user_id(_enroll_request(stolen))


async def test_logout_leaves_other_users_tokens_alone():
    mine = create_access_token("user-1", ["voice", "otp"])
    theirs = create_access_token("user-2", ["voice", "otp"])

    await auth_service.end_login([mine])

    assert await auth_service.get_enroll_user_id(_enroll_request(theirs)) == "user-2"


async def test_a_token_issued_after_the_logout_is_accepted(fake_redis):
    token = create_access_token("user-1", ["voice", "otp"])
    issued_at = decode_access_token_with_iat(token)[1]
    await fake_redis.setex(auth_service._access_revoked_key("user-1"), 3600, issued_at - 1)

    assert await auth_service.get_enroll_user_id(_enroll_request(token)) == "user-1"


async def test_a_token_issued_in_the_same_second_as_the_logout_is_refused(fake_redis):
    # the cutoff has one-second resolution, so a tie goes against the token
    token = create_access_token("user-1", ["voice", "otp"])
    issued_at = decode_access_token_with_iat(token)[1]
    await fake_redis.setex(auth_service._access_revoked_key("user-1"), 3600, issued_at)

    with pytest.raises(HTTPException):
        await auth_service.get_enroll_user_id(_enroll_request(token))


async def test_a_token_from_before_the_iat_claim_is_revoked_by_any_logout():
    legacy = pyjwt.encode(
        {
            "sub": "user-1",
            "auth_methods": ["voice", "otp"],
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        SECRET,
        algorithm="HS256",
    )
    assert await auth_service.get_enroll_user_id(_enroll_request(legacy)) == "user-1"

    await auth_service.end_login([await auth_service.create_session("user-1")])

    with pytest.raises(HTTPException):
        await auth_service.get_enroll_user_id(_enroll_request(legacy))


async def test_the_cutoff_is_kept_at_least_as_long_as_a_token_can_live(fake_redis):
    # the cutoff and the token's expiry come from one constant; this fails if either is
    # ever changed on its own, because a cutoff that expires first brings revoked tokens back
    kept_for = {}
    original = fake_redis.setex

    async def spy(key, ttl, value):
        kept_for[key] = ttl
        await original(key, ttl, value)

    fake_redis.setex = spy
    token = create_access_token("user-1", ["voice", "otp"])

    await auth_service.end_login([token])

    claims = pyjwt.decode(token, SECRET, algorithms=["HS256"])
    assert kept_for[auth_service._access_revoked_key("user-1")] >= claims["exp"] - claims["iat"]


async def test_enrollment_reads_only_the_authorization_header():
    # X-Access-Token exists for logout alone. A request with an expired session in
    # Authorization and a perfectly good JWT in X-Access-Token is refused.
    good_jwt = create_access_token("user-1", ["voice", "otp"])

    with pytest.raises(HTTPException) as refused:
        await auth_service.get_enroll_user_id(
            _request(authorization="Bearer no-such-session", x_access_token=good_jwt)
        )
    assert refused.value.status_code == 401


async def test_a_revoked_jwt_is_refused_by_the_same_session_check_as_any_unknown_token():
    # the JWT layer declines it and the request falls through to the session lookup,
    # so a revoked token and a made-up one get the same 401 and the same message
    revoked = create_access_token("user-1", ["voice", "otp"])
    await auth_service.end_login([revoked])

    with pytest.raises(HTTPException) as from_revoked:
        await auth_service.get_enroll_user_id(_enroll_request(revoked))
    with pytest.raises(HTTPException) as from_unknown:
        await auth_service.get_enroll_user_id(_enroll_request("made-up"))

    assert (from_revoked.value.status_code, from_revoked.value.detail) == (
        from_unknown.value.status_code, from_unknown.value.detail
    ) == (401, "Phiên đăng nhập không hợp lệ hoặc đã hết hạn")


async def test_logout_with_unknown_tokens_changes_nothing(fake_redis):
    await auth_service.end_login(["not-a-session", "not.a.jwt"])
    assert not await fake_redis.exists(auth_service._access_revoked_key("user-1"))


async def test_logout_with_no_tokens_is_harmless():
    await auth_service.end_login([])


async def test_the_endpoint_ends_the_session_and_revokes_the_jwt_sent_in_the_header():
    session = await auth_service.create_session("user-1")
    token = create_access_token("user-1", ["voice", "otp"])

    result = await auth_router.logout(_request(authorization=f"Bearer {session}", x_access_token=token))

    assert result == {"message": "Đã đăng xuất"}
    with pytest.raises(HTTPException):
        await auth_service.get_current_user_id(_enroll_request(session))
    with pytest.raises(HTTPException):
        await auth_service.get_enroll_user_id(_enroll_request(token))


async def test_the_endpoint_still_answers_without_any_credentials():
    assert await auth_router.logout(_request()) == {"message": "Đã đăng xuất"}
