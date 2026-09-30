import secrets
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
import os
from services.redis_client import get_redis

# What limits OTP abuse is split across two files, so both are named here:
#  - this module: a code lives OTP_TTL_SECONDS; wrong guesses count toward
#    MAX_OTP_ATTEMPTS per OTP_FAIL_WINDOW_SECONDS, and that counter survives
#    requesting a new code (see the comment on send_otp below);
#  - routers/voice_auth.py: how often a code can be requested or checked, as
#    @limiter.limit decorators on the endpoints -- /otp/send and /recovery/otp/send
#    at 3 per minute, /otp/verify and /recovery/otp/verify at 5 per minute -- but
#    that limit is per IP, so it alone doesn't bound guesses against one account.
MAX_OTP_ATTEMPTS = 5
OTP_TTL_SECONDS = 300
# The failure counter's own window: resending a code used to reset it to 0, so
# with the per-IP limits above an attacker could get roughly 3 sends/min x 5
# guesses/send = 15 guesses/min against one account, and reset that budget
# again just by switching IP. Counting failures per account for a full hour
# instead, independent of how many codes were requested in between, caps total
# guesses at MAX_OTP_ATTEMPTS regardless of resends or source IP.
OTP_FAIL_WINDOW_SECONDS = 3600

mail_config = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_FROM=os.getenv("MAIL_FROM"),
    MAIL_PORT=587,
    MAIL_SERVER="smtp.gmail.com",
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    MAIL_DEBUG=False
)


async def send_otp(user_id: str, email: str) -> None:
    code = f"{secrets.randbelow(1_000_000):06d}"
    redis_client = get_redis()
    await redis_client.setex(f"otp:{user_id}", OTP_TTL_SECONDS, code)
    # Deliberately does NOT touch otp_fail_count: it counts wrong guesses over
    # OTP_FAIL_WINDOW_SECONDS regardless of how many codes were sent in that
    # time, otherwise resending (rate-limited only per IP, not per account)
    # would hand back a fresh guessing budget every time.

    message = MessageSchema(
        subject="Mã xác thực của bạn",
        recipients=[email],
        body=f"Mã OTP của bạn là: {code}\nMã có hiệu lực trong 5 phút.",
        subtype=MessageType.plain
    )
    await FastMail(mail_config).send_message(message)


async def verify_otp(user_id: str, code: str) -> bool:
    redis_client = get_redis()
    fail_key = f"otp_fail_count:{user_id}"

    fails = int(await redis_client.get(fail_key) or 0)
    if fails >= MAX_OTP_ATTEMPTS:
        await redis_client.delete(f"otp:{user_id}")
        return False

    stored = await redis_client.get(f"otp:{user_id}")
    if not stored:
        return False

    # constant-time comparison: a plain != leaks, in principle, how many
    # leading digits matched through how long the comparison took
    if not secrets.compare_digest(stored.decode(), code):
        fails = await redis_client.incr(fail_key)
        if fails == 1:
            # only the first failure starts the window, so a trickle of wrong
            # guesses can't keep pushing it back and hold the lock open forever
            await redis_client.expire(fail_key, OTP_FAIL_WINDOW_SECONDS)
        return False

    await redis_client.delete(f"otp:{user_id}")
    await redis_client.delete(fail_key)
    return True
