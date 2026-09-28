#!/usr/bin/env sh
set -eu

PLAYWRIGHT_IMAGE="${COREVIEW_PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27}"
NETWORK="coreview-offline-browser-$$"
ARTIFACTS="${COREVIEW_BROWSER_ARTIFACTS_HOST:-$PWD/artifacts/offline-frontend}"
SIMULATOR_CONTAINER="$(docker compose ps -q simulator)"

if [ -z "$SIMULATOR_CONTAINER" ]; then
  echo "The Docker laboratory is not running; start it before the browser smoke." >&2
  exit 1
fi

cleanup() {
  docker network disconnect "$NETWORK" "$SIMULATOR_CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

mkdir -p "$ARTIFACTS"
docker network create --internal "$NETWORK" >/dev/null
docker network connect --alias coreview-offline-app "$NETWORK" "$SIMULATOR_CONTAINER"

docker run --rm \
  --network "$NETWORK" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -e COREVIEW_LAB_ADMIN_USER="${COREVIEW_LAB_ADMIN_USER:-lab-admin}" \
  -e COREVIEW_LAB_ADMIN_PASSWORD="${COREVIEW_LAB_ADMIN_PASSWORD:?Set COREVIEW_LAB_ADMIN_PASSWORD}" \
  -e COREVIEW_BROWSER_BASE_URL=http://coreview-offline-app:5067 \
  -e COREVIEW_BROWSER_SIMULATOR_URL=http://coreview-offline-app:9090 \
  -e COREVIEW_BROWSER_ARTIFACTS=/artifacts \
  -v "$PWD:/work:ro" \
  -v "$ARTIFACTS:/artifacts:rw" \
  -w /work \
  "$PLAYWRIGHT_IMAGE" \
  node scripts/offline_frontend_smoke.mjs
