FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir '.[dev]'
ENV PYTHONPATH=/app/src
CMD ["python", "-m", "pytest", "-q"]
