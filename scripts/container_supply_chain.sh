#!/usr/bin/env bash
set -euo pipefail

readonly IMAGE="${COREVIEW_IMAGE:-coreview-cloud-lab:0.5b}"
readonly ARTIFACT_DIR="${1:-artifacts/container}"
readonly TRIVY_IMAGE="aquasec/trivy@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969"
readonly TRIVY_CACHE_VOLUME="${COREVIEW_TRIVY_CACHE_VOLUME:-coreview-trivy-cache}"

mkdir -p "$ARTIFACT_DIR"

docker image inspect "$IMAGE" > "$ARTIFACT_DIR/image-inspect.json"

run_trivy() {
  docker run --rm \
    --volume /var/run/docker.sock:/var/run/docker.sock \
    --volume "${TRIVY_CACHE_VOLUME}:/root/.cache/trivy" \
    "$TRIVY_IMAGE" "$@"
}

run_trivy image \
  --scanners vuln \
  --format cyclonedx \
  "$IMAGE" > "$ARTIFACT_DIR/sbom.cdx.json"

run_trivy image \
  --scanners vuln \
  --format json \
  "$IMAGE" > "$ARTIFACT_DIR/vulnerabilities.json"

# The complete report above always includes unfixed findings. The blocking gate
# is intentionally narrower: HIGH/CRITICAL findings with a published fix must
# be remediated or explicitly reviewed before an exception is introduced.
run_trivy image \
  --scanners vuln \
  --severity HIGH,CRITICAL \
  --ignore-unfixed \
  --exit-code 1 \
  "$IMAGE"

echo "Supply-chain artifacts written to $ARTIFACT_DIR"
