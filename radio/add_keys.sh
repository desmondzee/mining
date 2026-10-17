#!/bin/zsh
# Store API keys for the radio service without echoing them. Run in a real terminal.
cd "$(dirname "$0")" && mkdir -p secrets
for k in elevenlabs_api_key openai_api_key; do
  read -rs "v?$k (paste, then Enter; Enter alone to skip): "
  echo
  if [ -n "$v" ]; then printf '%s' "$v" > "secrets/$k"; chmod 600 "secrets/$k"; echo "  saved $k"; fi
done
