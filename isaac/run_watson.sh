#!/usr/bin/env bash
# First Isaac Sim run on watson (NVIDIA GB10 / DGX Spark, aarch64, headless; no WebRTC livestream on aarch64).
# Targets the TEAM pipeline isaac/sim (Isaac Sim 6.2 container, spec.md "Isaac sim"). Idempotent: safe to re-run.
#
#   isaac/run_watson.sh check      GPU/driver/arch, docker + NVIDIA runtime, image, assets, brain/radio reachability
#   isaac/run_watson.sh smoke      isaac/smoke_test.py inside Isaac (headless) -> isaac/logs/smoke/ (PNGs, report.json)
#   isaac/run_watson.sh up         isaac/sim/main.py in the container (docker compose, profile isaac) -> brain :9000
#   isaac/run_watson.sh bridge     add-on path instead of `up`: isaac/brain_bridge.py --backend isaac (KeepInBounds on)
#   isaac/run_watson.sh status     brain /api/state summary (sim_connected, trucks, cameras) + container state
#   isaac/run_watson.sh logs       follow the sim container log
#   isaac/run_watson.sh down       stop the sim container(s)
#   isaac/run_watson.sh all        check -> smoke -> up -> status (default)
#
# Env overrides:
#   ISAAC_TAG=6.2.0            container tag (nvcr.io/nvidia/isaac-sim, multi-arch: same tag on aarch64)
#   ISAAC_MODE=container|native  native = use $ISAACSIM_PATH/python.sh (or ~/isaacsim) instead of docker
#   ISAACSIM_PATH=~/isaacsim   native install dir (must be Isaac 6.x: isaac/sim needs Python 3.12)
#   BRAIN_HOST=127.0.0.1 BRAIN_PORT=9000 BRAIN_WEB=http://127.0.0.1:8080 RADIO_URL=http://127.0.0.1:8090
#   WORLD=quarry  SMOKE_ARGS="--renderer RaytracedLighting"  SIM_ARGS="--fps 15"
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
LOGS="$HERE/logs"
CACHE="$REPO/downloads/isaac/cache"
ISAAC_TAG="${ISAAC_TAG:-6.2.0}"
IMAGE="nvcr.io/nvidia/isaac-sim:${ISAAC_TAG}"
LOCAL_IMAGE="mining-isaac-sim:${ISAAC_TAG}"          # built by isaac/docker/compose.yml
COMPOSE=(docker compose -f "$HERE/docker/compose.yml" --profile isaac)
ISAAC_MODE="${ISAAC_MODE:-container}"
BRAIN_HOST="${BRAIN_HOST:-127.0.0.1}"
BRAIN_PORT="${BRAIN_PORT:-9000}"
BRAIN_WEB="${BRAIN_WEB:-http://127.0.0.1:8080}"
RADIO_URL="${RADIO_URL:-http://127.0.0.1:8090}"
WORLD="${WORLD:-quarry}"
SMOKE_ARGS="${SMOKE_ARGS:-}"
SIM_ARGS="${SIM_ARGS:-}"
STAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$LOGS" "$LOGS/smoke" "$CACHE"
chmod 777 "$LOGS" "$LOGS/smoke" "$CACHE" 2>/dev/null || true   # container user is uid 1234

red() { printf '\033[31m%s\033[0m\n' "$*"; }
grn() { printf '\033[32m%s\033[0m\n' "$*"; }
ylw() { printf '\033[33m%s\033[0m\n' "$*"; }
hdr() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
FAILS=0
fail() { red "FAIL  $*"; FAILS=$((FAILS + 1)); }
warn() { ylw "WARN  $*"; }
ok()   { grn "OK    $*"; }
next() { printf '      -> %s\n' "$*"; }

# ---------------------------------------------------------------------------------------------- GPU flag for docker run
gpu_flags() {
  # compose.yml uses CDI (devices: nvidia.com/gpu=all). Mirror that when CDI specs exist, else the classic runtime.
  if command -v nvidia-ctk >/dev/null 2>&1 && nvidia-ctk cdi list 2>/dev/null | grep -q 'nvidia.com/gpu=all'; then
    echo "--device nvidia.com/gpu=all"
  else
    echo "--gpus all"
  fi
}

native_root() {
  local c
  for c in "${ISAACSIM_PATH:-}" "$HOME/isaacsim" "$HOME/isaac-sim" /isaac-sim; do
    [ -n "$c" ] && [ -x "$c/python.sh" ] && { echo "$c"; return 0; }
  done
  return 1
}

