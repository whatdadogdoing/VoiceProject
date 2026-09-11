import secrets
import bcrypt
import jwt
from fastapi import Request, HTTPException
from services.redis_client import get_redis
from services.token import decode_access_token
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
            return decode_access_token(token)
        except jwt.PyJWTError:
            pass

    user_id = await get_current_user_id(request)
    if await has_voiceprint(user_id):
        raise HTTPException(
            403,
            "Bạn đã đăng ký giọng nói trước đó. Hãy xác thực lại bằng giọng nói và OTP để đăng ký lại."
        )
    return user_id
