import asyncio
import json
from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Depends
from pydantic import BaseModel
from services.speaker_verification import embed, cosine_similarity, adapt_embedding
from services.anti_spoofing import analyze
from services.phrase_check import check_phrase
from services.audio import to_wav_pcm16, is_too_quiet
from services.risk_engine import evaluate, fraud_lock_key, should_adapt_voiceprint
from services.otp import send_otp, verify_otp
from services.token import create_access_token
from services.consent import has_active_consent, record_consent, revoke_consent
from services.rate_limiter import limiter
from services.auth import get_current_user_id
from services.redis_client import get_redis
from services.phrases import next_verify_phrase
from models.db import (
    get_user_embedding, get_user_email, has_voiceprint, log_attempt,
    is_known_device, is_known_ip, update_voiceprint_embedding
)
from utils import get_client_ip, get_device_fingerprint, local_hour

router = APIRouter(prefix="/api/voice-auth")

VOICE_PASSED_TTL_SECONDS = 300
VERIFY_PHRASE_TTL_SECONDS = 120


class OtpVerifyRequest(BaseModel):
    code: str


@router.get("/status")
async def status(user_id: str = Depends(get_current_user_id)):
    return {
        "has_consent": await has_active_consent(user_id),
        "has_voiceprint": await has_voiceprint(user_id)
    }


@router.post("/consent/accept")
@limiter.limit("5/minute")
async def accept_consent(request: Request, user_id: str = Depends(get_current_user_id)):
    await record_consent(
        user_id,
        ip=get_client_ip(request),
        user_agent=request.headers.get("User-Agent", "")
    )
    return {"message": "Đã ghi nhận đồng ý"}


@router.post("/consent/revoke")
@limiter.limit("5/minute")
async def revoke_consent_endpoint(request: Request, user_id: str = Depends(get_current_user_id)):
    await revoke_consent(user_id)
    return {"message": "Đã rút lại đồng ý, voiceprint của bạn đã bị vô hiệu hoá"}


@router.post("/verify/prompt")
@limiter.limit("10/minute")
async def verify_prompt(request: Request, user_id: str = Depends(get_current_user_id)):
    phrase = await next_verify_phrase(user_id)
    await get_redis().setex(f"verify_phrase:{user_id}", VERIFY_PHRASE_TTL_SECONDS, phrase)
    return {"phrase": phrase}


@router.post("/verify")
@limiter.limit("5/minute")
async def verify(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    audio: UploadFile = File(...)
):
    if not await has_active_consent(user_id):
        raise HTTPException(403, "Bạn chưa đồng ý cho phép sử dụng dữ liệu giọng nói")

    expected_phrase_raw = await get_redis().get(f"verify_phrase:{user_id}")
    if not expected_phrase_raw:
        raise HTTPException(400, "Chưa có câu để đọc, hãy gọi /verify/prompt trước")
    await get_redis().delete(f"verify_phrase:{user_id}")
    expected_phrase = expected_phrase_raw.decode()

    audio_bytes = await audio.read()
    if len(audio_bytes) > 5 * 1024 * 1024:
        raise HTTPException(400, "Audio quá lớn")

    enrolled_embedding = await get_user_embedding(user_id)
    if not enrolled_embedding:
        raise HTTPException(404, "User chưa đăng ký voiceprint")

    client_ip = get_client_ip(request)
    device_fp = get_device_fingerprint(request)

    try:
        wav_bytes = await asyncio.to_thread(to_wav_pcm16, audio_bytes)
    except Exception:
        raise HTTPException(400, "Không đọc được file âm thanh, hãy thử ghi âm lại")

    # Quality gate before the phrase check: garbled speech-to-text on a too-quiet
    # recording would otherwise surface as a confusing "wrong phrase" rejection.
    if await asyncio.to_thread(is_too_quiet, wav_bytes):
        await log_attempt(
            user_id, None, None, "rejected", "audio_too_quiet",
            ip=client_ip, device=device_fp
        )
        return {"decision": "rejected", "reason": "audio_too_quiet"}

    if not await asyncio.to_thread(check_phrase, wav_bytes, expected_phrase):
        await log_attempt(
            user_id, None, None, "rejected", "phrase_mismatch",
            ip=client_ip, device=device_fp
        )
        return {"decision": "rejected", "reason": "phrase_mismatch"}

    sample_embedding, spoof_score = await asyncio.gather(
        asyncio.to_thread(embed, wav_bytes),
        asyncio.to_thread(analyze, wav_bytes)
    )
    voiceprint_score = cosine_similarity(enrolled_embedding, sample_embedding)

    context = {
        "hour_of_day": local_hour(),
        "is_new_device": not await is_known_device(user_id, device_fp),
        "is_new_ip": not await is_known_ip(user_id, client_ip)
    }

    decision, reason, meta = await evaluate(user_id, voiceprint_score, spoof_score, context)

    await log_attempt(
        user_id, voiceprint_score, spoof_score, decision, reason,
        ip=client_ip,
        device=device_fp
    )

    if decision == "mfa_required":
        await get_redis().setex(f"voice_passed:{user_id}", VOICE_PASSED_TTL_SECONDS, "1")
        # held until OTP also succeeds, so the stored voiceprint only ever
        # adapts toward a sample that cleared both auth factors -- and only a
        # confident match is kept at all: a borderline sample that scraped past
        # the match threshold must not be able to pull the template toward
        # itself (verify_otp_endpoint simply skips adaptation when nothing is
        # cached here).
        if should_adapt_voiceprint(voiceprint_score):
            await get_redis().setex(
                f"voice_sample_embedding:{user_id}", VOICE_PASSED_TTL_SECONDS, json.dumps(sample_embedding)
            )

    return {"decision": decision, "reason": reason, **meta}


