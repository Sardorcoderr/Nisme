# Nisme AI — production image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# ffmpeg + build deps (whisper / some wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Backend deps
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# App code
COPY backend /app/backend
COPY frontend /app/frontend

WORKDIR /app/backend

# Railway / Render PORT ni ishlatadi
EXPOSE 8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
