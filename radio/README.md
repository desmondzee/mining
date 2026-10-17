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
cd ~/Documents/hacks/Physical_mining

# 1. keys (once). Typed blind, never echoed. Or run radio/add_keys.sh
read -rs K && printf '%s' "$K" > radio/secrets/elevenlabs_api_key && unset K   # STT + voices
read -rs K && printf '%s' "$K" > radio/secrets/openai_api_key && unset K       # LLM parser (optional)

# 2. brain tunnel (terminal 1). Skip it to demo on the built-in mock sim
ssh -N -L 8080:127.0.0.1:8080 watson

# 3. radio server (terminal 2)
radio/run.sh            # = uv run --with 'uvicorn[standard]' --with fastapi --with httpx --with python-multipart radio/server.py

# 4. HTTPS for the phone (terminal 3). The phone mic needs HTTPS
brew install cloudflared   # once
radio/tunnel.sh            # prints https://<random>.trycloudflare.com

# 5. open that URL on the phone, tap once (unlocks audio), hold PUSH and talk
```

Check `http://localhost:8090/api/radio/health`. It should show `"mode": "brain"`, `"sim_connected": true`, and `stt`, `tts` and `llm` set to true. The header pills on the phone show the same: **LIVE / MOCK / NO BRAIN**, STT, TTS, and LLM or REGEX.

### HTTPS alternatives
- `npx localtunnel --port 8090`. The first visit shows a reminder page that asks for this machine's public IP, which you get from https://loca.lt/mytunnelpassword.
- `ssh -R 80:localhost:8090 nokey@localhost.run` prints an https URL, with no install.
- Same Wi-Fi with no tunnel: `HOST=0.0.0.0 radio/run.sh` works for the UI and text commands. The phone mic will not work over plain http (except on localhost).

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

### Excavators (voice-controlled)
**Excavator One / Two / Three = `exc-0 / exc-1 / exc-2`** from `world.json` `equipment`, one per face (Lower / Middle / Upper). They start **HOLDING**; nothing digs until told. Their state lives only in the radio (the brain has no excavator endpoint). They answer in their own ElevenLabs voice (Bill, not used by the trucks or control).
- "Excavator one start digging", "all excavators start digging", "excavator at the middle face start digging", "the lower face excavator start digging" → DIGGING ("Copy, Excavator One digging at Lower Face.")
- "Excavator two hold" / "hold digging" / "stop digging" / "pause", "all excavators hold", "every digger hold" → HOLDING ("Excavator Two holding.")
- Also accepted: "digger one", "shovel one", "excavator 1", and mishearings such as "excavate her one", "digger won" and "x cavator two". A truck command and an excavator command can be given in one sentence.
- **Safety:** "everyone stop", "all stop", the ALL STOP button and RELEASE put every excavator on hold too.
- **Excavators as truck destinations:** "truck one go to excavator two", "truck one load at excavator one", "send truck two to digger three" → that excavator's face load point. With no number ("truck one to the excavator") the truck goes to the nearest DIGGING excavator, or the nearest excavator if none is digging. "Truck one haul from excavator two" hauls from that face to the crusher.
- **At the face:** when a hauling truck reaches a load point whose excavator is digging, the excavator calls "Excavator One loading Truck Two." If the excavator is holding, the truck calls "Truck Two, at Lower Face, waiting on Excavator One." By default loading still proceeds after the dwell. Set `RADIO_EXC_WAIT=1` to make the truck wait until that excavator starts digging.
- Status reports for all trucks also include the excavators. The phone shows excavator chips and square map markers, which pulse while digging. In LIVE mode the aerial view (`/view`) swings each excavator while it is digging.

**Unscripted speech:** with an OpenAI key, the LLM parses every command. The prompt includes trucks, excavators with their current state, and sites with aliases, and it maps loose phrasing to the closest tool. If the LLM fails or returns nothing, the regex parser runs. When the regex finds nothing, it snaps near-miss words to the radio vocabulary with difflib ("truk", "lowr", "crasher", "excavaytor") and tries again. It only answers "say again" if that also fails.

