#!/usr/bin/env bash
# Public HTTPS URL for the radio (the phone mic needs HTTPS). Usage: radio/tunnel.sh [localhost.run|cloudflare]
set -euo pipefail
PORT="${PORT:-8090}"
PROVIDER="${1:-localhost.run}"

if ! curl -s -m 2 "http://localhost:${PORT}/api/radio/health" >/dev/null; then
  echo "warning: radio server not answering on :${PORT} yet (start it first: see radio/README.md)" >&2
fi

case "$PROVIDER" in
  localhost.run|lhr)
    CMD=(ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -R "80:localhost:${PORT}" nokey@localhost.run)
    PATTERN='https://[a-z0-9.-]+\.(lhr\.life|localhost\.run)'
    ;;
  cloudflare|cloudflared)
    command -v cloudflared >/dev/null 2>&1 || { echo "cloudflared not found: brew install cloudflared" >&2; exit 1; }
    CMD=(cloudflared tunnel --no-autoupdate --url "http://localhost:${PORT}")
    PATTERN='https://[a-z0-9-]+\.trycloudflare\.com'
    ;;
  *)
    echo "usage: $0 [localhost.run|cloudflare]" >&2; exit 2
    ;;
esac

LOG="$(mktemp -t radio-tunnel.XXXXXX)"
"${CMD[@]}" >"$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; rm -f "$LOG"' EXIT INT TERM
URL=""
for _ in $(seq 1 60); do
  URL="$(grep -Eo "$PATTERN" "$LOG" | grep -v 'admin.localhost.run' | head -1 || true)"
  [ -n "$URL" ] && break
  kill -0 $PID 2>/dev/null || break
  sleep 0.5
done
if [ -z "$URL" ]; then
  echo "could not read the tunnel URL; ${PROVIDER} output:" >&2; cat "$LOG" >&2; exit 1
fi
echo
echo "  Open on the phone:  $URL"
echo
if command -v qrencode >/dev/null 2>&1; then qrencode -t ansiutf8 "$URL"; fi
echo "(anyone with this URL can reach the radio; set RADIO_PIN. Ctrl-C to stop the tunnel)" >&2
wait $PID
