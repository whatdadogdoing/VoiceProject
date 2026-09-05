from models.db import count_recent_failures, count_recent_fraud_failures
from services.redis_client import get_redis

MAX_FAILURES = 5

# Spoofing is treated as a suspected active attack, not an honest mistake: 3
# detections within the window below trips a lock that does NOT expire on its
# own -- unlike the general retry limit above (which just covers a bad mic or
# a misread phrase), an attacker shouldn't be able to just wait this out. The
# only way out is a successful recovery-OTP verify (see
# routers/voice_auth.py's verify_recovery_otp, which clears the lock key).
MAX_FRAUD_ATTEMPTS = 3
FRAUD_DETECTION_WINDOW_MINUTES = 30


def fraud_lock_key(user_id: str) -> str:
    return f"fraud_locked:{user_id}"


async def evaluate(
    user_id: str,
    voiceprint_score: float,
    spoof_score: float,
    context: dict
) -> tuple[str, str | None, dict]:

    recent_failures = await count_recent_failures(user_id, minutes=10)
    if recent_failures >= MAX_FAILURES:
        return "rejected", "rate_limit_exceeded", {}

    if await get_redis().exists(fraud_lock_key(user_id)):
        return "rejected", "fraud_lockout", {}

    if spoof_score >= 0.5:
        recent_fraud = await count_recent_fraud_failures(user_id, minutes=FRAUD_DETECTION_WINDOW_MINUTES)
        if recent_fraud + 1 >= MAX_FRAUD_ATTEMPTS:
            await get_redis().set(fraud_lock_key(user_id), "1")
            return "rejected", "fraud_lockout", {}
        return "rejected", "spoofing_detected", {"tries_left": MAX_FRAUD_ATTEMPTS - recent_fraud - 1}

    if voiceprint_score < 0.7:
        return "rejected", "voiceprint_mismatch", {}

    if _is_suspicious_context(context):
        if voiceprint_score < 0.85:
            return "rejected", "suspicious_context", {}

    return "mfa_required", None, {}


def _is_suspicious_context(context: dict) -> bool:
    hour = context.get("hour_of_day", 12)
    is_new_device = context.get("is_new_device", False)
    is_new_ip = context.get("is_new_ip", False)

    if 1 <= hour <= 5 and (is_new_device or is_new_ip):
        return True
    if is_new_device and is_new_ip:
        return True
    return False
