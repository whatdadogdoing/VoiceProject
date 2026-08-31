"""Voice-clone lab: record a reference clip of your own voice, synthesize any
sentence in that voice via viXTTS, and optionally fire the result straight at
the real voice-auth backend's /verify endpoint to see if it gets caught.

Talks to the real backend server-to-server (not from the browser), so the
real app's CORS policy is irrelevant here -- this process makes its own HTTP
calls, the same way an actual attacker script would.
"""
import asyncio
import io
import os

import requests
import soundfile as sf
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydub import AudioSegment

try:
    from vinorm import TTSnorm
except ImportError:
    TTSnorm = None

DATA_DIR = "/app/data"
REFERENCE_PATH = os.path.join(DATA_DIR, "reference.wav")
MODEL_DIR = os.path.join(DATA_DIR, "vixtts_model")
REPO_ID = "capleaf/viXTTS"
AUTH_BACKEND_URL = os.environ.get("AUTH_BACKEND_URL", "http://backend:8000")

os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI(title="Fake Voice Lab")

_model = None
_attack_state = {"session_token": None, "email": None}


def load_model():
    # imported lazily: pulls in torch/transformers/spacy, which otherwise
    # delays uvicorn's startup by a minute+ on this hardware for no benefit
    # until someone actually asks to synthesize something
    global _model
    if _model is not None:
        return _model

    from huggingface_hub import hf_hub_download, snapshot_download
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts

    os.makedirs(MODEL_DIR, exist_ok=True)
    required_files = ["model.pth", "config.json", "vocab.json", "speakers_xtts.pth"]
    have = os.listdir(MODEL_DIR)
    if not all(f in have for f in required_files):
        snapshot_download(repo_id=REPO_ID, repo_type="model", local_dir=MODEL_DIR)
        hf_hub_download(repo_id="coqui/XTTS-v2", filename="speakers_xtts.pth", local_dir=MODEL_DIR)

    config = XttsConfig()
    config.load_json(os.path.join(MODEL_DIR, "config.json"))
    model = Xtts.init_from_config(config)
    model.load_checkpoint(config, checkpoint_dir=MODEL_DIR, use_deepspeed=False)
    model.eval()
    _model = model
    return _model


def normalize_vietnamese_text(text: str) -> str:
    if TTSnorm is None:
        return text
    return (
        TTSnorm(text, unknown=False, lower=False, rule=True)
        .replace("..", ".")
        .replace("!.", "!")
        .replace("?.", "?")
        .replace(" .", ".")
        .replace(" ,", ",")
        .replace('"', "")
        .replace("'", "")
    )


def _calculate_keep_len(text: str) -> int:
    word_count = len(text.split())
    num_punct = text.count(".") + text.count("!") + text.count("?") + text.count(",")
    if word_count < 5:
        return 15000 * word_count + 2000 * num_punct
    elif word_count < 10:
        return 13000 * word_count + 2000 * num_punct
    return -1


def synthesize_wav(text: str) -> bytes:
    if not os.path.isfile(REFERENCE_PATH):
        raise HTTPException(400, "Chưa có mẫu giọng nói tham chiếu, hãy ghi âm trước")

    model = load_model()
    gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(
        audio_path=REFERENCE_PATH,
        gpt_cond_len=model.config.gpt_cond_len,
        max_ref_length=model.config.max_ref_len,
        sound_norm_refs=model.config.sound_norm_refs,
    )
    normalized_text = normalize_vietnamese_text(text)
    out = model.inference(
        text=normalized_text,
        language="vi",
        gpt_cond_latent=gpt_cond_latent,
        speaker_embedding=speaker_embedding,
        temperature=0.3,
        length_penalty=1.0,
        repetition_penalty=10.0,
        top_k=30,
        top_p=0.85,
        enable_text_splitting=True,
    )
    wav = out["wav"]
    keep_len = _calculate_keep_len(text)
    if keep_len != -1:
        wav = wav[:keep_len]

    buf = io.BytesIO()
    sf.write(buf, wav, 24000, format="WAV", subtype="PCM_16")
    return buf.getvalue()


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
    wav_bytes = await asyncio.to_thread(synthesize_wav, text)
    return Response(content=wav_bytes, media_type="audio/wav")


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
        data={"transcript": phrase},
    )
    if not verify_res.ok:
        raise HTTPException(verify_res.status_code, verify_res.json().get("detail", "Gửi thất bại"))

    return {"phrase": phrase, "result": verify_res.json()}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
