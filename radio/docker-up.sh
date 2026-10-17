#!/usr/bin/env bash
# Build + start radio and tunnel in Docker, then print the phone URL.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose -f radio/docker-compose.yml up -d --build
echo "waiting for the tunnel URL ..."
for _ in $(seq 1 60); do
  URL="$(docker compose -f radio/docker-compose.yml logs tunnel 2>/dev/null | grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true)"
  [ -n "$URL" ] && break; sleep 1
done
echo; echo "  Open on the phone:  ${URL:-<not ready - run: docker compose -f radio/docker-compose.yml logs tunnel>}"; echo
docker compose -f radio/docker-compose.yml exec -T radio python -c "import urllib.request,json;print(json.loads(urllib.request.urlopen('http://127.0.0.1:8090/api/radio/health').read()))" || true
