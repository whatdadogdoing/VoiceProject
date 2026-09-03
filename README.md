# VoiceProject

A voice-based authentication system combining speaker verification, deepfake/anti-spoofing detection, and email OTP as a second factor. Includes a companion tool for red-teaming the system's own anti-spoofing defenses with a real voice-clone attack.

## How it works

1. **Password login** — standard email/password, issues a short-lived session token.
2. **Voice enrollment** — the user reads 3 randomly chosen phrases aloud. Each sample is converted to a speaker embedding (via [Resemblyzer](https://github.com/resemble-ai/Resemblyzer)); the average of the three becomes the stored "voiceprint."
3. **Voice verification** — the user reads a new random challenge phrase (never repeated from their last 10 attempts, to resist replay). The system checks:
   - **Speaker match**: cosine similarity between the new sample and the stored voiceprint (≥ 0.7 to pass).
   - **Anti-spoofing**: [AASIST-L](https://github.com/clovaai/aasist), a graph-attention network pretrained on ASVspoof2019 LA, scores the audio for synthetic/deepfake artifacts (rejected if spoof probability ≥ 0.5).
   - **Risk context**: unfamiliar device/IP or unusual hours can raise the required similarity threshold.
4. **Email OTP** — a second factor required even after voice passes, before a full access token is issued.
5. **Re-enrollment** — replacing a voiceprint follows a blue-green pattern: the new sample is fully captured and validated *before* the old voiceprint is deactivated and removed, so a failed re-enrollment never leaves the account without a working voiceprint. Re-enrollment requires proof of a completed voice+OTP login — or, if voice verification stops cooperating for the legitimate owner, a fallback path using password + a separate email OTP.

## Architecture

- **`backend/`** — FastAPI service (Python). Routes: `auth` (register/login), `voice-auth` (consent, verify, OTP, recovery), `enroll`. Postgres for users/voiceprints/attempt logs, Redis for sessions/OTP/enrollment state.
- **`frontend/`** — static HTML/CSS/JS served by nginx, which also reverse-proxies `/api/*` to the backend.
- **`fake-voice-lab/`** — a separate, standalone tool for self-testing (see below).
- **`security-tests/`** — an earlier CLI prototype for the same kind of attack testing, superseded by `fake-voice-lab`.

## Running it

```
cp .env.example .env   # fill in real values (Gmail app password, JWT secret, etc.)
docker compose up -d
```

Frontend: `http://localhost:3000`. The backend isn't exposed directly — everything goes through nginx.

## Fake Voice Lab

`fake-voice-lab/` is a standalone tool (own Dockerfile, own port) for answering one question: *if someone got a short recording of my voice, could they clone it and beat my own anti-spoofing model?*

- Record a reference clip of your own voice.
- Type any sentence and hear it synthesized in that voice (via [VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS), a Vietnamese zero-shot voice cloner with a torch-free CPU/ONNX path — picked after a heavier Coqui XTTS-based clone caused severe swap thrashing on this hardware), just to gauge clone quality.
- Log in with your real account and fire the cloned voice at the actual `/verify` endpoint — it fetches the live random challenge phrase, clones exactly that phrase, and submits it, showing whether AASIST-L catches it.

Run it with `docker compose up -d` from inside `fake-voice-lab/` (it joins the main app's Docker network to reach the real backend). Only ever point this at an account you own.

## Notes

- AASIST-L was trained on studio-quality ASVspoof2019 audio; real browser-recorded audio (codec compression, mic noise-suppression DSP) can shift its scores in ways the model wasn't trained for — worth keeping in mind when reading its output as ground truth.
- This is a single-user personal/academic project, not hardened for multi-user production traffic.