# ---------------------------------------------------------------------------------------------------------- check
cmd_check() {
  hdr "host"
  local arch; arch="$(uname -m)"
  [ "$arch" = "aarch64" ] && ok "arch aarch64 (DGX Spark build)" || warn "arch $arch (expected aarch64 on watson)"
  [ -r /etc/dgx-release ] && ok "DGX OS: $(grep -m1 DGX_SWBUILD_VERSION /etc/dgx-release 2>/dev/null | cut -d= -f2 | tr -d '\"')" \
    || warn "no /etc/dgx-release (Isaac aarch64 is supported only on DGX Spark / DGX OS 7)"

  hdr "GPU / driver"
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    fail "nvidia-smi not found"; next "install the DGX OS NVIDIA driver; nothing GPU-side can work without it"
  elif ! nvidia-smi >/dev/null 2>&1; then
    fail "nvidia-smi errors"; next "driver not loaded: sudo reboot, or check 'dmesg | grep -i nvrm'"
  else
    local name drv
    name="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
    drv="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
    ok "GPU $name, driver $drv"
    case "$drv" in
      580.*|59*|6*) ok "driver branch >= 580 (Isaac 6.2 DGX Spark lists 580.159.03; 5.1 listed 580.95.05)";;
      *) warn "driver $drv older than the 580 branch Isaac lists for DGX Spark"; next "sudo apt update && sudo apt full-upgrade (DGX OS)";;
    esac
    nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null | sed 's/^/      mem\/util: /'
  fi

  if [ "$ISAAC_MODE" = "native" ]; then
    hdr "native Isaac"
    local root
    if root="$(native_root)"; then
      ok "Isaac at $root"
      local pyv; pyv="$("$root/python.sh" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null | tail -1)"
      [ -f "$root/VERSION" ] && echo "      VERSION: $(head -1 "$root/VERSION")"
      case "$pyv" in
        3.12|3.13) ok "Isaac python $pyv";;
        *) fail "Isaac python '$pyv' (< 3.12 = Isaac 5.x): isaac/sim/main.py:91 is a SyntaxError there"
           next "use the container (ISAAC_MODE=container, default) with ISAAC_TAG=6.2.0";;
      esac
    else
      fail "no python.sh under \$ISAACSIM_PATH, ~/isaacsim, ~/isaac-sim, /isaac-sim"; next "unset ISAAC_MODE to use the container"
    fi
  else
    hdr "docker"
    if ! command -v docker >/dev/null 2>&1; then
      fail "docker not installed"
    elif ! docker info >/dev/null 2>&1; then
      fail "docker daemon not reachable (or no permission)"; next "sudo usermod -aG docker \$USER && newgrp docker"
    else
      ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
      docker compose version >/dev/null 2>&1 && ok "docker compose plugin" || fail "docker compose plugin missing"
      if docker info 2>/dev/null | grep -qi 'nvidia'; then ok "NVIDIA container runtime registered"; else
        warn "docker info shows no nvidia runtime"; next "sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"; fi
      if command -v nvidia-ctk >/dev/null 2>&1 && nvidia-ctk cdi list 2>/dev/null | grep -q 'nvidia.com/gpu=all'; then
        ok "CDI device nvidia.com/gpu=all (compose.yml uses it)"
      else
        warn "no CDI spec for nvidia.com/gpu=all: isaac/docker/compose.yml 'devices:' will fail"
        next "sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml   (smoke uses --gpus all meanwhile)"
      fi
      if docker image inspect "$IMAGE" >/dev/null 2>&1; then ok "image $IMAGE present"
      else warn "image $IMAGE not pulled yet (~20 GB; 'smoke'/'up' pull it)"; next "docker pull $IMAGE   (start this NOW, it is the long pole)"; fi
      if docker image inspect "$IMAGE" >/dev/null 2>&1; then
        local img_arch; img_arch="$(docker image inspect -f '{{.Architecture}}' "$IMAGE" 2>/dev/null)"
        [ "$img_arch" = "arm64" ] && ok "image arch arm64" || warn "image arch '$img_arch' (want arm64 on watson)"
        if docker run --rm $(gpu_flags) --entrypoint nvidia-smi "$IMAGE" -L >/dev/null 2>&1; then ok "GPU visible inside $IMAGE ($(gpu_flags))"
        else fail "GPU not visible inside the container ($(gpu_flags))"; next "sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"; fi
      fi
    fi
  fi

  hdr "repo assets"
  local f
  for f in "data/worlds/$WORLD/world.json" "data/worlds/$WORLD/collider.glb" "data/worlds/$WORLD/visual.glb" \
           "data/worlds/$WORLD/surround.glb" "data/worlds/$WORLD/sky_pano.jpg" "data/worlds/$WORLD/navgrid.json" \
           unity/Assets/Models/Trucks/komatsu_hd465.glb assets/models/excavator/excavator.glb isaac/sim/main.py; do
    if [ -s "$REPO/$f" ]; then
      if head -c 40 "$REPO/$f" | grep -q 'git-lfs'; then fail "$f is a git-lfs pointer"; next "git lfs pull"; else ok "$f ($(du -h "$REPO/$f" | cut -f1))"; fi
    else fail "$f missing"; next "git pull / rsync data/worlds/$WORLD from a laptop"; fi
  done
  df -h "$REPO" | awk 'NR==2{print "      disk free: "$4" on "$6}'

  hdr "brain / radio"
  if curl -fsS -m 3 "$BRAIN_WEB/api/state" -o "$LOGS/state.json" 2>/dev/null; then
    ok "brain web $BRAIN_WEB up ($(state_summary "$LOGS/state.json"))"
  else
    warn "brain web $BRAIN_WEB not answering"; next "cd $REPO && docker compose up -d brain   (NOT the 'fake' profile)"
  fi
  if (exec 3<>"/dev/tcp/$BRAIN_HOST/$BRAIN_PORT") 2>/dev/null; then ok "brain sim socket $BRAIN_HOST:$BRAIN_PORT open"
  else warn "brain sim socket $BRAIN_HOST:$BRAIN_PORT closed (needed for up/bridge, not for smoke)"; fi
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'fakesim'; then
    warn "a fakesim container is running: the brain keeps only the latest sim connection"
    next "docker compose stop fakesim"
  fi
  if curl -fsS -m 3 "$RADIO_URL/api/radio/health" -o "$LOGS/radio_health.json" 2>/dev/null; then
    ok "radio $RADIO_URL: $(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print("mode",d.get("mode"),"sim_connected",d.get("sim_connected"))' "$LOGS/radio_health.json" 2>/dev/null)"
  else
    warn "radio $RADIO_URL not answering (optional; radio/run.sh)"
  fi

  hdr "check summary"
  if [ "$FAILS" -eq 0 ]; then grn "check: no blocking failures"; else red "check: $FAILS failure(s) above"; fi
  return "$FAILS"
}

