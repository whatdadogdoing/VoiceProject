import secrets
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
import os
from services.redis_client import get_redis

# What limits OTP abuse is split across two files, so both are named here:
#  - this module: a code lives OTP_TTL_SECONDS, and after MAX_OTP_ATTEMPTS wrong
#    guesses it is invalidated outright rather than left to expire;
#  - routers/voice_auth.py: how often a code can be requested or checked, as
#    @limiter.limit decorators on the endpoints -- /otp/send and /recovery/otp/send
#    at 3 per minute, /otp/verify and /recovery/otp/verify at 5 per minute.
MAX_OTP_ATTEMPTS = 5
OTP_TTL_SECONDS = 300

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
    await redis_client.delete(f"otp_attempts:{user_id}")

    message = MessageSchema(
        subject="Mã xác thực của bạn",
        recipients=[email],
        body=f"Mã OTP của bạn là: {code}\nMã có hiệu lực trong 5 phút.",
        subtype=MessageType.plain
    )
    await FastMail(mail_config).send_message(message)


async def verify_otp(user_id: str, code: str) -> bool:
    redis_client = get_redis()
    attempts_key = f"otp_attempts:{user_id}"

    attempts = int(await redis_client.get(attempts_key) or 0)
    if attempts >= MAX_OTP_ATTEMPTS:
        await redis_client.delete(f"otp:{user_id}")
        return False

    stored = await redis_client.get(f"otp:{user_id}")
    if not stored:
        return False

    if stored.decode() != code:
        await redis_client.incr(attempts_key)
        await redis_client.expire(attempts_key, OTP_TTL_SECONDS)
        return False

    await redis_client.delete(f"otp:{user_id}")
    await redis_client.delete(attempts_key)
    return True
