#!/usr/bin/env bash
set -euo pipefail

readonly image="${COREVIEW_IMAGE:-coreview-cloud-lab:0.5c}"
readonly output_dir="${1:?usage: export_ci_image.sh OUTPUT_DIR}"
readonly source_inspect="${COREVIEW_SOURCE_IMAGE_INSPECT:-artifacts/container/image-inspect.json}"
readonly archive_name="coreview-validated-image.tar"

: "${GITHUB_SHA:?GITHUB_SHA is required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
: "${GITHUB_RUN_ATTEMPT:?GITHUB_RUN_ATTEMPT is required}"
: "${GITHUB_RUN_NUMBER:?GITHUB_RUN_NUMBER is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GITHUB_SERVER_URL:?GITHUB_SERVER_URL is required}"
: "${GITHUB_WORKFLOW:?GITHUB_WORKFLOW is required}"
: "${GITHUB_WORKFLOW_REF:?GITHUB_WORKFLOW_REF is required}"

if [[ ! "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "GITHUB_SHA must be a full 40-character commit SHA." >&2
  exit 1
fi

for evidence in \
  "$source_inspect" \
  artifacts/container/sbom.cdx.json \
  artifacts/container/vulnerabilities.json
do
  if [[ ! -s "$evidence" ]]; then
    echo "Required validation evidence is missing: $evidence" >&2
    exit 1
  fi
done

umask 077
mkdir -p "$output_dir"

current_image_id="$(docker image inspect "$image" --format '{{.Id}}')"
validated_image_id="$(python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))[0]["Id"])' "$source_inspect")"
if [[ "$current_image_id" != "$validated_image_id" ]]; then
  echo "The image tag no longer points to the image inspected and scanned in this run." >&2
  exit 1
fi

docker image inspect "$image" > "$output_dir/image-inspect.json"

python - "$output_dir/image-inspect.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    inspect = json.load(stream)[0]

config = inspect.get("Config") or {}
env_keys = {
    entry.partition("=")[0]
    for entry in config.get("Env") or []
}
forbidden_env_keys = {
    "APP_SECRET_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "COREVIEW_LAB_ADMIN_PASSWORD",
    "COREVIEW_LAB_APP_SECRET",
    "PRTG_PASSHASH",
    "PRTG_PASSWORD",
}
if env_keys & forbidden_env_keys:
    raise SystemExit("Image configuration contains a forbidden secret environment key.")

labels = config.get("Labels") or {}
label_text = "\n".join(f"{key}={value}" for key, value in labels.items())
for marker in ("BEGIN PRIVATE KEY", "AWS_SECRET_ACCESS_KEY", "COREVIEW_LAB_APP_SECRET"):
    if marker in label_text:
        raise SystemExit("Image labels contain a forbidden secret marker.")
PY

if docker image history --no-trunc --format '{{.CreatedBy}}' "$image" \
  | grep --quiet --extended-regexp \
    'COREVIEW_LAB_(APP_SECRET|ADMIN_PASSWORD)|AWS_(SECRET_ACCESS_KEY|SESSION_TOKEN)|PRTG_(PASSWORD|PASSHASH)'
then
  echo "Image build history contains a forbidden secret marker." >&2
  exit 1
fi

docker run --rm \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --entrypoint python \
  "$image" \
  -c '
from pathlib import Path

forbidden = (
    Path("/app/.env"),
    Path("/app/backend.hcl"),
    Path("/app/estado_sensores_prtg.json"),
    Path("/app/servers.json"),
    Path("/app/terraform.tfvars"),
    Path("/app/usuarios.db"),
)
runtime_roots = (Path("/runtime"), Path("/app/static/uploads"))
if any(path.exists() for path in forbidden):
    raise SystemExit("Image filesystem contains a forbidden runtime file.")
if any(path.is_file() for root in runtime_roots for path in root.rglob("*")):
    raise SystemExit("Image filesystem contains embedded runtime data.")
'

docker image save --output "$output_dir/$archive_name" "$image"
(
  cd "$output_dir"
  sha256sum "$archive_name" > "$archive_name.sha256"
  sha256sum --check "$archive_name.sha256"
)

archive_sha256="$(awk '{print $1}' "$output_dir/$archive_name.sha256")"
image_os="$(docker image inspect "$image" --format '{{.Os}}')"
image_architecture="$(docker image inspect "$image" --format '{{.Architecture}}')"

GITHUB_SHA="$GITHUB_SHA" \
GITHUB_RUN_ID="$GITHUB_RUN_ID" \
GITHUB_RUN_ATTEMPT="$GITHUB_RUN_ATTEMPT" \
GITHUB_RUN_NUMBER="$GITHUB_RUN_NUMBER" \
GITHUB_REPOSITORY="$GITHUB_REPOSITORY" \
GITHUB_SERVER_URL="$GITHUB_SERVER_URL" \
GITHUB_WORKFLOW="$GITHUB_WORKFLOW" \
GITHUB_WORKFLOW_REF="$GITHUB_WORKFLOW_REF" \
IMAGE_REFERENCE="$image" \
IMAGE_ID="$current_image_id" \
IMAGE_OS="$image_os" \
IMAGE_ARCHITECTURE="$image_architecture" \
ARCHIVE_NAME="$archive_name" \
ARCHIVE_SHA256="$archive_sha256" \
python - "$output_dir/metadata.json" <<'PY'
import json
import os
import sys

metadata = {
    "schema_version": "1.0",
    "source_commit": os.environ["GITHUB_SHA"],
    "run_id": os.environ["GITHUB_RUN_ID"],
    "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
    "run_number": os.environ["GITHUB_RUN_NUMBER"],
    "repository": os.environ["GITHUB_REPOSITORY"],
    "run_url": (
        f'{os.environ["GITHUB_SERVER_URL"]}/{os.environ["GITHUB_REPOSITORY"]}'
        f'/actions/runs/{os.environ["GITHUB_RUN_ID"]}'
    ),
    "workflow": os.environ["GITHUB_WORKFLOW"],
    "workflow_ref": os.environ["GITHUB_WORKFLOW_REF"],
    "image_reference": os.environ["IMAGE_REFERENCE"],
    "image_id": os.environ["IMAGE_ID"],
    "os": os.environ["IMAGE_OS"],
    "architecture": os.environ["IMAGE_ARCHITECTURE"],
    "archive": os.environ["ARCHIVE_NAME"],
    "archive_sha256": os.environ["ARCHIVE_SHA256"],
    "secret_checks": {
        "build_history": "passed",
        "config_and_labels": "passed",
        "filesystem": "passed",
    },
}
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(metadata, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY

echo "Validated CI image prepared for short-retention artifact upload."
