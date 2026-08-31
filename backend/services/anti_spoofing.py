import io
import os
import wave

import numpy as np
import torch

from services.aasist_model import Model as AASISTModel

_WEIGHTS_PATH = os.path.join(os.path.dirname(__file__), "weights", "aasist_l.pth")
_NB_SAMP = 64600  # fixed input length the pretrained weights were trained on (~4.04s @ 16kHz)

_MODEL_CONFIG = {
    "first_conv": 128,
    "filts": [70, [1, 32], [32, 32], [32, 24], [24, 24]],
    "gat_dims": [24, 32],
    "pool_ratios": [0.4, 0.5, 0.7, 0.5],
    "temperatures": [2.0, 2.0, 100.0, 100.0],
}

_model: AASISTModel | None = None


def load_model() -> None:
    global _model
    if _model is None:
        model = AASISTModel(_MODEL_CONFIG)
        model.load_state_dict(torch.load(_WEIGHTS_PATH, map_location="cpu"))
        model.eval()
        _model = model


def _pad(x: np.ndarray, max_len: int = _NB_SAMP) -> np.ndarray:
    if x.shape[0] >= max_len:
        return x[:max_len]
    num_repeats = max_len // x.shape[0] + 1
    return np.tile(x, num_repeats)[:max_len]


def _wav_to_float32(wav_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    pcm16 = np.frombuffer(raw, dtype=np.int16)
    return pcm16.astype(np.float32) / 32768.0


def analyze(wav_bytes: bytes) -> float:
    load_model()

    x = _pad(_wav_to_float32(wav_bytes))
    x_tensor = torch.from_numpy(x).unsqueeze(0)

    with torch.no_grad():
        _, logits = _model(x_tensor)
        # trained with label 0=spoof, 1=bonafide (AASIST/ASVspoof2019 convention)
        probs = torch.softmax(logits, dim=1)

    return probs[0, 0].item()
