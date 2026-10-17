#!/usr/bin/env bash
# usage (watson, from ~/Documents/mining): isaac/fx/run.sh <script.py> [args...]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$REPO/downloads/isaac/fx"
mkdir -p "$OUT" && chmod 777 "$OUT"
SCRIPT="$1"; shift
docker run --rm --name mining-isaac-fx --device nvidia.com/gpu=all --network=host \
  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y -e NVIDIA_DRIVER_CAPABILITIES=all -e PYTHONUNBUFFERED=1 \
  -v mining-isaac-cache-main:/isaac-sim/.cache:rw \
  -v mining-isaac-cache-compute:/isaac-sim/.nv/ComputeCache:rw \
  -v mining-isaac-cache-kit:/isaac-sim/kit/cache:rw \
  -v mining-isaac-ovlogs:/isaac-sim/.nvidia-omniverse/logs:rw \
  -v mining-isaac-config:/isaac-sim/.nvidia-omniverse/config:rw \
  -v mining-isaac-data:/isaac-sim/.local/share/ov/data:rw \
  -v "$REPO:/mining:ro" -v "$REPO/data:/data:ro" \
  -v "$REPO/downloads/isaac/cache:/cache:rw" -v "$REPO/downloads/isaac/out/usd:/usd:ro" \
  -v "$OUT:/out:rw" \
  --entrypoint /isaac-sim/python.sh nvcr.io/nvidia/isaac-sim:6.2.0 "$SCRIPT" "$@"
