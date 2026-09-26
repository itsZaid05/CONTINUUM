FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# The organizer streams one JSON event per stdin line and receives one JSON
# action per stdout line. Override CMD for local-tool smoke runs if desired.
ENTRYPOINT ["continuum", "kit"]