## How it works
- **STT**: `POST /api/radio/transcribe` (multipart `audio`: webm from Chrome, mp4 from iOS Safari) goes to ElevenLabs `POST /v1/speech-to-text` with `model_id=scribe_v2`, falling back to `scribe_v1`. With no ElevenLabs key it uses OpenAI `whisper-1` if an OpenAI key is set.
- **Parser**: OpenAI chat completions with tools `go_to`, `haul`, `return_to_parking`, `stop`, `status`, `dig` and `hold` (`go_to` and `haul` also take `excavator`). The system prompt lists the trucks and the site aliases, including common mishearings ("truck to", "red line", "middle phase"). Without a key, or on any error, the regex parser takes over. It is tested in `radio/test_parse.py`.
- **Routing**: road polylines are merged into a graph (shared endpoints become junctions), and Dijkstra runs from the road node nearest the truck to the site's `load_point`, `dump_point` or the truck's parking slot. Grid jitter is removed with Douglas-Peucker (1.5 m tolerance). Waypoints are kept wherever the road turns more than 12°, with straight fillers at most 20 m apart. The radio posts the next waypoint when the truck is within 9 m, or 5.5 m at bends, or once it has passed the waypoint. The last waypoint uses the brain's own 4 m arrive. If a truck circles a waypoint (the straight-line policy has an ~8.7 m turning radius), it is counted as reached after 5 s.
- **Haul**: load_point → (5 s loading) → dump_point → (4 s tipping) → repeat, for N loads or until another command. After N loads the truck returns to the ready line.
- **Override**: if someone else changes a truck's goal (the team web UI, a script), the radio drops its task for that truck and says so.
- **World hot-reload**: `world.json` is re-read when it changes, since envgen may regenerate it.
- **Mock mode**: if the brain is not reachable at startup, an in-process bicycle-model sim (with the brain's policy) moves the trucks, so the phone UI can be tested anywhere. When the real brain appears later, the radio switches over automatically. `RADIO_MOCK=1` forces mock and `RADIO_MOCK=0` forbids it.
- **TTS**: ElevenLabs `eleven_flash_v2_5`. Distinct premade voices are chosen from `GET /v1/voices` at startup: Sarah for control, and Adam, Callum and George for the trucks. The mp3s are cached in `radio/cache/audio`. With no key, `audio_url` is null and the phone uses `speechSynthesis` through the same squelch.

## API
| | |
|---|---|
| `GET /` | phone UI (`radio/web/index.html`) |
| `GET /api/radio/health` | mode (brain/mock), brain/sim link, which keys are active, last error |
| `GET /api/radio/state` | trucks (status, task, remaining route), `excavators: [{id, callsign, face, face_name, state, x, z, heading_deg, pos3}]` + last radio log entries |
| `GET /api/radio/excavators` | lightweight `[{id, face, state, heading_deg, pos3}]` (state = `digging` / `holding`), for pollers such as Unity |
| `GET /api/radio/world` | roads + sites for the minimap |
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
| `RADIO_WORLD` | `data/worlds/quarry/world.json` | road graph + sites |
| `RADIO_MOCK` | `auto` | `1` force mock, `0` never mock |
| `RADIO_MOCK_SPEED` | `1` | mock sim time multiplier |
| `RADIO_LOAD_DWELL` / `RADIO_DUMP_DWELL` | `5` / `4` s | |
| `RADIO_EXC_WAIT` | `0` | `1`: a hauling truck waits at the face until its excavator is digging |
| `OPENAI_MODEL` / `OPENAI_BASE_URL` | `gpt-4.1-mini` / OpenAI | any OpenAI-compatible gateway |
| `ELEVENLABS_TTS_MODEL` / `ELEVENLABS_STT_MODEL` | `eleven_flash_v2_5` / `scribe_v2` | |
| `ELEVENLABS_VOICE_IDS` | auto | comma list: control, truck-0, truck-1, truck-2, excavators |

Keys: `ELEVENLABS_API_KEY` and `OPENAI_API_KEY`, read from env, then `radio/secrets/<lowercase>`, then `envgen/secrets/<lowercase>`.

## Tests
```sh
PORT=8091 RADIO_MOCK=1 bash radio/run.sh &                                                     # optional: mock instance for the e2e part
uv run -q --with httpx --with fastapi --with python-multipart --with 'uvicorn[standard]' python radio/test_parse.py   # parser, routing, e2e (skipped if :8091 is down)
RADIO_MOCK=1 RADIO_MOCK_SPEED=4 radio/run.sh                                                      # UI without a brain
```
End-to-end without Unity, using the team's fake sim from origin/main:
```sh
git worktree add /tmp/main-wt origin/main && cd /tmp/main-wt/brain
PORT=8081 SIM_PORT=9001 HOST=127.0.0.1 uv run brain &      # brain
uv run fakesim --port 9001 &                                # fake sim (flat, ±100 m, its own rocks)
BRAIN_URL=http://127.0.0.1:8081 radio/run.sh                # from the repo root
```

## Docker (e.g. on Des's laptop)

```bash
ssh -N -L 8080:127.0.0.1:8080 watson     # keep running: brain tunnel
zsh radio/add_keys.sh                    # once: ElevenLabs key -> radio/secrets/ (mounted read-only, never baked in)
bash radio/docker-up.sh                  # builds + starts radio and a cloudflared tunnel, prints the phone URL
```
The container reaches the brain at `host.docker.internal:8080` (override with `BRAIN_URL=...`). Stop it with
`docker compose -f radio/docker-compose.yml down`.
