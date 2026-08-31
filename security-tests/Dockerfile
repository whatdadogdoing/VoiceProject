FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir "TTS @ git+https://github.com/thinhlpg/TTS.git@add-vietnamese-xtts"
RUN pip install --no-cache-dir huggingface_hub vinorm requests soundfile

COPY clone_attack.py .

ENTRYPOINT ["python", "clone_attack.py"]
