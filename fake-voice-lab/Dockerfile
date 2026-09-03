FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir vieneu
RUN pip install --no-cache-dir fastapi uvicorn[standard] python-multipart requests pydub

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
