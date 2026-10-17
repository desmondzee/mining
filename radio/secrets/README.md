# radio secrets

One file per key, filename = lowercase variable name, contents = the key only. Everything here except
this README and .gitignore is git-ignored.

Add keys without them landing in shell history or on screen:

```sh
read -rs ELEVEN && printf '%s' "$ELEVEN" > radio/secrets/elevenlabs_api_key && unset ELEVEN
read -rs OAI && printf '%s' "$OAI" > radio/secrets/openai_api_key && unset OAI
chmod 600 radio/secrets/*_key
```

Precedence: environment variable > `radio/secrets/<name>` > `envgen/secrets/<name>`.

| file | used for | without it |
|---|---|---|
| `elevenlabs_api_key` | speech-to-text (Scribe) + truck voices (Flash TTS) | type commands; replies use the phone's built-in speech |
| `openai_api_key` | LLM command parser (tool calling) | regex/keyword parser (covers the demo phrases) |
