# VoiceProject

A voice-based authentication system combining speaker verification, deepfake/anti-spoofing detection, and email OTP as a second factor. Includes a companion tool for red-teaming the system's own anti-spoofing defenses with a real voice-clone attack.

## How it works

1. **Password login** — standard email/password, issues a short-lived (30 min) session token.
2. **Voice enrollment** — the user reads 3 randomly chosen phrases aloud. Each sample is converted to a speaker embedding (via [Resemblyzer](https://github.com/resemble-ai/Resemblyzer)); the average of the three becomes the stored "voiceprint."
3. **Voice verification** — the user reads a new random challenge phrase (never repeated from their last 10 attempts, to resist replay). The system checks:
   - **Speaker match**: cosine similarity between the new sample and the stored voiceprint (≥ 0.7 to pass).
   - **Anti-spoofing**: [AASIST-L](https://github.com/clovaai/aasist), a graph-attention network pretrained on ASVspoof2019 LA, scores the audio for synthetic/deepfake artifacts (rejected if spoof probability ≥ 0.5).
   - **Risk context**: an unfamiliar device/IP or unusual hours raises the required similarity threshold.
4. **Email OTP** — a second factor required even after voice passes, before a full access token is issued. Entered as 6 individual digit boxes that auto-advance and auto-submit once filled — no confirm button needed.
5. **Fraud lockout** — 3 spoofing detections within 30 minutes trips a lock on voice verification for the account that does **not** expire on its own; an attacker can't just wait it out. The only way out is the recovery path below.
6. **Recovery** — a fallback path that needs only the existing password session plus a fresh email OTP (no voice required). It serves two different situations with the same mechanism:
   - Voice verification keeps failing for the legitimate owner → leads into re-enrollment.
   - The account is locked from suspected fraud → clears the lock and drops back into a normal verify attempt.
7. **Re-enrollment** — replacing a voiceprint follows a blue-green pattern: the new sample is fully captured and validated *before* the old voiceprint is deactivated and removed, so a failed re-enrollment never leaves the account without a working voiceprint. Requires proof of a completed voice+OTP login, or the recovery path above.
8. **Re-verify / logout** — once logged in, a persistent header lets you log out from any screen, and a "Xác thực lại giọng nói" button re-runs voice verification on demand (e.g. to confirm a fix actually worked) without a full logout/login cycle.

## Architecture

- **`backend/`** — FastAPI service (Python).
  - `routers/auth.py` — register/login (password only).
  - `routers/voice_auth.py` — consent, verify, the post-verify OTP, and the password+email recovery OTP (both re-enrollment and fraud-lock unlock share these endpoints).
  - `routers/enroll.py` — enrollment and re-enrollment sample submission.
  - `services/risk_engine.py` — score thresholds, rate limiting, and the persistent fraud lock (a Redis flag cleared only by a successful recovery OTP).
  - `services/anti_spoofing.py` / `services/speaker_verification.py` — AASIST-L and Resemblyzer inference.
  - Postgres for users/voiceprints/attempt logs; Redis for sessions, OTP codes, enrollment state, and fraud locks.
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
- Type any sentence and hear it synthesized in that voice (via [VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS), a Vietnamese zero-shot voice cloner with a torch-free CPU/ONNX path), just to gauge clone quality.
- Save the result anywhere, in whichever format you pick (WAV/MP3/MP4/M4A/OGG/FLAC, converted server-side via ffmpeg) — on Chrome/Edge this opens the browser's native Save dialog so you choose the real destination folder, not just a default downloads dump.
- Log in with your real account and fire the cloned voice at the actual `/verify` endpoint — it fetches the live random challenge phrase, clones exactly that phrase, and submits it, showing whether AASIST-L catches it.

Run it with `docker compose up -d` from inside `fake-voice-lab/` (it joins the main app's Docker network to reach the real backend). Only ever point this at an account you own.

### Why VieNeu-TTS

Getting a working Vietnamese voice clone on this hardware (2-core CPU, 12GB RAM, no GPU) took three attempts:

1. **viXTTS** (a Vietnamese fine-tune of Coqui XTTS-v2) — cloned convincingly, but a single inference call caused 31GB of swap-thrashing disk I/O and never completed in any reasonable time.
2. **v-tts / VALTEC-TTS** — advertised as a lightweight (74.8M param) alternative, but its published zero-shot model weights returned a 401 (the HuggingFace Space hosting them isn't actually public) — a dead end regardless of hardware.
3. **VieNeu-TTS** — torch-free ONNX CPU path, ~285MB footprint, ~14s per sentence once the model is warm. Works reliably, including with the main app stack running alongside it.

## Notes

- AASIST-L was trained on studio-quality ASVspoof2019 audio. Real browser-recorded audio — codec compression, mic echo-cancellation/noise-suppression/AGC — shifts its scores in ways the model wasn't trained for and was observed causing false "spoofing detected" rejections on genuine speech; the recorder explicitly disables those browser DSP features to keep audio closer to what the model expects.
- This is a single-user personal/academic project, not hardened for multi-user production traffic.
- Developed on a resource-constrained Windows/WSL2/Docker Desktop setup. Getting Docker Desktop stable there needed explicit `.wslconfig` memory/CPU/swap limits and `init: true` on the heavier container (to reap zombie processes from ML library threads) — both host-specific, so `.wslconfig` isn't committed here.