@router.post("/otp/send")
@limiter.limit("3/minute")
async def send_otp_endpoint(request: Request, user_id: str = Depends(get_current_user_id)):
    if not await get_redis().exists(f"voice_passed:{user_id}"):
        raise HTTPException(403, "Bạn cần xác thực giọng nói thành công trước khi nhận OTP")

    email = await get_user_email(user_id)
    if not email:
        raise HTTPException(404, "User không tồn tại")
    await send_otp(user_id, email)
    return {"message": "OTP đã được gửi"}


@router.post("/otp/verify")
@limiter.limit("5/minute")
async def verify_otp_endpoint(request: Request, body: OtpVerifyRequest, user_id: str = Depends(get_current_user_id)):
    if not await get_redis().exists(f"voice_passed:{user_id}"):
        raise HTTPException(403, "Phiên xác thực giọng nói đã hết hạn, vui lòng xác thực lại")

    ok = await verify_otp(user_id, body.code)
    if not ok:
        raise HTTPException(401, "OTP không hợp lệ hoặc đã hết hạn")

    await get_redis().delete(f"voice_passed:{user_id}")

    raw_sample = await get_redis().get(f"voice_sample_embedding:{user_id}")
    if raw_sample:
        await get_redis().delete(f"voice_sample_embedding:{user_id}")
        enrolled_embedding = await get_user_embedding(user_id)
        if enrolled_embedding:
            adapted = adapt_embedding(enrolled_embedding, json.loads(raw_sample))
            await update_voiceprint_embedding(user_id, adapted)

    token = create_access_token(user_id, ["voice", "otp"])
    return {"access_token": token, "token_type": "bearer"}


@router.post("/recovery/otp/send")
@limiter.limit("3/minute")
async def send_recovery_otp(request: Request, user_id: str = Depends(get_current_user_id)):
    """When voice verification won't cooperate for the account owner -- either
    it keeps failing, or a suspected-fraud lock has closed off voice attempts
    entirely -- email OTP alone (on top of the password session already
    required to get here) is accepted as a fallback path."""
    email = await get_user_email(user_id)
    if not email:
        raise HTTPException(404, "User không tồn tại")
    await send_otp(user_id, email)
    return {"message": "OTP đã được gửi"}


@router.post("/recovery/otp/verify")
@limiter.limit("5/minute")
async def verify_recovery_otp(request: Request, body: OtpVerifyRequest, user_id: str = Depends(get_current_user_id)):
    ok = await verify_otp(user_id, body.code)
    if not ok:
        raise HTTPException(401, "OTP không hợp lệ hoặc đã hết hạn")

    # a successful recovery OTP proves ownership regardless of why it was
    # needed, so it also lifts a suspected-fraud lock if one is active
    await get_redis().delete(fraud_lock_key(user_id))

    token = create_access_token(user_id, ["recovery", "otp"])
    return {"access_token": token, "token_type": "bearer"}
