# Secrets

One file per key, filename = lowercase variable name, contents = the key only. Never committed.

```
printf '%s' 'YOUR_KEY' > deploy/secrets/wlt_api_key
printf '%s' 'YOUR_KEY' > deploy/secrets/openai_api_key
chmod 600 deploy/secrets/*_key
```

envgen reads them locally from here and in Docker from /run/secrets (mounted read-only).
Precedence: real environment variables > secret files > deploy/.env.
