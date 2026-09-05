# 🎙️ VoiceProject

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-black?logo=fastapi)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)

A voice-based authentication system combining **speaker verification**, **deepfake/anti-spoofing detection**, and **email OTP** as a second factor — plus a companion tool for red-teaming its own anti-spoofing defenses with a real voice-clone attack.

---

## ✨ Features

| | |
|---|---|
| 🗣️ **Speaker verification** | [Resemblyzer](https://github.com/resemble-ai/Resemblyzer) embeddings, cosine similarity vs. a stored voiceprint |
| 🕵️ **Anti-spoofing** | [AASIST-L](https://github.com/clovaai/aasist), a graph-attention deepfake detector pretrained on ASVspoof2019 |
| 📧 **Email OTP 2FA** | 6-digit code, auto-advancing input boxes, auto-submits — no confirm button |
| 🔁 **Random challenge phrases** | never repeats a user's last 10 phrases → resists replay attacks |
| 🔒 **Persistent fraud lockout** | 3 spoof detections in 30 min locks voice auth *indefinitely* — no waiting it out |
| 🆘 **Password + email recovery** | unlocks the account, or re-enrolls a voice that stopped cooperating |
| 🔄 **Blue-green re-enrollment** | new voiceprint fully validated before the old one is ever removed |
| 🧪 **Fake Voice Lab** | clone your own voice and attack your own `/verify` endpoint to test it |

---

## 🔄 Authentication Flow

```mermaid
sequenceDiagram
    actor U as User
    participant FE as Frontend
    participant BE as Backend
    participant AI as AASIST-L / Resemblyzer

    U->>FE: email + password
    FE->>BE: POST /api/auth/login
    BE-->>FE: session_token (30 min)

    FE->>BE: POST /verify/prompt
    BE-->>FE: random challenge phrase
    U->>FE: reads phrase aloud
    FE->>BE: POST /verify (audio)
    BE->>AI: speaker match + spoof score
    AI-->>BE: scores

    alt spoof detected, 3rd time in 30 min
        BE-->>FE: 🔒 fraud_lockout (see below)
    else voice + anti-spoofing pass
        BE-->>FE: mfa_required
        FE->>BE: POST /otp/send
        U->>FE: 6-digit email OTP
        FE->>BE: POST /otp/verify
        BE-->>FE: ✅ access_token
    end
```

---

## 🔒 Fraud Lockout & Recovery

The lockout **does not expire on a timer** — that's the point. An attacker who trips it can't just wait and try again; only proving account ownership through a separate channel (email) lifts it.

```mermaid
flowchart TD
    A["🎤 Voice verify attempt"] --> B{"Spoof score ≥ 0.5?"}
    B -- No --> C{"Speaker match + risk OK?"}
    C -- Yes --> D["✅ mfa_required → OTP"]
    C -- No --> E["❌ Rejected: mismatch / suspicious context"]
    B -- Yes --> F{"3rd spoof detection in 30 min?"}
    F -- No --> G["⚠️ Rejected: spoofing_detected — tries left shown"]
    F -- Yes --> H["🔒 Account LOCKED — persists indefinitely"]
    H --> I["Recovery: password session + fresh email OTP"]
    I --> J["🔓 Lock cleared → back to normal verify"]
```

The same recovery endpoints (password + email OTP, no voice needed) also cover a second case — a legitimate voice that just won't verify anymore — in which case success leads into re-enrollment instead of unlocking.

---

## 🏗️ Architecture

```mermaid
flowchart LR
    Browser(["🌐 Browser"]) -->|":3000"| FE["frontend/<br/>nginx + static JS"]
    FE -->|"/api/* proxy"| BE["backend/<br/>FastAPI"]
    BE --> PG[("Postgres<br/>users · voiceprints · attempts")]
    BE --> RD[("Redis<br/>sessions · OTP · fraud locks")]
    FVL["fake-voice-lab/<br/>standalone tool · :5001"] -.->|"server-to-server<br/>(no CORS involved)"| BE
```

| Path | Responsibility |
|---|---|
| `backend/routers/auth.py` | register / login (password only) |
| `backend/routers/voice_auth.py` | consent · verify · OTP · **recovery** (unlock + re-enroll fallback) |
| `backend/routers/enroll.py` | enrollment & re-enrollment sample submission |
| `backend/services/risk_engine.py` | score thresholds, rate limits, the persistent fraud lock |
| `backend/services/anti_spoofing.py` | AASIST-L inference |
| `backend/services/speaker_verification.py` | Resemblyzer embeddings |
| `frontend/` | nginx + vanilla HTML/CSS/JS, reverse-proxies `/api/*` |
| `fake-voice-lab/` | standalone voice-clone attack tool (own Dockerfile, own port) |
| `security-tests/` | earlier CLI prototype, superseded by `fake-voice-lab` |

---

## 🚀 Quick Start

```bash
cp .env.example .env   # fill in real values: Gmail app password, JWT secret, etc.
docker compose up -d
```

→ **`http://localhost:3000`**. The backend is never exposed directly — everything routes through nginx.

---

## 🧪 Fake Voice Lab

*If someone got a short recording of my voice, could they clone it and beat my own anti-spoofing model?*

```mermaid
flowchart LR
    A["🎙️ Record your<br/>reference voice"] --> B["✍️ Type any sentence"]
    B --> C["🤖 VieNeu-TTS clones<br/>your voice"]
    C --> D{"What next?"}
    D -->|"just listening"| E["💾 Save as WAV/MP3/MP4/<br/>M4A/OGG/FLAC, any folder"]
    D -->|"real attack test"| F["🔑 Log into your<br/>real account"]
    F --> G["Fetch the LIVE random<br/>challenge phrase"]
    G --> H["Clone exactly that phrase"]
    H --> I["Submit to real /verify"]
    I --> J["📊 See if AASIST-L<br/>caught it"]
```

Run with `docker compose up -d` from inside `fake-voice-lab/` — it joins the main app's Docker network to reach the real backend directly. **Only ever point this at an account you own.**

### Why VieNeu-TTS?

Getting a working Vietnamese voice clone on this hardware (2-core CPU, 12GB RAM, no GPU) took three attempts:

| # | Model | What happened | Verdict |
|---|---|---|---|
| 1 | **viXTTS** (Coqui XTTS-v2 fine-tune) | Cloned convincingly, but one inference call caused **31GB** of swap-thrashing disk I/O | ❌ Unusable on this hardware |
| 2 | **v-tts / VALTEC-TTS** | Advertised as lightweight (74.8M params) | ❌ Model weights 401'd — the hosting repo isn't actually public |
| 3 | **VieNeu-TTS** | Torch-free ONNX CPU path, ~285MB footprint | ✅ **~14s/sentence, reliable, works alongside the main stack** |

---

## 📝 Notes

- AASIST-L was trained on studio-quality ASVspoof2019 audio. Real browser-recorded audio — codec compression, echo-cancellation/noise-suppression/AGC — shifted its scores enough to cause false "spoofing detected" rejections on genuine speech; the recorder explicitly disables those browser DSP features to stay closer to what the model expects.
- Single-user personal/academic project — not hardened for multi-user production traffic.
- Developed on a resource-constrained Windows/WSL2/Docker Desktop setup. Stability there needed explicit `.wslconfig` memory/CPU/swap limits and `init: true` on the heavier container (to reap zombie processes from ML library threads) — both host-specific, so `.wslconfig` isn't committed here.
