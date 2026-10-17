# Haul Net radio

A phone push-to-talk "radio" for the autonomous haul trucks. You hold the button and talk. ElevenLabs Scribe transcribes the audio. The command is parsed by OpenAI tool calling, or by a regex parser when there is no key. The radio then sends each truck along the **road graph** from `data/worlds/quarry/world.json` as a chain of waypoints through the brain (`POST /api/trucks/{id}/goal`). Trucks answer in their own ElevenLabs voices, played through a radio filter on the phone.

Nothing moves until someone speaks or types a command. **RELEASE** (`POST /api/radio/release`) clears every radio task and truck goal.

```
phone (HTTPS tunnel) ──► radio/server.py :8090 ──► brain :8080 (SSH tunnel to GB10) ──► Unity
        ▲  /ws/radio: log + truck states            │ polls /api/state at 5 Hz, feeds next waypoint
        └──────── TTS mp3 replies ◄─────────────────┘
```

## 60-second setup (demo day)

```sh
cd ~/Software/mining

# 1. ElevenLabs key (once). Any one of these works, first match wins:
#    env ELEVENLABS_API_KEY or ELEVEN_LABS_API_KEY
#    env ELEVENLABS_API_KEY_FILE=/run/secrets/elevenlabs_api_key   (Docker secrets)
#    radio/secrets/elevenlabs_api_key   (zsh radio/add_keys.sh writes it without echoing)
#    ELEVEN_LABS_API_KEY=... in the repo-root .env
zsh radio/add_keys.sh

# 2. brain tunnel (terminal 1). Skip it to demo on the built-in mock sim
ssh -N -L 8080:127.0.0.1:8080 watson

# 3. radio server (terminal 2). RADIO_PIN is the access code the phone asks for once
RADIO_PIN=4826 radio/run.sh

# 4. HTTPS for the phone mic (terminal 3). Pick one option from the table below
radio/tunnel.sh            # localhost.run over ssh: prints https://<random>.lhr.life

# 5. open the URL on the phone, enter the code, tap once (unlocks audio), hold PUSH and talk
```

Check `http://localhost:8090/api/radio/health`. It should show `"mode": "brain"`, `"sim_connected": true`, `stt` and `tts` true, and `routing` (`brain` when the brain plans road routes, `radio` when the radio chains waypoints itself). The header pills on the phone show the same: **LIVE / MOCK / NO BRAIN**, STT, TTS, and LLM or REGEX.

### HTTPS options for the phone mic
Browsers only allow the microphone on HTTPS (or `localhost`). Any of these works; none is required.

| Option | Command | Installs | Notes |
|---|---|---|---|
| localhost.run | `radio/tunnel.sh` (= `ssh -R 80:localhost:8090 nokey@localhost.run`) | none | public URL, changes on every start |
| Self-signed, same Wi-Fi | `RADIO_TLS=1 HOST=0.0.0.0 RADIO_PIN=4826 radio/run.sh`, open `https://<laptop LAN IP>:8090` | none (uses `openssl`) | browser shows a certificate warning once: tap "Advanced", then proceed. Cert in `radio/secrets/tls/`, regenerated when the LAN IP changes; `RADIO_LAN_IP` overrides the detected IP |
| Own certificate | `SSL_CERTFILE=... SSL_KEYFILE=... HOST=0.0.0.0 radio/run.sh` | none | e.g. a `mkcert` cert trusted on the phone: no warning |
| Cloudflare quick tunnel | `radio/tunnel.sh cloudflare` | `brew install cloudflared` | public `trycloudflare.com` URL |
| localtunnel | `pnpm dlx localtunnel --port 8090` | node | first visit asks for this machine's public IP (https://loca.lt/mytunnelpassword) |

Any public tunnel URL can be opened by anyone who has it, so set `RADIO_PIN` whenever you use one.

### Access control and limits
The radio only exposes its own page and API: no static directory, no file paths from requests, no shell calls with request data, outbound HTTP only to `BRAIN_URL`, ElevenLabs and OpenAI.

| Protection | Default | Env |
|---|---|---|
| Bind address | `127.0.0.1` (a container sets `HOST=0.0.0.0`) | `HOST` |
| Access code | off | `RADIO_PIN` |
| Session cookie | HttpOnly, SameSite=Strict, Secure on HTTPS (also behind a proxy sending `X-Forwarded-Proto: https`), 12 h | `RADIO_SESSION_HOURS` |
| Gated with a PIN | state, world, command, transcribe, release, audio, `/ws/radio` (closes with 4401) | |
| Public | `/` page, `/api/radio/health` (reduced: no brain URL, no errors), `/api/radio/login` | |
| Login attempts | 5 per IP, 20 total per minute | `RADIO_LOGIN_PER_MIN`, `RADIO_LOGIN_GLOBAL_PER_MIN` |
| Speech-to-text | 10 per session, 30 total per minute | `RADIO_STT_PER_MIN`, `RADIO_STT_GLOBAL_PER_MIN` |
| Commands | 20 per session, 60 total per minute | `RADIO_COMMAND_PER_MIN`, `RADIO_COMMAND_GLOBAL_PER_MIN` |
| TTS syntheses | 60 per minute total, cached phrases are free; over the cap the phone speaks with its built-in voice | `RADIO_TTS_GLOBAL_PER_MIN` |
| Audio upload | 600 KB, audio content types only, kept in memory; the phone stops recording at 30 s | `RADIO_MAX_AUDIO_KB` |
| Command text | 300 characters | |
| WebSocket messages | 512 bytes per message (only pings are read) | |

