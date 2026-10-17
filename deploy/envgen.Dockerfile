# syntax=docker/dockerfile:1
# One-off world generator. Usage (from deploy/):
#   docker compose --profile tools run --rm envgen python -m envgen.procedural --seed 42 --id default
#   docker compose --profile tools run --rm envgen python -m envgen.worldlabs --help
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data

WORKDIR /app

COPY envgen/requirements*.txt /tmp/req/
RUN if [ -f /tmp/req/requirements.txt ]; then pip install -r /tmp/req/requirements.txt; fi

# Package lives at /app/envgen so `python -m envgen.<module>` works from /app.
COPY envgen/ /app/envgen/

RUN mkdir -p /app/data
CMD ["python", "-m", "envgen.procedural", "--seed", "42", "--id", "default"]
