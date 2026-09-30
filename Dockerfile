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

# Copy the complete package inputs before the locked install. ``uv sync`` uses
# the committed lockfile rather than resolving a potentially newer dependency
# set at image-build time.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY backend ./backend
COPY data ./data

# multimodal = local ASR (faster-whisper) + local OCR (RapidOCR models ship in
# its wheel). The ASR model itself is intentionally supplied as a mounted,
# pre-fetched model directory, never downloaded during a request. Fail the
# build when the exact locked runtime cannot be installed.
RUN pip install --no-cache-dir uv==0.12.21 \
    && uv sync --frozen --no-dev --extra multimodal \
    && rm -rf /root/.cache

# Expose FastAPI & WebSocket port for live prototype HUD
EXPOSE 8000

# The organizer streams one JSON event per stdin line and receives one JSON
# action per stdout line via the official runner contract.
ENTRYPOINT ["/app/.venv/bin/continuum", "kit"]