Without `RADIO_PIN`, the per-session limits apply per client IP. `0` disables a limit. RELEASE is never rate limited.

### Container
The radio is configured by env vars only, so it containerises without code changes: `BRAIN_URL`, `HOST=0.0.0.0`, `PORT`, `RADIO_PIN`, and the key via `ELEVENLABS_API_KEY` or `ELEVENLABS_API_KEY_FILE`. It needs `data/worlds/quarry/world.json` (or `RADIO_WORLD`) and `radio/web/index.html`. `radio/cache/` holds the TTS mp3 cache.

## Demo script (3 minutes)

Before going on stage: open the phone page, tap once, check that the pills show LIVE, STT and TTS, and press **RELEASE**. The trucks should be on the ready line.

| t | Say (hold PUSH) | What happens |
|---|---|---|
| 0:00 | *(intro)* "This phone is a radio on the mine's dispatch channel." | |
| 0:15 | **"Truck One, go to the Lower Face."** | Truck One acknowledges in its own voice and pulls off the ready line, following the haul road (dashed route on the phone map). |
| 0:40 | **"Truck Two, haul from the Middle Face to the crusher."** | Truck Two says "Copy, Truck Two hauling Middle Face to crusher." It loops load → crusher. When it leaves the face it calls in on its own: "Truck Two, loaded, heading to crusher." |
| 1:10 | **"Status report."** | All three trucks report in turn, in three voices: task and metres to go. |
| 1:35 | **"Everyone stop."** | Every truck halts immediately and each one confirms "stopped". This is the safety beat. |
| 1:55 | **"Truck Three, haul two loads from the Lower Face to the crusher."** | A counted haul: it reports each load and then returns to the ready line by itself. |
| 2:30 | **"All trucks, return to the ready line."** | All three route home along the roads. |
| 2:50 | *(close)* "Natural-language dispatch, road-aware routing, autonomous driving." | |

Backups if the room is loud or STT fails: the quick-command chips under the button send the same phrases, the text box takes any command, the big **ALL STOP** button stops every truck, and the space bar works as push-to-talk on a laptop.

Other phrases that work, with or without the LLM: "trucks one and two go to the waste dump", "truck three go to the fuel bay", "truck one stop", "truck two status", "truck zero go to the crusher" (= Truck One). Two commands in one breath also work: "truck one go to the upper face and truck two return to the ready line".

Callsigns: **Truck One / Two / Three = `truck-0 / truck-1 / truck-2`**.

