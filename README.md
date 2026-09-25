# 🎙️ VoiceProject

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-black?logo=fastapi)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
[![tests](https://github.com/whatdadogdoing/VoiceProject/actions/workflows/tests.yml/badge.svg)](https://github.com/whatdadogdoing/VoiceProject/actions/workflows/tests.yml)

A voice-based authentication system combining **speaker verification**, **deepfake/anti-spoofing detection**, and **email OTP** as a second factor — plus a companion tool for red-teaming its own anti-spoofing defenses with a real voice-clone attack.

A Word version of the technical documentation (features, data model, setup, measured results) is in [`VoiceProject_Documentation.docx`](VoiceProject_Documentation.docx).

---

## ✨ Features

| | |
|---|---|
| 🗣️ **Speaker verification** | [Resemblyzer](https://github.com/resemble-ai/Resemblyzer) embeddings, cosine similarity vs. a stored voiceprint |
| 🕵️ **Anti-spoofing** | [AASIST-L](https://github.com/clovaai/aasist), a graph-attention deepfake detector pretrained on ASVspoof2019, running on ONNX Runtime (~2.6x faster than raw PyTorch on CPU) |
| 🎙️ **Server-side phrase verification** | Offline [Whisper](https://github.com/SYSTRAN/faster-whisper) speech-to-text checks what was actually said in the audio — PhoWhisper-base (Whisper tuned for Vietnamese) first (~3 s), Whisper `small` as a second opinion only when it rejects — and the client's own transcript is never trusted for this |
| 📧 **Email OTP 2FA** | 6-digit code, auto-advancing input boxes, auto-submits — no confirm button |
| 🔁 **Random challenge phrases** | a phrase is not reissued to a user until the whole 200-phrase pool has been used → resists replay attacks |
| 🔢 **Random code (optional)** | a fresh 4-digit number to read aloud after the phrase, checked in the same recording — closes the replay gap a finite phrase pool leaves (`VERIFY_CODE_ENABLED`, off by default) |
| 🔒 **Persistent fraud lockout** | 3 spoof detections in 30 min locks voice auth *indefinitely* — no waiting it out |
| 🆘 **Password + email recovery** | unlocks the account, or re-enrolls a voice that stopped cooperating |
| 🔄 **Blue-green re-enrollment** | new voiceprint fully validated before the old one is ever removed |
| 🛡️ **Hardened auth plumbing** | non-spoofable client IP, account-level login lockout, logout that ends the session and revokes the post-MFA token, persisted per-browser device id |
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
    BE-->>FE: random challenge phrase (+ a 4-digit code, if enabled)
    U->>FE: reads the phrase aloud (then the code, digit by digit)
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

**Two tokens, two jobs.** The `session_token` (an opaque string in Redis, 30 minutes from login, not extended by use) is what `/verify`, the OTP endpoints, consent and recovery accept. The `access_token` (a JWT, 1 hour) is the proof of a finished voice + OTP login, and its one use is re-enrollment once a voiceprint exists (`/enroll/*` via `get_enroll_user_id`); it does not open `/verify`. A session can simply be deleted, a JWT can't, so logout does two things: it deletes the session and it stores a cutoff time for the user (`access_revoked_at:{user_id}`, kept for the JWT's one-hour life), after which any JWT issued at or before it is refused. That also kills a copy of the JWT taken from the browser. The client sends both tokens on logout (the JWT in `X-Access-Token`) because the 30-minute session can be gone while the 1-hour JWT is still alive, and the server finds the user from whichever one still resolves. Tokens issued before the `iat` claim existed count as issued at 0, so the first logout after an upgrade revokes them too. What this does not do: the JWT is still kept in the browser's `localStorage`, so a script injected into the page could read it while the user is logged in; revoking on logout limits the damage, not the theft.

**Optional random code.** With `VERIFY_CODE_ENABLED=true` in `.env` (default off; `.env.example` has the line), every challenge also carries a random 4-digit code, shown under the phrase. The user reads the phrase and then the digits one by one ("không chín sáu bảy"); the server checks both in the same transcript — the phrase in the words before the code, the code as exactly the last four digits, however the recognizer wrote them (`4729`, `4 7 2 9`, `bốn bảy hai chín`). The code comes from `secrets`, lives in Redis for 120 s (`verify_code:{user_id}`) and is used up by the first `/verify`, right or wrong. A wrong code is reported as `phrase_mismatch`, the same answer as a wrong phrase, so a replaying attacker is not told which half failed. Digits light up as they're recognized, the same live hint the phrase itself gets (`frontend/voice-recorder.js`'s Web Speech API transcript, client-side only — the server scores the actual uploaded audio, never this hint). Because the phrase is only the first half, the recorder no longer stops by itself once the last phrase word is heard; instead it keeps listening through the digits and, once all four are recognized, stops and submits on its own — no button press needed (the "Xong" button remains as a manual fallback, e.g. if the browser doesn't support live recognition). The phrase's own words are excluded first, so a phrase ending in a digit word ("...được không") is never mistaken for the start of the code. After editing `.env`, run `docker compose up -d backend` (`restart` does not re-read it). It needs the PhoWhisper fast recognizer (see Quick Start). Checked with the real recognizers on the 12 recordings read digit by digit: the right code was accepted 12 of 12 times (median 3.6 s), and the same recordings checked against another code were accepted 0 of 12 times. The highlighting/auto-submit logic was checked by running the real `frontend/app.js` against a scripted stub DOM, including the phrase-ending-in-a-digit-word case above. It raises the bar for replay but does not close every path: digits spliced from earlier recordings, or synthesized in the victim's voice, still have to get past speaker match and AASIST-L.

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
| `backend/routers/auth.py` | register / login (password only) / logout (ends the session, revokes the JWT) |
| `backend/routers/voice_auth.py` | consent · verify · OTP · **recovery** (unlock + re-enroll fallback) |
| `backend/routers/enroll.py` | enrollment & re-enrollment sample submission |
| `backend/services/risk_engine.py` | score thresholds, rate limits, the persistent fraud lock |
| `backend/services/anti_spoofing.py` | AASIST-L inference (ONNX Runtime) |
| `backend/services/speaker_verification.py` | Resemblyzer embeddings, and `ENCODER_ID` naming the embedding space |
| `backend/schema.sql`, `backend/migrations/`, `backend/models/migrate.py` | the current full schema, the incremental changes for older databases, and the code that applies them when the backend starts (see the Database section below) |
| `backend/services/stt.py` | speech-to-text: PhoWhisper-base (fast pass, installed once by `backend/scripts/fetch_phowhisper.py`) and Whisper `small` (second opinion) |
| `backend/services/phrase_check.py` | server-side phrase verification: the fast recognizer first, the slow one only when it rejects; with the random code on, phrase and code must be in the same transcript |
| `backend/services/verify_code.py` | the optional random 4-digit code read after the phrase: the `VERIFY_CODE_ENABLED` switch, the generator, and splitting a transcript into phrase and code |
| `frontend/` | nginx + vanilla HTML/CSS/JS, reverse-proxies `/api/*` |
| `fake-voice-lab/` | standalone voice-clone attack tool (own Dockerfile, own port) |
| `security-tests/` | earlier CLI prototype, superseded by `fake-voice-lab` |

---

## 🚀 Quick Start

```bash
cp .env.example .env   # fill in real values: Gmail app password, JWT secret, etc.
docker compose up -d
```

Optional in `.env`: `VERIFY_CODE_ENABLED=true` adds a random 4-digit code to read after each challenge phrase (default `false`; see the Authentication Flow section, and the PhoWhisper step below, which it relies on).

`JWT_SECRET` must be a real random value: the backend refuses to start if it is missing, still the `.env.example` placeholder, or shorter than 32 bytes, the minimum RFC 7518 §3.2 sets for an HS256 key (`python -c "import secrets; print(secrets.token_urlsafe(48))"` makes a good one).

There is no database step: the backend creates the tables itself when it starts (see the Database section below).

→ **`http://localhost:3000`**. The backend is never exposed directly — everything routes through nginx.

**First run takes a while — that's expected, not broken:**
- Building the backend image installs the full ML/audio dependency stack (torch, onnxruntime, librosa, scikit-learn, resemblyzer, faster-whisper, ...) from scratch — several minutes depending on your connection.
- The backend then needs several more minutes on top of that to actually become ready: it loads AASIST-L (ONNX), the Resemblyzer speaker encoder, and the two Whisper models before it can serve a single request. The Whisper models live in a Docker volume (`models`, mounted at `/opt/models` via `MODELS_DIR`) rather than under the bind-mounted `backend/`: on Windows/WSL2, reading files of that size through the bind mount hung the process in an uninterruptible disk wait several times. On a fresh clone the volume is empty and faster-whisper downloads Whisper `small` (~460 MB) and, as a stand-in for the fast recognizer, `base` (~140 MB) into it on first start. `docker compose ps` shows it as `health: starting` during this window.
- **One extra step for the fast recognizer.** The fast tier is PhoWhisper-base (VinAI's Whisper fine-tune for Vietnamese, BSD-3-Clause), converted once to the format faster-whisper runs. Do it once, after the first start:
  ```bash
  docker compose exec backend pip install transformers
  docker compose exec backend python scripts/fetch_phowhisper.py    # ~76 MB into the models volume, revision pinned
  docker compose restart backend
  ```
  Skip it and the backend still starts, on stock Whisper `base`, with a warning in the log. That works but is much slower in practice: `base` confirmed only 14 of 19 recordings made in the app where PhoWhisper confirmed 19, so most logins wait for the slow recognizer (about 14 s instead of about 4 s).
- The frontend deliberately won't start until the backend reports healthy, so `http://localhost:3000` may be unreachable for a few minutes after `up` rather than serving a broken page — see `docker-compose.yml`'s `healthcheck`/`depends_on: condition: service_healthy`.

**If a build looks stuck, don't force-kill Docker processes** (`taskkill` / `Stop-Process -Force` on `com.docker.build.exe`, `docker-compose.exe`, etc.) — killing the wrong internal process mid-build can corrupt Docker Desktop's WSL2 data disk and wipe every image, container, and volume on the machine. If something genuinely hangs, run `docker desktop restart` instead: it's the official, graceful way to reset Docker Desktop's engine without touching the underlying VM disk.

---

## 🗄️ Database

**Nothing to set up by hand.** `docker compose up -d` starts Postgres 16 with an empty database, and when the backend starts it builds the schema itself (`backend/models/migrate.py`, called from `main.py` before anything else). One line in `docker compose logs backend` says what it found:

| The database was… | The log says | What happened |
|---|---|---|
| empty (a fresh clone) | `database: empty, created the schema from schema.sql` | all tables created |
| made by an older version of this project | `database: applied migrations 001_…` | only the missing changes applied, data kept |
| already current | `database: schema is up to date` | nothing |

If a database holds *some* of the tables but not all, the backend refuses to start and names what is missing, rather than guess with somebody's data.

| Table | Holds |
|---|---|
| `users` | email and password hash |
| `voiceprints` | the enrolled voice: one active row per user, a 256-number embedding, and which encoder made it |
| `voice_auth_attempts` | one row per verification: voice score, spoof score, decision, reason, IP |
| `consent_records` | the biometric-consent record: version, time, IP, user agent |
| `schema_migrations` | which files in `backend/migrations/` have been applied |

Redis (sessions, OTP codes, rate limits, fraud locks, enrollment progress, challenge-phrase history) has no schema and needs no setup, but it does have to survive a restart; see the next paragraph.

**Redis persistence.** Sessions, OTPs, enrollment progress and the fraud lock live only in Redis, and Redis's default is a snapshot at most once an hour, so a crash could forget the lock of an account locked minutes earlier. `docker-compose.yml` therefore starts it with `--appendonly yes` (an append-only file flushed every second). Checked on a scratch volume by killing the container with SIGKILL: keys written before and after switching it on both came back. Fresh clones need nothing. **If you already have a `redis_data` volume from before this change, run this once first**, because a Redis started with the append-only file on and only an old snapshot on disk ignores the snapshot and comes up empty (also checked: the key was gone):

```bash
docker compose exec redis redis-cli config set appendonly yes   # writes the file from the live data
docker compose up -d redis                                        # recreates it with the compose flag
```

What is still lost: up to a second of writes on a hard crash, and everything on `docker compose down -v`. The 5-rejections-in-10-minutes throttle and the voiceprints are in Postgres, not Redis, and the spoof count behind a lock is rebuilt from Postgres, so a lost lock is re-armed by the next spoof detection rather than gone for good.

**Connection.** Development credentials only: user `user`, password `password`, database `voiceauth`, set in `docker-compose.yml` and in `DATABASE_URL` of `.env.example`. Postgres is not published to the host, so nothing outside the compose network can reach it. The backend reads nothing but `DATABASE_URL`, so pointing it at another Postgres means changing that one value (the role needs to create tables and the `pgcrypto` extension, which is what a database owner can do on Postgres 13+).

**Look inside.**

```bash
docker compose exec postgres psql -U user -d voiceauth
#   \dt                                   list the tables
#   select email, created_at from users;
```

**Run the setup by hand** (it is safe to repeat): `docker compose exec backend python -m models.migrate`

**Start over — development only, this deletes every user and voiceprint:**

```bash
docker compose exec postgres psql -U user -d voiceauth -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker compose restart backend      # rebuilds the schema from schema.sql
docker compose exec redis redis-cli FLUSHALL   # optional: forgets sessions, OTPs, locks, enrollment progress
```

**Changing the schema.** Add `backend/migrations/00N_short_name.sql` for databases that already exist, written so that running it twice is harmless (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`; a test enforces this), **and** make the same change in `schema.sql`, which always describes the full current schema and is what an empty database gets. A test builds a database the old way and the new way and fails if the two differ. It needs a throwaway database, and refuses any name that does not end in `_test` because it empties the `public` schema of whatever it is given:

```bash
docker compose exec postgres psql -U user -d voiceauth -c "CREATE DATABASE voiceauth_test"
docker compose exec backend pip install -q -r requirements-dev.txt
docker compose exec -e MIGRATION_TEST_DSN=postgresql://user:password@postgres/voiceauth_test backend python -m pytest tests/test_migrate.py
```

---

## 🧪 Fake Voice Lab

*The premise: if an attacker obtained a short recording of a user's voice, could a commodity zero-shot voice-cloning model clone it convincingly enough to beat this app's own defenses?*

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

The attack test exercises the full defense stack, not just the spoof detector in isolation: a submitted clone still has to pass speaker verification (does the embedding match the enrolled voiceprint?), server-side phrase verification (does the speech-to-text transcript of the clone actually match the live challenge phrase?), and AASIST-L's spoof score — the same three checks a real forged attempt against `/verify` would have to clear.

Run with `docker compose up -d` from inside `fake-voice-lab/` — it joins the main app's Docker network to reach the real backend directly, the same way an outside attacker's script would talk to it over HTTP. **Only ever point this at an account you own.**

### Why VieNeu-TTS?

Most open Vietnamese voice-cloning models are built on large architectures that assume a GPU is available, which makes them a poor fit for a fully local, CPU-only deployment (2 cores, 12GB RAM, no GPU here) — the kind of environment this project targets so the whole stack, including the attack lab, can run on a single ordinary machine. Three candidates were evaluated before finding one that actually holds up under that constraint:

| # | Model | What happened | Verdict |
|---|---|---|---|
| 1 | **viXTTS** (Coqui XTTS-v2 fine-tuned for Vietnamese) | Produced convincing clones, but XTTS-v2's pipeline (a GPT-style acoustic model plus a vocoder) is heavy without GPU offload — a single inference call pushed memory usage high enough to trigger **31GB** of swap, thrashing disk I/O until the process became unusable | ❌ Needs a GPU to be practical |
| 2 | **v-tts / VALTEC-TTS** | Advertised as a lightweight alternative (~74.8M parameters, small enough for CPU inference) | ❌ The model weights returned an HTTP 401 — the hosting repository isn't actually publicly downloadable despite being advertised as open |
| 3 | **VieNeu-TTS** | Ships a torch-free ONNX CPU inference path with a small (~285MB) footprint, so it doesn't need a full PyTorch/CUDA-oriented stack just to run | ✅ **~14s/sentence, reliable, and light enough to run alongside the rest of the app (Resemblyzer, AASIST-L, Postgres, Redis) on the same machine** |

The deciding factor across all three wasn't voice quality — it was whether the model could run reliably on CPU-only hardware without starving everything else on the box. VieNeu-TTS was the first one that did.

---

## 📝 Notes

- AASIST-L was trained on studio-quality ASVspoof2019 audio. Real browser-recorded audio shifted its scores enough to cause false "spoofing detected" rejections on genuine speech; the recorder explicitly disables the browser's echo-cancellation/noise-suppression/AGC to stay closer to what the model expects. Measuring it later showed the dominant factor is plain **loudness**, not codec compression — see "Measured on real recordings" below — so the server also normalizes speech level before scoring.
- AASIST-L runs from a PyTorch→ONNX export (`backend/scripts/export_aasist_onnx.py`) instead of raw PyTorch — verified numerically identical (< 1e-8 max diff on random inputs) and ~2.6x faster per call (983ms → 378ms on 2 CPU threads), which matters on the resource-constrained hardware below.
- The challenge phrase is checked against server-side speech-to-text output (PhoWhisper-base, then Whisper `small` if it rejects), not a client-supplied transcript — a client (browser or script) has no way to skip or fake this check.
- Debugging aid, off by default: if the directory `backend/eval_data/rejected/` exists, the audio of an enrollment sample rejected as a suspected spoof is saved there (score and time in the file name), so a false positive on a genuine voice can be inspected instead of guessed at (`services/debug_capture.py`). `backend/eval_data/` is git-ignored and Docker-ignored because it is where real voice recordings go.
- Each stored voiceprint records which encoder produced it (`voiceprints.model_id`, `embedding_dim`). Vectors from a different encoder, a different set of weights or a changed preprocessing step live in another space and cannot be compared, so `/verify` answers `voiceprint_outdated` (no model runs, no attempt is logged) and points the user at re-enrollment instead of producing a meaningless score or crashing on a size mismatch. Bump `ENCODER_ID` in `services/speaker_verification.py` whenever the encoder changes. A database created before these columns existed is upgraded automatically when the backend starts (`001_voiceprint_encoder_id.sql`); existing voiceprints are labelled with what they are (Resemblyzer, 256 dimensions).
- OTP abuse is limited in two places: `services/otp.py` (a code lives 300 s and is invalidated after 5 wrong guesses) and `routers/voice_auth.py` (`@limiter.limit` on the endpoints: sending a code 3 per minute, checking one 5 per minute, for both the normal and the recovery flow).
- The consent text and its version are tied together by a test. `CURRENT_CONSENT_VERSION` (`services/consent.py`) is what a user's consent record stores, while the words they read live in `frontend/index.html`, so nothing forced the two to change together: the text could be edited and everyone who had agreed to the old wording stayed "consented". `tests/test_consent_version.py` keeps a SHA-256 of the consent text for each version and fails if the text changes without a new version (which asks every user to agree again). That fixes the drift inside this repository; a consent row still stores only the version, not the text itself.
- Single-user personal/academic project — not hardened for multi-user production traffic.
- Developed on a resource-constrained Windows/WSL2/Docker Desktop setup. Stability there needed explicit `.wslconfig` memory/CPU/swap limits and `init: true` on the heavier container (to reap zombie processes from ML library threads) — both host-specific, so `.wslconfig` isn't committed here.
- `backend/tests/` has a pytest suite for the security-critical logic (risk engine decisions, phrase matching and the challenge pool, the random code, JWT auth-method checks, logout revoking the JWT, IP header handling, login lockout, the consent text pinned to its version, which model each recognizer tier loads) that doesn't need Postgres/Redis running — `pip install -r requirements-dev.txt && pytest` from `backend/`. The tests of the database setup that do need a Postgres are skipped unless `MIGRATION_TEST_DSN` is set (see the Database section). GitHub Actions (`.github/workflows/tests.yml`) runs the whole suite on every push and pull request, those Postgres tests included; it leaves out `resemblyzer` and `faster-whisper` because no test loads a speech model and resemblyzer would pull in several gigabytes of torch.
- Hardening pass on request/response and container handling, found and verified by actually running the stack rather than just reading the code:
  - `frontend/nginx.conf` raises `client_max_body_size` to 6m. nginx's own default (1m) was stricter than the backend's 5MB audio-size check, so a legitimate recording between 1-5MB got a raw HTML 413 from nginx before ever reaching the backend's JSON error handling.
  - The backend now has a real Docker healthcheck (`docker-compose.yml`, probing `/docs`), and the frontend's startup is gated on it passing. The ASGI app can't accept any connection at all until model loading finishes in its `lifespan` startup hook, so previously nginx started immediately and served instant 502s for the entire multi-minute cold start.
  - `backend/Dockerfile` explicitly preinstalls a CPU-only torch wheel before `pip install -r requirements.txt`. resemblyzer depends on torch internally as a real runtime dependency (not just the AASIST-L export tooling) — without the preinstall, pip resolves it on its own and pulls the much larger default CUDA build instead.
  - Both pip install steps use BuildKit cache mounts rather than `--no-cache-dir`, so a build interrupted partway through (a stalled download on a slow connection) doesn't have to re-fetch every already-downloaded package on the next attempt.
- Risk-engine and enrollment fixes from a code review, each confirmed against the code before changing it:
  - **Enrollment now runs anti-spoofing.** Each enrollment sample goes through the same AASIST-L check as `/verify` (shared `SPOOF_THRESHOLD`), so a synthetic or replayed recording can't become the stored voiceprint. A rejected sample returns `spoof_detected` without advancing enrollment and is deliberately *not* written to `voice_auth_attempts`, so it can't feed the fraud-lockout counter. It is counted separately instead: 10 such rejections per user per hour (`ENROLL_SPOOF_LIMIT`, a Redis counter kept apart from the verify fraud lock) block further enrollment samples with HTTP 429 until the hour has passed, because the `spoof_detected` answer would otherwise be a pass/fail oracle limited only by the 10-per-minute per-IP rate limit. The score is logged server-side only, never returned to the client. Because AASIST-L has shown false positives on genuine browser audio (see above), the threshold should be calibrated against real enrollment recordings.
  - **The "unusual hour" rule used the wrong clock.** `hour_of_day` came from the container's local time, which is UTC, so the "1am–5am" rule actually fired at 8am–noon in Vietnam and never at real night. It now uses Vietnam local time (fixed UTC+7, no DST, so no `tzdata` dependency).
  - **A freshly enrolled user's first verify always looked suspicious.** "Known device/IP" is derived from past non-rejected attempts, and a new user has none, so the first verify was always "new device *and* new IP" and needed the strict match bar (0.85 at the time, 0.78 now) instead of 0.7 — and since a rejection doesn't make a device known, a borderline genuine user could stay stuck at the strict bar. Completing enrollment now records an `enrolled` entry with that request's IP and `X-Device-Id`, which counts as a trusted device/IP.
  - **Audio-quality failures no longer trip the security throttle.** `audio_too_quiet` and `phrase_mismatch` were counted toward the 5-rejections-in-10-minutes limit, so a poor microphone could lock out a genuine user — and since that check only runs after those gates pass, five sloppy takes followed by a good one still came back `rate_limit_exceeded`. They stay in `voice_auth_attempts` as an audit trail but are excluded from the count. This is safe because they are still capped per IP, the challenge phrase changes on every attempt (see below for the actual limit of that), and neither reason involves a voiceprint score an attacker could tune against.
  - **Voiceprint adaptation only follows confident matches.** A successful verify used to nudge the stored template 15% toward the new sample regardless of how it scored, so a near-miss clone that barely cleared 0.7 could drag the template toward itself and make the next attempt easier. The sample is now only kept for adaptation when the match score is at least `ADAPT_MIN_SCORE` (0.85); a session scoring 0.7–0.85 still passes but leaves the template unchanged. Trade-off: a voice that has drifted so far that its scores sit in that band is no longer tracked and will eventually need re-enrollment.
  - **The thresholds can now be measured.** `SPOOF_THRESHOLD` (0.5), `MATCH_THRESHOLD` (0.7) and `STRICT_MATCH_THRESHOLD` (0.78, originally an unmeasured 0.85) in `services/risk_engine.py` started out as engineering defaults rather than measured values, and are being replaced by measured ones as data comes in (see the next bullet). `backend/scripts/evaluate_thresholds.py` scores enrollment, held-out genuine, impostor and voice-clone recordings with the deployed models and reports FRR, FAR and EER at each threshold with 95% confidence intervals (run inside the backend container: `python scripts/evaluate_thresholds.py --enroll … --genuine … --impostors … --clones …`). First small-sample results are in the next bullet; they should be re-measured on more recordings before being relied on.
  - **Measured on real recordings (first pass — small samples, indicative only).** Data: the author's own voice (3 enrollment takes, 14 held-out takes from a desktop recorder, 8 excerpts from a phone call, 3 takes from the app's own browser recorder), 16 impostor clips (7 of one other real person, taken from a call, plus 9 stock TTS-model sample voices) and 10 fake-voice-lab clones. The recordings are not published (they are voice data, one of them a third party's); only aggregate scores are.
    - **Speaker match** (desktop-recorder genuine, n=14, vs impostors n=16 and clones n=10), at the deployed 0.70: 7.1% of genuine takes rejected (1/14, 95% CI 1–31%), 6.2% of impostors accepted (1/16, CI 1–28%), 0% of clones accepted (0/10, CI 0–28%). The genuine-vs-impostor EER is 6.7% at a threshold of 0.706 — i.e. 0.70 sits at the crossover for this data. The clones scored a median 0.56, *below* the impostor median (0.62), so the VieNeu clones fail at speaker match before AASIST is even consulted (with the caveat that `reference.wav` was recorded on a different day and device than the enrollment takes). Against a voiceprint enrolled through the browser itself, the 23 in-app takes scored 0.808 on average (0.655–0.897) and three real verifications about 0.83 (a desktop-recorder voiceprint had matched browser takes only at 0.74–0.77). Longer speech scores higher — the shorter half of the clips averaged 0.776, the longer half 0.837 — so the voiceprint size matters: simulating voiceprints averaged from k in-app takes and tested on the rest, the share of genuine takes under 0.70 was 45.5% (k=1), 16.1% (2), 7.8% (3), 3.9% (5) and 2.1% (8), so enrollment now takes 5 samples instead of 3. Averaging the embeddings beat scoring against each sample separately (the best-of-three rule rejected 11.7% of genuine takes, the mean of similarities 33%). The strict bar used in unusual contexts was a 0.85 default that would have rejected 65–73% of these genuine takes; it is now 0.78, the smallest value at which none of the 26 impostor and clone clips was accepted (the highest scored 0.777) while about 23–33% of genuine takes are rejected, which is acceptable for a bar that applies only in an unusual context. The impostor clips came from other recording domains and only a few speakers, so the false-acceptance side is indicative: a 5-sample voiceprint also lets somewhat more of them through at a fixed threshold (10% against 7% at 0.70), while separating genuine from impostor better overall.
    - **AASIST-L score depends on loudness.** On the same real recording, +6 dB moved the spoof score from 0.024 to 0.521 and from 0.165 to 0.943; raw desktop-recorder audio (RMS about −15 dBFS) was flagged as spoofed 100% of the time, while the browser recorder's quieter output (about −28 dBFS) scored 0.02–0.29. Opus compression and added noise barely moved the score, so codec mismatch is *not* the main driver here. Without a fix, how close someone sits to the microphone decides whether they are flagged — and three flags in 30 minutes lock the account permanently. `services/anti_spoofing.py` therefore normalizes speech level (90th percentile of 50 ms frame RMS) to −40 dBFS before scoring. That target was tuned in two rounds. A first choice of −32 dBFS (from desktop-recorder audio) still flagged 2 of 13 genuine recordings made in the app's own browser recorder, and they were the quiet ones the normalization had *boosted* — the lower the level, the lower the spoof score. Sweeping 13 in-app genuine takes and 10 clones then showed a stable plateau from −39 to −42 dBFS with 0/13 genuine flagged and 10/10 clones flagged; −40 sits in the middle (worst genuine score 0.159, weakest clone 0.585; above −37 genuine speech starts being flagged, below −43 clones start slipping through). On a later, separate set of 10 in-app takes the deployed setting flagged 0/10 (highest score 0.067). Padding short clips with silence instead of repeating them made no difference. Only amplification is capped (at +30 dB); attenuation is not, because a take made right at the microphone needs about 35 dB of cut and an earlier ±30 dB clip left it at −35 dBFS. Boosting the 20 in-app takes by +26 dB to imitate a close mic, that version flagged 2 of 20 (up to 0.71) and at +30 dB flagged 5 of 20 (up to 0.98), while the uncapped cut flagged 0 of 20 at both. The target was picked on small samples from one speaker, with clones fed in directly rather than replayed through a speaker, so it is a starting point to re-check, not a validated constant; phone-call audio is still flagged often (a different domain the app doesn't record in).
    - **Phrase check.** The first design used Vosk (offline Vietnamese speech-to-text) with Whisper as a fallback. On recordings made in the app's own browser recorder Vosk passed almost nothing (0 of 9 at the 0.6 word-match bar; 5 of 17 across all labelled clips, with either model size), so it was replaced. Three suspected causes were tested one at a time: the 48→16 kHz conversion (a proper resampler lifted mean word recall from 0.35 to 0.42 but not the pass count), the decoder search width (8× wider and 8× slower gave identical results), and the browser's own noise suppression/AGC (turning it on made everything worse — Whisper could place only 4 of 10 clips against a pool phrase instead of 9, 3 of 10 were flagged as spoofed instead of 0, and the speaker match dropped — so the recorder keeps it off). The first two-tier design was Whisper `base` (beam 5, ~3 s) first and Whisper `small` (~10 s whatever the clip length, because Whisper always processes a 30 s window) only when `base` rejects, both at the same 0.6 threshold (`services/phrase_check.py`). Timed stage by stage on 8 in-app recordings, nearly all of a `/verify` is speech-to-text (decoding 0.3 s, `base` 3.2 s, `small` about 10 s when needed, speaker embedding plus anti-spoofing 0.4 s together), so the wait depends on how often the fast recognizer passes. `base` did not pass often enough: it confirmed 14 of 19 recordings made in the app, 4 of 8 in that timing run and only 2 of 7 real submissions, which had a median of 16 s (5–21 s), no better than the 14 s median (max 28 s) of the 22 submissions before the change. **The fast tier is now PhoWhisper-base** ([vinai/PhoWhisper-base](https://huggingface.co/vinai/PhoWhisper-base): Whisper fine-tuned on 844 hours of Vietnamese, BSD-3-Clause, converted once to CTranslate2 int8 by `scripts/fetch_phowhisper.py` at a pinned revision). At the same beam 5 it confirmed 19 of 19 in-app recordings (mean word recall 0.93 against 0.75 for `base`; `small` also 19 of 19) and 20 of 20 desktop clips, in 3.4 s against 3.2 s. Through the real pipeline on 8 in-app recordings it passed all 8 and a whole `/verify` took a median 4.3 s (max 5.2 s) against 8.6 s (max 14.6 s) before; `small` stays as the second opinion. Through the real browser after the swap, 10 real `/verify` submissions had a median of 6 s (mean 9 s, max 18 s): the 6 that were accepted (`mfa_required`) had a median of 5 s (4–14 s), while the 3 rejected as `phrase_mismatch` took 14–18 s — genuine cases where the fast recognizer didn't confirm the phrase, so `small` ran too, the same slow path the old design always took (a 4th rejection, `audio_too_quiet`, answers in under a second because it's checked before any speech-to-text runs). This replaces the single-sample 5 s figure measured right after the swap and confirms the fast tier is now handling ordinary correct readings fast; the slow path still exists and is a few times longer, whether the phrase turns out right or wrong. The swap was checked for safety rather than assumed, against `base`: (1) *wrong phrase* — of 7,761 (clip, other phrase) pairs 5 reached 0.6 with PhoWhisper and 2 with `base`, all because the pool held near-duplicate phrases: 12 of 39,800 ordered pairs reach 0.6 even with a perfect transcript ("…chỉ đường giúp tôi…" against "…bật đèn giúp tôi…"); eight phrases were replaced and `test_a_perfect_reading_of_one_phrase_never_passes_the_check_for_another` keeps it that way. (2) *Long speech* — 6 of the 9 stock TTS sample voices (a 30-word "Xin chào, tôi là một trợ lý AI…" paragraph) reached 0.6 against one short phrase made of common words (5 with `base`), because recall of the expected words ignores everything else that was said; `matches_phrase` therefore also rejects a transcript longer than twice the phrase (`PHRASE_MAX_LENGTH_RATIO`). Genuine readings heard 1.00× the phrase at the median and 2.11× at most (n=28), the paragraphs 24 words or more: the rule rejects 9 of 9 paragraphs and 1 of 28 genuine readings (the clip where the speaker read out extra digits). (3) *Silence and noise* — `base` transcribes nothing; PhoWhisper invents a few words (for example "sự thay đổi thay đổi…"), but none came within 0.27 of any phrase. (4) *Clones* — PhoWhisper transcribes more of the 10 clones (three reach 0.6, against one for `base`), so this check no longer stops them by accident: speaker match (0 of 10 accepted at 0.70) and AASIST-L (10 of 10 flagged) have to, as they did. Lowering the threshold was measured and rejected: at 0.6, 0 of 1,728 wrong-phrase or free-speech pairs passed; 0.5 already let one through, and it would have rescued only one more genuine take. Constraining the recognizer to the expected phrase (Vosk grammar mode) accepted every correct reading but also 69% of wrong-phrase and 57% of free-speech attempts, so it was not used. With Vosk and with Whisper the 10 clones also failed this check on their own (1/10 transcribed each); see point (4) above for what changed.
    - **Not measured yet:** anti-spoofing on a proper in-domain set (about 20 or more genuine takes and impostors recorded through the browser, and clones played through a speaker into the mic, which is the real replay path). A temporary "download recording" link and a `?dsp=on` recorder switch were used to collect the browser recordings and the noise-suppression A/B above, then removed once they had answered their questions.
  - **Replay resistance has a real, measured limit.** A stored-audio replay attack (a genuine recording of the victim, captured earlier) can't be caught by AASIST or Resemblyzer — it *is* unmodified real speech, not synthetic. The only thing standing in its way is the phrase challenge not repeating. `services/phrases.py` picks each challenge from a fixed pool of 200 phrases (grown from 102: speech-to-text mishears English loanwords such as "podcast", "guitar" or "email", so the pool avoids them, and `tests/test_phrase_pool.py` keeps it that way by checking that every word is a plain Vietnamese syllable, with no digits and a sensible length, and that no perfect reading of one phrase passes the check for another: 8 near-duplicate pairs were found and fixed). The first version excluded only the last `PHRASE_HISTORY_SIZE` (10) phrases, so a phrase came back as soon as it aged out of that window — a test showed the very first phrase reappearing on the 12th call — and an attacker holding genuine recordings of several of a victim's past challenge phrases had a non-trivial chance of a future challenge matching one of them. Now a phrase is not issued to the same user again until the whole pool has been used once (`phrase_used:{user_id}` in Redis; a new cycle carries over the last 10 phrases so none comes straight back), so a captured recording only becomes useful again after roughly two hundred logins (`test_no_phrase_is_issued_twice_until_the_whole_pool_has_been_used`, also exercised against a real Redis). This narrows the window a lot but does not close it: a phrase does eventually recur, and splicing words from several recordings is not addressed. A random number the user must read together with the phrase would close the sentence-level gap. It has now been measured on 20 recordings of a phrase followed by a code (12 read digit by digit, 8 as a number; one speaker, a desktop recorder rather than the app's): Whisper `base` read the exact code 10 times out of 20 and `small` 18 out of 20, but PhoWhisper-base, now the fast tier, read it **19 out of 20** (12 of 12 read digit by digit, 7 of 8 as a number; the one miss is the clip where the speaker read the digits and then the number) in 3.5 s, and all 20 phrases still passed the phrase check. So a random code can be checked in the fast tier without adding latency, and it is built (see Authentication Flow), off by default: it was verified with the real recognizers on those recordings, but they came from a desktop recorder (one speaker, n=20), so it should be tried through the app's own recorder before it is switched on for good. Voice alone still wouldn't complete login (OTP is always required).

---

## 📄 License

The source code and documentation in this repository are released under the [MIT License](LICENSE), © 2026 Nguyễn Quang Bình.

The license covers the code only:

- **No voice data is included.** `backend/eval_data/` (real recordings used for evaluation, one set of them a third party's) is git-ignored and never published.
- **Third-party components keep their own licenses.** `backend/services/aasist_model.py` is vendored from [clovaai/aasist](https://github.com/clovaai/aasist) (MIT, © NAVER Corp.), and the AASIST-L weights in `backend/services/weights/` come from that project's pretrained checkpoint, which was trained on the ASVspoof 2019 LA data set under that data set's own terms. Resemblyzer is Apache-2.0; faster-whisper and the Whisper models are MIT. PhoWhisper (VinAI Research, BSD-3-Clause) is downloaded and converted on your machine by `scripts/fetch_phowhisper.py`, not redistributed here; if you use it in published work, please cite Le, Nguyen and Nguyen, *PhoWhisper: Automatic Speech Recognition for Vietnamese* (ICLR 2024 Tiny Papers). Check the terms of any model you download for `fake-voice-lab/` before using it beyond coursework.
