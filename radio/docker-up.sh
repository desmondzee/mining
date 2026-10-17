#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
files=(-f radio/docker-compose.yml)
docker network inspect mining_default >/dev/null 2>&1 && files+=(-f radio/docker-compose.gb10.yml)
docker compose "${files[@]}" up -d --build
echo "waiting for the tunnel URL ..."
for _ in $(seq 1 60); do
  URL="$(docker compose "${files[@]}" logs tunnel 2>/dev/null | grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true)"
  [ -n "$URL" ] && break; sleep 1
done
echo; echo "  Open on the phone:  ${URL:-<not ready - run: docker compose ${files[*]} logs tunnel>}"; echo
docker compose "${files[@]}" exec -T radio python -c "import urllib.request,json;print(json.loads(urllib.request.urlopen('http://127.0.0.1:8090/api/radio/health').read()))" || true
