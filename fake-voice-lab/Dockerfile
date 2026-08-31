FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir "TTS @ git+https://github.com/thinhlpg/TTS.git@add-vietnamese-xtts"
# the TTS fork pins transformers>=4.33.0 with no upper bound, so pip grabs the
# latest release; transformers>=4.57.0 removed the top-level BeamSearchScorer
# export this fork's code imports, breaking it. Pin back to a known-good version.
RUN pip install --no-cache-dir "transformers==4.36.2"
RUN pip install --no-cache-dir fastapi uvicorn[standard] python-multipart huggingface_hub vinorm requests soundfile pydub

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
