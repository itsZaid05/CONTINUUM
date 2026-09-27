# CONTINUUM Multi-Stage Container Dockerfile
# Samsung PRISM Generative AI Hackathon (Theme 05: Interruptible Real-Time Agents)
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src:/app

# Install dependencies and project
COPY pyproject.toml README.md LICENSE ./
COPY requirements.txt ./
COPY src ./src
COPY backend ./backend
COPY data ./data

RUN pip install --no-cache-dir . || pip install --no-cache-dir -r requirements.txt

# Expose FastAPI & WebSocket port for live prototype HUD
EXPOSE 8000

# The organizer streams one JSON event per stdin line and receives one JSON
# action per stdout line via the official runner contract.
ENTRYPOINT ["continuum", "kit"]
