import io
import json
import os
import urllib.request
import wave
import zipfile

from vosk import KaldiRecognizer, Model, SetLogLevel

SetLogLevel(-1)  # silence Vosk's default verbose C++ logging

_MODEL_NAME = "vosk-model-small-vn-0.4"
_MODEL_URL = f"https://alphacephei.com/vosk/models/{_MODEL_NAME}.zip"
_WEIGHTS_DIR = os.path.join(os.path.dirname(__file__), "weights")
_MODEL_DIR = os.path.join(_WEIGHTS_DIR, _MODEL_NAME)

_model: Model | None = None


def _ensure_model_downloaded() -> None:
    if os.path.isdir(_MODEL_DIR):
        return
    os.makedirs(_WEIGHTS_DIR, exist_ok=True)
    zip_path = _MODEL_DIR + ".zip"
    urllib.request.urlretrieve(_MODEL_URL, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(_WEIGHTS_DIR)
    os.remove(zip_path)


def load_model() -> None:
    global _model
    if _model is None:
        _ensure_model_downloaded()
        _model = Model(_MODEL_DIR)


def transcribe(wav_bytes: bytes) -> str:
    """Offline Vietnamese speech-to-text on 16kHz mono PCM16 WAV bytes.

    This exists so phrase-matching checks what was actually said in the
    submitted audio, instead of trusting a client-supplied transcript field
    -- a client (or a script bypassing the browser entirely) could otherwise
    submit any audio with an empty/fabricated transcript and skip the
    challenge-phrase check altogether.
    """
    load_model()
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        recognizer = KaldiRecognizer(_model, wf.getframerate())
        parts = []
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            if recognizer.AcceptWaveform(data):
                parts.append(json.loads(recognizer.Result()).get("text", ""))
        parts.append(json.loads(recognizer.FinalResult()).get("text", ""))
    return " ".join(p for p in parts if p).strip()
