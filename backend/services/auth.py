import secrets
import time
import bcrypt
import jwt
from fastapi import Request, HTTPException
from services.redis_client import get_redis
from services.token import ACCESS_TOKEN_LIFETIME, decode_access_token_with_iat
from models.db import has_voiceprint

SESSION_TTL_SECONDS = 1800

LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60

# Used in place of a real hash when the email doesn't exist, so verify_password
# still runs its full bcrypt comparison either way -- otherwise a login for an
# unknown email returns near-instantly while a wrong password pays the bcrypt
# cost, and that timing gap is enough to enumerate registered emails.
DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"no-such-account", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def _login_attempts_key(email: str) -> str:
    return f"login_attempts:{email.strip().lower()}"


async def is_login_locked(email: str) -> bool:
    attempts = await get_redis().get(_login_attempts_key(email))
    return int(attempts or 0) >= LOGIN_MAX_ATTEMPTS


async def record_login_failure(email: str) -> None:
    key = _login_attempts_key(email)
    redis_client = get_redis()
    attempts = await redis_client.incr(key)
    if attempts == 1:
        await redis_client.expire(key, LOGIN_LOCKOUT_SECONDS)


async def clear_login_failures(email: str) -> None:
    await get_redis().delete(_login_attempts_key(email))


async def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    await get_redis().setex(f"session:{token}", SESSION_TTL_SECONDS, user_id)
    return token


async def invalidate_session(token: str) -> None:
    await get_redis().delete(f"session:{token}")


def _access_revoked_key(user_id: str) -> str:
    return f"access_revoked_at:{user_id}"


async def revoke_access_tokens(user_id: str) -> None:
    """Invalidate every access token issued to this user up to now. A JWT can't be
    looked up and deleted like a session, so logout records a cutoff time instead
    and get_enroll_user_id refuses any token issued at or before it. The key only
    has to outlive the longest-lived token, so it expires with them."""
    await get_redis().setex(
        _access_revoked_key(user_id), int(ACCESS_TOKEN_LIFETIME.total_seconds()), int(time.time())
    )


async def _access_token_revoked(user_id: str, issued_at: int) -> bool:
    cutoff = await get_redis().get(_access_revoked_key(user_id))
    return cutoff is not None and issued_at <= int(cutoff)


async def end_login(tokens: list[str]) -> None:
    """Logout. Each token may be the password-session token or the post-MFA JWT
    (the client holds both, and swaps the JWT in while re-enrolling). The session
    is deleted, and everything issued to the same user as a JWT is revoked, so a
    copy of the JWT taken from the browser stops working too. The user is found
    from whichever token still resolves: the 30-minute session may be gone while
    the 1-hour JWT is alive."""
    redis_client = get_redis()
    user_ids = set()
    for token in tokens:
        user_id = await redis_client.get(f"session:{token}")
        if user_id:
            user_ids.add(user_id.decode())
        await invalidate_session(token)
        try:
            user_ids.add(decode_access_token_with_iat(token)[0])
        except jwt.PyJWTError:
            pass
    for user_id in user_ids:
        await revoke_access_tokens(user_id)


async def get_current_user_id(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Thiếu thông tin đăng nhập")

    token = auth_header.removeprefix("Bearer ").strip()
    user_id = await get_redis().get(f"session:{token}")
    if not user_id:
        raise HTTPException(401, "Phiên đăng nhập không hợp lệ hoặc đã hết hạn")
    return user_id.decode()


async def get_enroll_user_id(request: Request) -> str:
    """Auth gate for enrollment. A brand-new user (no voiceprint yet) has
    nothing to protect, so the ordinary password-session token is enough.
    Once a voiceprint exists, re-enrollment must instead present the
    post-MFA access token (proof of a completed voice+OTP verify) — otherwise
    anyone who obtains the account password could silently overwrite the
    owner's voiceprint with their own."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        try:
            user_id, issued_at = decode_access_token_with_iat(token)
        except jwt.PyJWTError:
            pass
        else:
            # a logged-out token is refused here and then fails the session lookup below
            if not await _access_token_revoked(user_id, issued_at):
                return user_id

    user_id = await get_current_user_id(request)
    if await has_voiceprint(user_id):
        raise HTTPException(
            403,
            "Bạn đã đăng ký giọng nói trước đó. Hãy xác thực lại bằng giọng nói và OTP để đăng ký lại."
        )
    return user_id
