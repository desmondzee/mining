#!/usr/bin/env bash
# Start the radio server on :8090.  BRAIN_URL defaults to http://127.0.0.1:8080 (SSH tunnel to the brain).
# Falls back to an in-process mock sim if the brain is unreachable (RADIO_MOCK=1 forces it, RADIO_MOCK=0 forbids it).
cd "$(dirname "$0")/.."
exec uv run --with 'uvicorn[standard]' --with fastapi --with httpx --with python-multipart radio/server.py
