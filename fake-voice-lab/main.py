"""Voice-clone lab: record a reference clip of your own voice, synthesize any
sentence in that voice via VieNeu-TTS's zero-shot cloning, and optionally fire
the result straight at the real voice-auth backend's /verify endpoint to see
if it gets caught.

Talks to the real backend server-to-server (not from the browser), so the
real app's CORS policy is irrelevant here -- this process makes its own HTTP
calls, the same way an actual attacker script would.
"""
import asyncio
import io
import os
import tempfile

import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydub import AudioSegment

DATA_DIR = "/app/data"
REFERENCE_PATH = os.path.join(DATA_DIR, "reference.wav")
AUTH_BACKEND_URL = os.environ.get("AUTH_BACKEND_URL", "http://backend:8000")

os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI(title="Fake Voice Lab")

_tts = None
_attack_state = {"session_token": None, "email": None}
_last_wav_bytes: bytes | None = None

DOWNLOAD_FORMATS = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "mp4": "audio/mp4",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
}


def load_model():
    global _tts
    if _tts is None:
        from vieneu import Vieneu
        _tts = Vieneu(backend="onnx")  # torch-free CPU path
    return _tts


def synthesize_wav(text: str) -> bytes:
    if not os.path.isfile(REFERENCE_PATH):
        raise HTTPException(400, "Chưa có mẫu giọng nói tham chiếu, hãy ghi âm trước")

    tts = load_model()
    audio = tts.infer(text, ref_audio=REFERENCE_PATH, denoise=True)

    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        tts.save(audio, tmp.name)
        tmp.seek(0)
        return tmp.read()


@app.post("/api/reference")
async def upload_reference(audio: UploadFile = File(...)):
    raw = await audio.read()
    segment = AudioSegment.from_file(io.BytesIO(raw))
    segment = segment.set_frame_rate(24000).set_channels(1).set_sample_width(2)
    segment.export(REFERENCE_PATH, format="wav")
    return {"status": "saved"}


@app.get("/api/reference/status")
async def reference_status():
    return {"has_reference": os.path.isfile(REFERENCE_PATH)}


@app.post("/api/synthesize")
async def synthesize(text: str = Form(...)):
    global _last_wav_bytes
    wav_bytes = await asyncio.to_thread(synthesize_wav, text)
    _last_wav_bytes = wav_bytes
    return Response(content=wav_bytes, media_type="audio/wav")


@app.get("/api/download")
async def download(format: str = "wav"):
    if _last_wav_bytes is None:
        raise HTTPException(400, "Chưa có giọng giả nào được tạo")

    fmt = format.lower()
    if fmt not in DOWNLOAD_FORMATS:
        raise HTTPException(400, f"Định dạng không hỗ trợ: {format}")

    if fmt == "wav":
        data = _last_wav_bytes
    else:
        segment = AudioSegment.from_file(io.BytesIO(_last_wav_bytes), format="wav")
        # export to a real file, not an in-memory pipe: the mp4/m4a muxer needs
        # to seek back to write its header, which fails on a non-seekable pipe
        export_format = "ipod" if fmt in ("mp4", "m4a") else fmt  # ffmpeg's muxer name for m4a/mp4 audio
        with tempfile.NamedTemporaryFile(suffix=f".{fmt}") as tmp:
            segment.export(tmp.name, format=export_format)
            tmp.seek(0)
            data = tmp.read()

    return Response(
        content=data,
        media_type=DOWNLOAD_FORMATS[fmt],
        headers={"Content-Disposition": f'attachment; filename="fake_voice.{fmt}"'}
    )


@app.post("/api/attack/login")
async def attack_login(email: str = Form(...), password: str = Form(...)):
    r = requests.post(f"{AUTH_BACKEND_URL}/api/auth/login", json={"email": email, "password": password})
    if not r.ok:
        raise HTTPException(r.status_code, r.json().get("detail", "Đăng nhập thất bại"))
    _attack_state["session_token"] = r.json()["session_token"]
    _attack_state["email"] = email
    return {"status": "logged_in", "email": email}


@app.get("/api/attack/status")
async def attack_status():
    return {"logged_in": _attack_state["session_token"] is not None, "email": _attack_state["email"]}


@app.post("/api/attack/send")
async def attack_send():
    token = _attack_state["session_token"]
    if not token:
        raise HTTPException(400, "Chưa đăng nhập vào hệ thống xác thực")

    headers = {"Authorization": f"Bearer {token}"}
    prompt_res = requests.post(f"{AUTH_BACKEND_URL}/api/voice-auth/verify/prompt", headers=headers)
    if not prompt_res.ok:
        raise HTTPException(prompt_res.status_code, prompt_res.json().get("detail", "Không lấy được câu thử thách"))
    phrase = prompt_res.json()["phrase"]

    wav_bytes = await asyncio.to_thread(synthesize_wav, phrase)

    verify_res = requests.post(
        f"{AUTH_BACKEND_URL}/api/voice-auth/verify",
        headers=headers,
        files={"audio": ("fake.wav", wav_bytes, "audio/wav")},
    )
    if not verify_res.ok:
        raise HTTPException(verify_res.status_code, verify_res.json().get("detail", "Gửi thất bại"))

    return {"phrase": phrase, "result": verify_res.json()}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
