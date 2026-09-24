import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from routers.auth import router as auth_router
from routers.voice_auth import router as voice_auth_router
from routers.enroll import router as enroll_router
from services.rate_limiter import limiter
from services.anti_spoofing import load_model as load_antispoofing_model
from services.speaker_verification import load_encoder as load_speaker_encoder
from services.stt import load_models as load_speech_models
from models.db import get_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    load_antispoofing_model()
    load_speaker_encoder()
    load_speech_models()
    yield


app = FastAPI(title="Voice Auth API", lifespan=lifespan)
app.state.limiter = limiter

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Device-Id"],
)

app.include_router(auth_router)
app.include_router(voice_auth_router)
app.include_router(enroll_router)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Quá nhiều yêu cầu, thử lại sau"})
