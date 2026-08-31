from models.db import count_recent_failures, count_recent_fraud_failures

MAX_FAILURES = 5

# Spoofing is treated as a suspected-fraud signal, not an honest mistake, so it
# gets its own much stricter counter and a longer lockout window than the
# general retry limit above (which covers things like a bad mic or a misread
# phrase, where people can just try again).
MAX_FRAUD_ATTEMPTS = 3
FRAUD_LOCKOUT_MINUTES = 30


async def evaluate(
    user_id: str,
    voiceprint_score: float,
    spoof_score: float,
    context: dict
) -> tuple[str, str | None, dict]:

    recent_failures = await count_recent_failures(user_id, minutes=10)
    if recent_failures >= MAX_FAILURES:
        return "rejected", "rate_limit_exceeded", {}

    recent_fraud = await count_recent_fraud_failures(user_id, minutes=FRAUD_LOCKOUT_MINUTES)
    if recent_fraud >= MAX_FRAUD_ATTEMPTS:
        return "rejected", "fraud_lockout", {"lockout_minutes": FRAUD_LOCKOUT_MINUTES}

    if spoof_score >= 0.5:
        tries_left = MAX_FRAUD_ATTEMPTS - recent_fraud - 1
        return "rejected", "spoofing_detected", {"tries_left": tries_left}

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