## How it works
- **STT**: `POST /api/radio/transcribe` (multipart `audio`: webm from Chrome, mp4 from iOS Safari) goes to ElevenLabs `POST /v1/speech-to-text` with `model_id=scribe_v2`, falling back to `scribe_v1`. With no ElevenLabs key it uses OpenAI `whisper-1` if an OpenAI key is set.
- **Parser**: OpenAI chat completions with tools `go_to`, `haul`, `return_to_parking`, `stop` and `status`. The system prompt lists the trucks and the site aliases, including common mishearings ("truck to", "red line", "middle phase"). Without a key, or on any error, the regex parser takes over. It is tested in `radio/test_parse.py`.
- **Routing**: on every poll the radio checks whether the brain plans routes itself (`/api/state` has `roads` and the trucks carry `route` or `goal_site`; `RADIO_BRAIN_ROUTING=1/0` forces it). If so, each leg is one `POST /api/trucks/{id}/goal {"site": "<site id>"}` and the brain drives the road; arrival is the brain's `arrived` status or the end of the truck's `route`. If the brain does not list that site or refuses it with a 4xx, the radio sends the site's stop point as `{x, z}` instead. Otherwise (current main, mock) the radio routes itself: road polylines are merged into a graph (shared endpoints become junctions), and Dijkstra runs from the road node nearest the truck to the site's `load_point`, `dump_point` or the truck's parking slot. Grid jitter is removed with Douglas-Peucker (1.5 m tolerance). Waypoints are kept wherever the road turns more than 12°, with straight fillers at most 20 m apart. The radio posts the next waypoint when the truck is within 9 m, or 5.5 m at bends, or once it has passed the waypoint. The last waypoint uses the brain's own 4 m arrive (the brain clears the goal then, which the radio counts as reached). If a truck circles a waypoint (the straight-line policy has an ~8.7 m turning radius), it is counted as reached after 5 s.
- **Haul**: load_point → (5 s loading) → dump_point → (4 s tipping) → repeat, for N loads or until another command. After N loads the truck returns to the ready line.
- **Override**: if someone else changes a truck's goal (the team web UI, a script), the radio drops its task for that truck and says so. With brain routing, a different `goal_site`, or the goal cleared away from the destination, counts as an override.
- **World hot-reload**: `world.json` is re-read when it changes, since envgen may regenerate it.
- **Mock mode**: if the brain is not reachable at startup, an in-process bicycle-model sim (with the brain's policy) moves the trucks, so the phone UI can be tested anywhere. When the real brain appears later, the radio switches over automatically. `RADIO_MOCK=1` forces mock and `RADIO_MOCK=0` forbids it.
- **TTS**: ElevenLabs `eleven_flash_v2_5`. Distinct premade voices are chosen from `GET /v1/voices` at startup: Sarah for control, and Adam, Callum and George for the trucks. The mp3s are cached in `radio/cache/audio`. With no key, `audio_url` is null and the phone uses `speechSynthesis` through the same squelch.

## API
| | |
|---|---|
| `GET /` | phone UI (`radio/web/index.html`) |
| `GET /api/radio/health` | mode (brain/mock), brain/sim link, routing, which keys are active, last error (reduced without a session when `RADIO_PIN` is set) |
| `GET /api/radio/state` | trucks (status, task, remaining route) + last radio log entries |
| `GET /api/radio/world` | roads + sites for the minimap |
| `POST /api/radio/login` | `{pin}` → session cookie (only when `RADIO_PIN` is set) |
| `POST /api/radio/transcribe` | multipart `audio` → `{text}` |
| `POST /api/radio/command` | `{text}` → `{heard, parser, actions, replies:[{truck_id, callsign, text, audio_url}]}` |
| `POST /api/radio/release` | clear all radio tasks and truck goals |
| `GET /api/radio/audio/<id>.mp3` | cached TTS |
| `WS /ws/radio` | `hello` (health + log), `state` (5 Hz), `radio` events. The phone falls back to polling `/api/radio/state` if the websocket fails. |

```sh
curl -s -XPOST localhost:8090/api/radio/command -H 'content-type: application/json' \
  -d '{"text":"truck two haul from the middle face to the crusher"}'
```

## Env vars
| var | default | |
|---|---|---|
| `BRAIN_URL` | `http://127.0.0.1:8080` | team brain |
| `PORT` / `HOST` | `8090` / `127.0.0.1` | |
| `RADIO_PIN` | unset | access code; see Access control |
| `RADIO_TLS` / `SSL_CERTFILE` / `SSL_KEYFILE` / `RADIO_LAN_IP` | unset | HTTPS without a tunnel |
| `RADIO_BRAIN_ROUTING` | `auto` | `1` always send `{site}` goals, `0` always chain waypoints |
| `RADIO_WORLD` | `data/worlds/quarry/world.json` | road graph + sites |
| `RADIO_MOCK` | `auto` | `1` force mock, `0` never mock |
| `RADIO_MOCK_SPEED` | `1` | mock sim time multiplier |
| `RADIO_LOAD_DWELL` / `RADIO_DUMP_DWELL` | `5` / `4` s | |
| `OPENAI_MODEL` / `OPENAI_BASE_URL` | `gpt-4.1-mini` / OpenAI | any OpenAI-compatible gateway |
| `ELEVENLABS_TTS_MODEL` / `ELEVENLABS_STT_MODEL` | `eleven_flash_v2_5` / `scribe_v2` | |
| `ELEVENLABS_VOICE_IDS` | auto | comma list: control, truck-0, truck-1, truck-2 |

Keys: `ELEVENLABS_API_KEY` (alias `ELEVEN_LABS_API_KEY`) and `OPENAI_API_KEY`, read from env, then a `<NAME>_FILE` env path, then `radio/secrets/<lowercase>`, then `envgen/secrets/<lowercase>`, then the repo-root `.env`.

## Tests
```sh
uv run --with pytest --with fastapi --with 'uvicorn[standard]' --with httpx --with python-multipart pytest radio -q
RADIO_MOCK=1 RADIO_MOCK_SPEED=4 radio/run.sh                                 # UI without a brain
```
`radio/test_parse.py`: parser and road routing on the current `world.json`. `radio/test_radio.py`: brain-routing detection with mocked brain states, waypoint vs `{site}` goals, arrival and override, release, PIN gating, rate limits, upload limits, path traversal, WebSocket input. The tests never call ElevenLabs.

End-to-end without Unity, using the team's fake sim (flat, ±100 m, its own rocks; the quarry sites are partly outside it, the trucks still drive there):
```sh
(cd brain && PORT=8100 SIM_PORT=9100 uv run brain) &
(cd brain && uv run fakesim --port 9100) &
BRAIN_URL=http://127.0.0.1:8100 radio/run.sh
```
