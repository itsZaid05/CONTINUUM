# CONTINUUM Multi-Stage Container Dockerfile
# Samsung PRISM Generative AI Hackathon (Theme 05: Interruptible Real-Time Agents)
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src:/app \
    CONTINUUM_MODEL_DIR=/app/models

# OpenCV (pulled in by RapidOCR) needs these shared libraries on slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies and project
COPY pyproject.toml README.md LICENSE ./
COPY requirements.txt ./
COPY src ./src
COPY backend ./backend
COPY data ./data

# multimodal = local ASR (faster-whisper) + local OCR (RapidOCR, models in the wheel)
RUN pip install --no-cache-dir ".[multimodal]" || pip install --no-cache-dir -r requirements.txt

# Expose FastAPI & WebSocket port for live prototype HUD
EXPOSE 8000

# The organizer streams one JSON event per stdin line and receives one JSON
# action per stdout line via the official runner contract.
ENTRYPOINT ["continuum", "kit"]
