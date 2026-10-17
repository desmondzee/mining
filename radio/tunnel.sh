#!/usr/bin/env bash
# Expose the radio server (port 8090) over HTTPS so a phone can use the microphone.
# Prints the https URL to open on the phone.
set -euo pipefail
PORT="${PORT:-8090}"

if ! curl -s -m 2 "http://localhost:${PORT}/api/radio/health" >/dev/null; then
  echo "warning: radio server not answering on :${PORT} yet (start it first: see radio/README.md)" >&2
fi

if command -v cloudflared >/dev/null 2>&1; then
  echo "starting cloudflared quick tunnel -> http://localhost:${PORT} ..." >&2
  LOG="$(mktemp -t radio-tunnel.XXXXXX)"
  cloudflared tunnel --no-autoupdate --url "http://localhost:${PORT}" >"$LOG" 2>&1 &
  PID=$!
  trap 'kill $PID 2>/dev/null; rm -f "$LOG"' EXIT INT TERM
  for _ in $(seq 1 60); do
    URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
    [ -n "$URL" ] && break
    sleep 0.5
  done
  if [ -z "${URL:-}" ]; then
    echo "could not read the tunnel URL; cloudflared output:" >&2; cat "$LOG" >&2; exit 1
  fi
  echo
  echo "  Open on the phone:  $URL"
  echo
  if command -v qrencode >/dev/null 2>&1; then qrencode -t ansiutf8 "$URL"; fi
  echo "(Ctrl-C to stop the tunnel)" >&2
  wait $PID
else
  echo "cloudflared not found. Install it:  brew install cloudflared" >&2
  echo "Fallback (no install, needs node):  npx localtunnel --port ${PORT}" >&2
  echo "  (localtunnel shows a reminder page on first visit; enter this machine's public IP from https://loca.lt/mytunnelpassword)" >&2
  exit 1
fi
