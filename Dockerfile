FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONTINUUM_MODEL_DIR=/app/models

# OpenCV (pulled in by RapidOCR) needs these shared libraries on slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# multimodal = local ASR (faster-whisper) + local OCR (RapidOCR, models in the wheel)
RUN pip install --no-cache-dir ".[multimodal]"

# Fetch the ASR model at *build* time so the container never downloads at
# runtime; warm-up then loads it from /app/models within the setup budget.
RUN continuum fetch-models --asr base.en --dir /app/models

# The organizer streams one JSON event per stdin line and receives one JSON
# action per stdout line. Override CMD for local-tool smoke runs if desired.
ENTRYPOINT ["continuum", "kit"]
