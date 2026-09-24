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

# Shared by /verify (via evaluate) and enrollment, so both gates agree on what
# counts as a suspected synthetic/replayed voice.
SPOOF_THRESHOLD = 0.5

# Minimum speaker-match (cosine similarity) score, and the stricter bar used
# when the surrounding context looks unusual. Named so the evaluation script
# (scripts/evaluate_thresholds.py) measures exactly the values that are deployed.
#
# The strict bar was first 0.85, an unmeasured default. In-app genuine takes
# scored 0.80-0.82 on average against a 3-5 sample voiceprint, so 0.85 would have
# rejected 65-73% of them (0.85 also happened to be ADAPT_MIN_SCORE below). 0.78
# is the smallest value at which none of the 16 impostor and 10 clone clips was
# accepted in 300 random templates (the highest scored 0.777), while rejecting
# about 23-33% of genuine takes -- acceptable for a bar that only applies in an
# unusual context. The impostor clips came from other recording domains and only
# a few speakers, so treat the false-acceptance side as indicative.
MATCH_THRESHOLD = 0.7
STRICT_MATCH_THRESHOLD = 0.78

# Rejections that are about recording quality, not about who is speaking: a
# bad mic, a misread phrase. They stay in voice_auth_attempts as an audit trail
# but must not count toward the 5-in-10-minutes security throttle, otherwise a
# genuine user with a poor microphone is locked out by their own retries. This
# is safe because these attempts are still capped per IP by the /verify rate
# limit, each challenge phrase is single-use, and neither reason involves a
# voiceprint score, so they leak nothing an attacker could tune against.
QUALITY_FAILURE_REASONS = frozenset({"audio_too_quiet", "phrase_mismatch"})

# A successful verify nudges the stored voiceprint toward the new sample so it
# can follow natural voice change. Adapting from any sample that merely cleared
# the 0.7 match bar would let a borderline impostor/clone drag the template
# toward itself, making the next attempt easier -- so only confident matches
# are allowed to move it. Sessions scoring 0.7-0.85 still pass; they just don't
# update the template.
ADAPT_MIN_SCORE = 0.85


def should_adapt_voiceprint(voiceprint_score: float) -> bool:
    return voiceprint_score >= ADAPT_MIN_SCORE


def fraud_lock_key(user_id: str) -> str:
    return f"fraud_locked:{user_id}"


async def evaluate(
    user_id: str,
    voiceprint_score: float,
    spoof_score: float,
    context: dict
) -> tuple[str, str | None, dict]:

    recent_failures = await count_recent_failures(
        user_id, minutes=10, exclude_reasons=QUALITY_FAILURE_REASONS
    )
    if recent_failures >= MAX_FAILURES:
        return "rejected", "rate_limit_exceeded", {}

    if await get_redis().exists(fraud_lock_key(user_id)):
        return "rejected", "fraud_lockout", {}

    if spoof_score >= SPOOF_THRESHOLD:
        recent_fraud = await count_recent_fraud_failures(user_id, minutes=FRAUD_DETECTION_WINDOW_MINUTES)
        if recent_fraud + 1 >= MAX_FRAUD_ATTEMPTS:
            await get_redis().set(fraud_lock_key(user_id), "1")
            return "rejected", "fraud_lockout", {}
        return "rejected", "spoofing_detected", {"tries_left": MAX_FRAUD_ATTEMPTS - recent_fraud - 1}

    if voiceprint_score < MATCH_THRESHOLD:
        return "rejected", "voiceprint_mismatch", {}

    if _is_suspicious_context(context):
        if voiceprint_score < STRICT_MATCH_THRESHOLD:
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
