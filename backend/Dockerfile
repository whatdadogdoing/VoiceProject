FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*
# resemblyzer (the actual runtime speaker-verification engine, used by
# services/speaker_verification.py) depends on torch internally -- this is
# NOT dead weight for the export script, it's a real transitive runtime
# dependency. Installing the small CPU-only wheel explicitly, before
# requirements.txt, satisfies that dependency up front; without this, pip
# resolves it on its own while installing resemblyzer and pulls the default
# full CUDA build instead (~550MB vs this CPU wheel), which is both far
# larger and, on a slow connection, can turn the build into a multi-hour hang.
# Cache mounts (not --no-cache-dir here) so a build interrupted partway
# through this layer -- e.g. a stalled download on a slow/flaky connection --
# doesn't have to re-download every already-fetched wheel on the next
# attempt, only what's still missing.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