state_summary() {
  python3 - "$1" <<'PY' 2>/dev/null || echo "unparsable /api/state"
import json, sys
d = json.load(open(sys.argv[1]))
t = d.get("trucks", [])
print(f"sim_connected={d.get('sim_connected')} trucks={[x.get('id') for x in t]} cameras={d.get('cameras')} "
      f"moving={[x.get('id') for x in t if abs(x.get('speed') or 0) > 0.2]}")
PY
}

# ---------------------------------------------------------------------------------------------------------- smoke
cmd_smoke() {
  hdr "smoke test ($ISAAC_MODE, world $WORLD)"
  local log="$LOGS/smoke_${STAMP}.log"
  local rc
  if [ "$ISAAC_MODE" = "native" ]; then
    local root; root="$(native_root)" || { fail "no native Isaac"; return 1; }
    ( cd "$root" && ./python.sh "$REPO/isaac/smoke_test.py" --world "$REPO/data/worlds/$WORLD" --cache "$CACHE" \
        --out "$LOGS/smoke" --brain "$BRAIN_HOST:$BRAIN_PORT" $SMOKE_ARGS ) 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
  else
    docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "pulling $IMAGE (first time only, large)"; docker pull "$IMAGE" || { fail "docker pull $IMAGE"; next "docker login nvcr.io (user \$oauthtoken, NGC API key) or check network"; return 1; }; }
    docker rm -f mining-isaac-smoke >/dev/null 2>&1
    # Same mounts as isaac/docker/compose.yml (shader caches shared -> the first 'up' afterwards starts fast).
    docker run --rm --name mining-isaac-smoke $(gpu_flags) --network host \
      -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y -e NVIDIA_DRIVER_CAPABILITIES=all -e PYTHONUNBUFFERED=1 \
      -v "$REPO:/mining:ro" -v "$REPO/data:/data:ro" -v "$CACHE:/cache:rw" -v "$LOGS:/logs:rw" \
      -v mining-isaac-cache-main:/isaac-sim/.cache:rw -v mining-isaac-cache-compute:/isaac-sim/.nv/ComputeCache:rw \
      -v mining-isaac-cache-kit:/isaac-sim/kit/cache:rw -v mining-isaac-ovlogs:/isaac-sim/.nvidia-omniverse/logs:rw \
      -v mining-isaac-config:/isaac-sim/.nvidia-omniverse/config:rw -v mining-isaac-data:/isaac-sim/.local/share/ov/data:rw \
      --entrypoint /isaac-sim/python.sh "$IMAGE" \
      /mining/isaac/smoke_test.py --world "/data/worlds/$WORLD" --cache /cache --out /logs/smoke \
      --brain "$BRAIN_HOST:$BRAIN_PORT" $SMOKE_ARGS 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
  fi
  echo "log: $log   report: $LOGS/smoke/report.json   images: $LOGS/smoke/*.png"
  if [ "$rc" -eq 0 ]; then grn "SMOKE PASS"; return 0; fi
  red "SMOKE FAIL (exit $rc)"
  cat <<EOF
      next steps:
      - read the FAIL rows above (table at the end of $log)
      - 'python' FAIL: wrong Isaac (5.x = Python 3.11). Use ISAAC_TAG=6.2.0 container.
      - 'isaac_app' FAIL / no table at all: GPU not visible in the container -> 'isaac/run_watson.sh check';
        try SMOKE_ARGS="--renderer RaytracedLighting"; Kit log is in the mining-isaac-ovlogs volume.
      - 'warp' FAIL: no depth/lidar. Try: docker run ... /isaac-sim/python.sh -m pip install warp-lang
      - 'stage' FAIL: asset conversion (cache $CACHE must be writable by uid 1234: chmod 777)
      - 'rgb' black: first run compiles shaders (minutes); re-run smoke, it is cached in the volumes
      - can't fix in 15 min -> CHECKLIST.md fallback (fakesim / radio mock + /view)
