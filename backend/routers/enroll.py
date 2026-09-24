import asyncio
import json
import logging
from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Depends
from services.speaker_verification import embed, average_embedding, ENROLL_SAMPLES_REQUIRED, ENCODER_ID
from services.phrases import random_phrases, another_phrase
from services.phrase_check import check_phrase
from services.debug_capture import save_rejected
from services.anti_spoofing import analyze
from services.risk_engine import SPOOF_THRESHOLD
from services.audio import to_wav_pcm16
from services.consent import has_active_consent
from services.rate_limiter import limiter
from services.auth import get_enroll_user_id
from services.redis_client import get_redis
from models.db import save_voiceprint, has_voiceprint, log_attempt
from utils import get_client_ip, get_device_fingerprint

# uvicorn configures this logger at INFO; the root/module loggers aren't, so an
# unconfigured logging.getLogger(__name__) would silently drop these lines.
logger = logging.getLogger("uvicorn.error")

# A sample rejected as a suspected spoof is deliberately NOT written to
# voice_auth_attempts (see submit_sample), which left enrollment with no counter
# beyond the 10/minute per-IP rate limit -- and the "spoof_detected" answer is a
# pass/fail oracle someone could probe. So these are counted separately, per
# user, and kept apart from /verify's fraud lock: enough rejections in an hour
# block further samples until the window passes. Generous on purpose: with the
# current level normalization genuine takes are rarely flagged.
ENROLL_SPOOF_LIMIT = 10
ENROLL_SPOOF_WINDOW_SECONDS = 3600


def _spoof_counter_key(user_id: str) -> str:
    return f"enroll_spoof:{user_id}"


router = APIRouter(prefix="/api/voice-auth/enroll")


@router.post("/start")
@limiter.limit("5/minute")
async def start_enroll(request: Request, user_id: str = Depends(get_enroll_user_id)):
    if not await has_active_consent(user_id):
        raise HTTPException(403, "Bạn chưa đồng ý cho phép sử dụng dữ liệu giọng nói")

    phrases = random_phrases(ENROLL_SAMPLES_REQUIRED)
    state = {"embeddings": [], "phrases": phrases}
    await get_redis().setex(f"enroll:{user_id}", 600, json.dumps(state))

    return {
        "message": "Bắt đầu đăng ký, hãy đọc to các câu được yêu cầu",
        "samples_required": ENROLL_SAMPLES_REQUIRED,
        "phrases": phrases
    }


@router.post("/reshuffle")
@limiter.limit("10/minute")
async def reshuffle_phrase(request: Request, user_id: str = Depends(get_enroll_user_id)):
    raw = await get_redis().get(f"enroll:{user_id}")
    if not raw:
        raise HTTPException(400, "Phiên đăng ký không tồn tại hoặc đã hết hạn, hãy bắt đầu lại")

    state = json.loads(raw)
    sample_index = len(state["embeddings"])
    if sample_index >= len(state["phrases"]):
        raise HTTPException(400, "Đã hoàn thành đủ số mẫu yêu cầu")

    new_phrase = another_phrase(state["phrases"])
    state["phrases"][sample_index] = new_phrase
    await get_redis().setex(f"enroll:{user_id}", 600, json.dumps(state))
    return {"phrase": new_phrase}


@router.post("/sample")
@limiter.limit("10/minute")
async def submit_sample(
    request: Request,
    user_id: str = Depends(get_enroll_user_id),
    audio: UploadFile = File(...)
):
    if not await has_active_consent(user_id):
        raise HTTPException(403, "Bạn chưa đồng ý cho phép sử dụng dữ liệu giọng nói")

    strikes = await get_redis().get(_spoof_counter_key(user_id))
    if strikes is not None and int(strikes) >= ENROLL_SPOOF_LIMIT:
        logger.warning("enrollment blocked: too many suspected-spoof samples (user=%s)", user_id)
        raise HTTPException(429, "Có quá nhiều mẫu bị nghi giả mạo trong giờ qua, hãy thử lại sau")

    raw = await get_redis().get(f"enroll:{user_id}")
    if not raw:
        raise HTTPException(400, "Phiên đăng ký không tồn tại hoặc đã hết hạn, hãy bắt đầu lại")

    state = json.loads(raw)
    sample_index = len(state["embeddings"])
    expected_phrase = state["phrases"][sample_index]

    audio_bytes = await audio.read()
    if len(audio_bytes) > 5 * 1024 * 1024:
        raise HTTPException(400, "Audio quá lớn")

    try:
        wav_bytes = await asyncio.to_thread(to_wav_pcm16, audio_bytes)
    except Exception:
        raise HTTPException(400, "Không đọc được file âm thanh, hãy thử ghi âm lại")

    if not await asyncio.to_thread(check_phrase, wav_bytes, expected_phrase):
        return {
            "status": "phrase_mismatch",
            "message": "Câu đọc không khớp với câu được yêu cầu, hãy thử lại",
            "phrase": expected_phrase
        }

    # Same anti-spoofing model /verify uses. Without this, a synthetic or
    # replayed recording could become the stored template itself.
    sample_embedding, spoof_score = await asyncio.gather(
        asyncio.to_thread(embed, wav_bytes),
        asyncio.to_thread(analyze, wav_bytes)
    )
    if spoof_score >= SPOOF_THRESHOLD:
        # Deliberately not written to voice_auth_attempts: a rejected row would
        # feed the fraud-lockout counter and the "known device" logic, and an
        # enrollment retry isn't an authentication attack. The score goes to
        # the server log only (never to the client) so the threshold can be
        # calibrated against real recordings.
        logger.info("enrollment sample rejected as suspected spoof (user=%s, score=%.3f)", user_id, spoof_score)
        save_rejected(wav_bytes, "enroll-spoof", spoof_score)
        # refreshed on every strike, so the block lasts an hour after the last one
        # and the counter can never be left without an expiry
        await get_redis().incr(_spoof_counter_key(user_id))
        await get_redis().expire(_spoof_counter_key(user_id), ENROLL_SPOOF_WINDOW_SECONDS)
        return {
            "status": "spoof_detected",
            "message": "Giọng thu được có dấu hiệu là giọng tổng hợp hoặc bản ghi lại. "
                       "Hãy đọc trực tiếp vào micro bằng giọng thật của bạn, ở nơi yên tĩnh, rồi thử lại",
            "phrase": expected_phrase
        }

    state["embeddings"].append(sample_embedding)

    if len(state["embeddings"]) >= ENROLL_SAMPLES_REQUIRED:
        voiceprint = average_embedding(state["embeddings"])
        is_reenroll = await has_voiceprint(user_id)
        await save_voiceprint(user_id, voiceprint, model_id=ENCODER_ID, replace_existing=is_reenroll)
        await get_redis().delete(f"enroll:{user_id}")
        # Enrolling took a password session + consent from this very browser, so
        # treat it as the first trusted device/IP. Otherwise a brand-new user has
        # no history and their first verify is always "new device AND new IP",
        # which raises the required match score from 0.7 to 0.85 exactly when a
        # genuine user is least likely to clear it.
        await log_attempt(
            user_id, None, None, "enrolled", None,
            ip=get_client_ip(request) or None,
            device=get_device_fingerprint(request)
        )
        return {"status": "enrolled", "message": "Đăng ký voiceprint thành công"}

    await get_redis().setex(f"enroll:{user_id}", 600, json.dumps(state))
    return {
        "status": "enrolling",
        "samples_submitted": len(state["embeddings"]),
        "samples_required": ENROLL_SAMPLES_REQUIRED,
        "next_phrase": state["phrases"][len(state["embeddings"])]
    }
