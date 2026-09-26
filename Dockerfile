# CONTINUUM Multi-Stage Container Dockerfile
# Samsung PRISM Generative AI Hackathon (Theme 05: Interruptible Real-Time Agents)
FROM python:3.12-slim

WORKDIR /app

# Install project & dependencies
COPY . .
RUN pip install --no-cache-dir -e '.[dev]' || pip install --no-cache-dir -r requirements.txt

# Expose FastAPI & WebSocket port
EXPOSE 8000

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src:/app

# Default command runs full test suite; can be overridden to start the live server
CMD ["python", "-m", "pytest", "-q"]