EOF
  return "$rc"
}

# ---------------------------------------------------------------------------------------------------------- up / bridge
cmd_up() {
  hdr "isaac/sim up (container mining-isaac-sim -> brain $BRAIN_HOST:$BRAIN_PORT)"
  if docker ps --format '{{.Names}}' | grep -q '^mining-isaac-bridge$'; then
    warn "mining-isaac-bridge is running; stopping it (one sim connection at a time)"; docker rm -f mining-isaac-bridge >/dev/null
  fi
  if docker ps --format '{{.Names}}' | grep -q '^mining-isaac-sim$'; then
    ok "mining-isaac-sim already running (idempotent: not restarting; 'down' then 'up' to restart)"
  else
    if [ "$ISAAC_MODE" = "native" ]; then
      local root; root="$(native_root)" || { fail "no native Isaac"; return 1; }
      local log="$LOGS/sim_${STAMP}.log"
      ( cd "$REPO" && nohup "$root/python.sh" isaac/sim/main.py --brain "$BRAIN_HOST:$BRAIN_PORT" \
          --world "data/worlds/$WORLD" --cache "$CACHE" $SIM_ARGS >"$log" 2>&1 & echo $! >"$LOGS/sim.pid" )
      ok "native isaac/sim started, pid $(cat "$LOGS/sim.pid"), log $log"; return 0
    fi
    ISAAC_TAG="$ISAAC_TAG" "${COMPOSE[@]}" up -d --build 2>&1 | tee "$LOGS/compose_up_${STAMP}.log"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
      fail "compose up"
      next "CDI error ('unresolvable CDI devices')? sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"
      next "or use the add-on path: isaac/run_watson.sh bridge (plain docker run --gpus all)"
      return 1
    fi
  fi
  ( docker logs -f mining-isaac-sim >"$LOGS/sim_${STAMP}.log" 2>&1 & )
  wait_connected "mining-isaac-sim"
}

