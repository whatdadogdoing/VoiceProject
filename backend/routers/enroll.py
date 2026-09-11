import asyncio
import json
from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Depends
from services.speaker_verification import embed, average_embedding, ENROLL_SAMPLES_REQUIRED
from services.phrases import random_phrases, another_phrase, matches_phrase
from services.stt import transcribe
from services.audio import to_wav_pcm16
from services.consent import has_active_consent
from services.rate_limiter import limiter
from services.auth import get_enroll_user_id
from services.redis_client import get_redis
from models.db import save_voiceprint, has_voiceprint

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

    server_transcript = await asyncio.to_thread(transcribe, wav_bytes)
    if not matches_phrase(expected_phrase, server_transcript):
        return {
            "status": "phrase_mismatch",
            "message": "Câu đọc không khớp với câu được yêu cầu, hãy thử lại",
            "phrase": expected_phrase
        }

    sample_embedding = await asyncio.to_thread(embed, wav_bytes)
    state["embeddings"].append(sample_embedding)

    if len(state["embeddings"]) >= ENROLL_SAMPLES_REQUIRED:
        voiceprint = average_embedding(state["embeddings"])
        is_reenroll = await has_voiceprint(user_id)
        await save_voiceprint(user_id, voiceprint, replace_existing=is_reenroll)
        await get_redis().delete(f"enroll:{user_id}")
        return {"status": "enrolled", "message": "Đăng ký voiceprint thành công"}

    await get_redis().setex(f"enroll:{user_id}", 600, json.dumps(state))
    return {
        "status": "enrolling",
        "samples_submitted": len(state["embeddings"]),
        "samples_required": ENROLL_SAMPLES_REQUIRED,
        "next_phrase": state["phrases"][len(state["embeddings"])]
    }
