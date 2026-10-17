# syntax=docker/dockerfile:1
# Server image: FastAPI app (server/app/main.py -> app.main:app). Multi-arch (amd64/arm64).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data

WORKDIR /app

# curl for the compose healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install deps first for layer caching. Glob so the build tolerates a missing file.
COPY server/requirements*.txt /tmp/req/
RUN if [ -f /tmp/req/requirements.txt ]; then pip install -r /tmp/req/requirements.txt; fi \
    && pip install "uvicorn[standard]" websockets fastapi python-multipart

COPY server/ /app/

RUN mkdir -p /app/data
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