cmd_bridge() {
  hdr "add-on bridge: isaac/brain_bridge.py --backend isaac (isaac/sim scene + KeepInBounds, Unity rates)"
  if docker ps --format '{{.Names}}' | grep -q '^mining-isaac-sim$'; then
    warn "mining-isaac-sim is running; stopping it (one sim connection at a time)"; "${COMPOSE[@]}" down >/dev/null 2>&1
  fi
  if docker ps --format '{{.Names}}' | grep -q '^mining-isaac-bridge$'; then ok "mining-isaac-bridge already running"; else
    docker run -d --name mining-isaac-bridge --restart unless-stopped $(gpu_flags) --network host \
      -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y -e NVIDIA_DRIVER_CAPABILITIES=all -e PYTHONUNBUFFERED=1 \
      -e BRAIN_HOST="$BRAIN_HOST" -e BRAIN_PORT="$BRAIN_PORT" \
      -v "$REPO:/mining:ro" -v "$REPO/data:/data:ro" -v "$CACHE:/cache:rw" \
      -v mining-isaac-cache-main:/isaac-sim/.cache:rw -v mining-isaac-cache-compute:/isaac-sim/.nv/ComputeCache:rw \
      -v mining-isaac-cache-kit:/isaac-sim/kit/cache:rw -v mining-isaac-config:/isaac-sim/.nvidia-omniverse/config:rw \
      --entrypoint /isaac-sim/python.sh "$IMAGE" \
      /mining/isaac/brain_bridge.py --backend isaac --world "/data/worlds/$WORLD" --cache /cache $SIM_ARGS \
      || { fail "docker run bridge"; return 1; }
  fi
  ( docker logs -f mining-isaac-bridge >"$LOGS/bridge_${STAMP}.log" 2>&1 & )
  wait_connected "mining-isaac-bridge"
}

wait_connected() {
  local name="$1" i
  echo "waiting up to 300 s for sim_connected + trucks in $BRAIN_WEB/api/state (first start compiles shaders) ..."
  for i in $(seq 1 60); do
    if curl -fsS -m 3 "$BRAIN_WEB/api/state" -o "$LOGS/state.json" 2>/dev/null \
       && python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));sys.exit(0 if d.get("sim_connected") and d.get("trucks") else 1)' "$LOGS/state.json" 2>/dev/null; then
      grn "CONNECTED: $(state_summary "$LOGS/state.json")"
      echo "      view: ssh -N -L 8080:127.0.0.1:8080 watson  ->  http://localhost:8080"
      return 0
    fi
    if ! docker ps --format '{{.Names}}' | grep -q "^$name\$" && [ "$ISAAC_MODE" != "native" ]; then
      fail "$name exited"; docker logs --tail 60 "$name" 2>&1 | sed 's/^/      | /'; break
    fi
    sleep 5
  done
  fail "sim did not connect to the brain"
  next "docker logs --tail 100 $name   ('brain not reachable' = start brain; Traceback = see CHECKLIST.md)"
  next "is fakesim running? the brain keeps only the latest sim connection"
  return 1
}

cmd_status() {
  hdr "status"
  docker ps --format '{{.Names}}\t{{.Status}}' 2>/dev/null | grep -E 'mining|brain|isaac|fakesim' | sed 's/^/      /'
  if curl -fsS -m 3 "$BRAIN_WEB/api/state" -o "$LOGS/state.json"; then ok "$(state_summary "$LOGS/state.json")"
  else fail "brain $BRAIN_WEB/api/state not reachable"; fi
  local t
  for t in truck-0 truck-1 truck-2; do
    local n; n="$(curl -fsS -m 3 -D - -o /dev/null "$BRAIN_WEB/api/trucks/$t/lidar" 2>/dev/null | grep -i '^x-lidar' | sed -E 's/.*"points": *([0-9]+).*/\1/')"
    [ -n "$n" ] && [ "$n" != "0" ] && ok "lidar $t: $n points" || warn "lidar $t: none yet"
  done
}

cmd_logs() { docker logs -f --tail 100 "$(docker ps --format '{{.Names}}' | grep -E '^mining-isaac-(sim|bridge)$' | head -1)"; }
cmd_down() {
  "${COMPOSE[@]}" down 2>/dev/null; docker rm -f mining-isaac-bridge mining-isaac-smoke >/dev/null 2>&1
  [ -f "$LOGS/sim.pid" ] && kill "$(cat "$LOGS/sim.pid")" 2>/dev/null && rm -f "$LOGS/sim.pid"
  ok "sim containers stopped"
}

case "${1:-all}" in
  check)  cmd_check ;;
  smoke)  cmd_smoke ;;
  up)     cmd_up ;;
  bridge) cmd_bridge ;;
  status) cmd_status ;;
  logs)   cmd_logs ;;
  down)   cmd_down ;;
  all)    cmd_check; cmd_smoke && cmd_up && cmd_status ;;
  *) sed -n '2,22p' "$0"; exit 2 ;;
esac
