#!/usr/bin/env bash
set -Eeuo pipefail

readonly IMAGE_REPOSITORY="ghcr.io/szczepangrela/tic-tac-toe-ai"
readonly CONTAINER_NAME="tic-tac-toe-ai"
readonly PREVIOUS_NAME="${CONTAINER_NAME}-previous"
readonly NETWORK_NAME="tictactoe-network"
readonly NPM_CONTAINER="nginx-proxy-manager"
readonly PUBLIC_HEALTH_URL="https://tictactoe.grela.dev/api/health"
readonly HEALTHCHECK_ATTEMPTS="${HEALTHCHECK_ATTEMPTS:-18}"
readonly HEALTHCHECK_INTERVAL="${HEALTHCHECK_INTERVAL:-5}"
readonly DRAIN_SECONDS="${DRAIN_SECONDS:-15}"

digest="${1:-}"
if [[ ! "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Usage: $0 sha256:<64 lowercase hexadecimal characters>" >&2
  exit 64
fi

readonly digest
readonly image_reference="${IMAGE_REPOSITORY}@${digest}"
readonly release_suffix="${digest#sha256:}"
readonly candidate_name="${CONTAINER_NAME}-candidate-${release_suffix:0:12}"

proxy_ip=""
release_revision=""
stale_previous_image=""
production_renamed=false
candidate_promoted=false

container_exists() {
  docker container inspect "$1" >/dev/null 2>&1
}

wait_until_healthy() {
  local container="$1"
  local status=""

  for ((attempt = 1; attempt <= HEALTHCHECK_ATTEMPTS; attempt++)); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "${container}" 2>/dev/null || true)"
    if [[ "${status}" == "healthy" ]]; then
      return 0
    fi
    sleep "${HEALTHCHECK_INTERVAL}"
  done
  return 1
}

run_app() {
  local name="$1"
  local restart_policy="$2"

  docker run -d \
    --name "${name}" \
    --restart "${restart_policy}" \
    --network "${NETWORK_NAME}" \
    --cpus "1.0" \
    --memory "512m" \
    --pids-limit "128" \
    --read-only \
    --tmpfs /tmp:rw,size=32m,noexec,nosuid,nodev \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --init \
    --stop-timeout 30 \
    --log-driver json-file \
    --log-opt max-size=10m \
    --log-opt max-file=3 \
    --env "FORWARDED_ALLOW_IPS=127.0.0.1,${proxy_ip}" \
    --env "RELEASE_REVISION=${release_revision}" \
    "${image_reference}"
}

check_container_endpoints() {
  local container="$1"

  docker exec "${container}" python -c '
import json
import sys
import urllib.request

expected = sys.argv[1]
for path in ("/", "/static/favicon.svg"):
    with urllib.request.urlopen("http://127.0.0.1:8084" + path, timeout=5) as response:
        if response.status != 200:
            raise SystemExit(f"{path} returned {response.status}")
with urllib.request.urlopen("http://127.0.0.1:8084/api/health", timeout=5) as response:
    health = json.load(response)
if health.get("status") != "ok" or health.get("revision") != expected:
    raise SystemExit(f"unexpected health payload: {health}")
request = urllib.request.Request(
    "http://127.0.0.1:8084/api/move",
    data=json.dumps({"board": [[0, 0, 0], [0, 0, 0], [0, 0, 0]], "algorithm": "rules", "seed": 1}).encode(),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=10) as response:
    if response.status != 200 or "move" not in json.load(response):
        raise SystemExit("move preflight failed")
' "${release_revision}"
}

check_public_revision() {
  local body=""
  body="$(curl --fail --silent --show-error --max-time 15 \
    "${PUBLIC_HEALTH_URL}?revision=${release_revision}")" || return 1
  [[ "${body}" == *'"status":"ok"'* && "${body}" == *"\"revision\":\"${release_revision}\""* ]]
}

configure_proxy() {
  if ! container_exists "${NPM_CONTAINER}"; then
    echo "Required proxy container ${NPM_CONTAINER} is not running." >&2
    return 1
  fi

  docker network connect "${NETWORK_NAME}" "${NPM_CONTAINER}" >/dev/null 2>&1 || true
  proxy_ip="$(docker inspect --format "{{(index .NetworkSettings.Networks \"${NETWORK_NAME}\").IPAddress}}" "${NPM_CONTAINER}")"
  if [[ -z "${proxy_ip}" ]]; then
    echo "Could not determine the Nginx Proxy Manager address on ${NETWORK_NAME}." >&2
    return 1
  fi
}

reload_proxy() {
  docker exec "${NPM_CONTAINER}" nginx -t
  docker exec "${NPM_CONTAINER}" nginx -s reload
}

restore_previous() {
  echo "Promotion failed; restoring the previous production container." >&2
  docker logs --tail 100 "${CONTAINER_NAME}" >&2 || true

  if container_exists "${CONTAINER_NAME}"; then
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  fi

  if [[ "${production_renamed}" != true ]] || ! container_exists "${PREVIOUS_NAME}"; then
    echo "No previous production container is available." >&2
    return 1
  fi

  docker rename "${PREVIOUS_NAME}" "${CONTAINER_NAME}"
  docker start "${CONTAINER_NAME}" >/dev/null
  reload_proxy
  wait_until_healthy "${CONTAINER_NAME}"

  local previous_revision=""
  previous_revision="$(docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "${CONTAINER_NAME}")"
  release_revision="${previous_revision}"
  check_public_revision
  echo "Rollback completed successfully." >&2
  return 1
}

cleanup_unpromoted_candidate() {
  if [[ "${candidate_promoted}" != true ]] && container_exists "${candidate_name}"; then
    docker rm -f "${candidate_name}" >/dev/null 2>&1 || true
  fi
}

trap cleanup_unpromoted_candidate EXIT

docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1 || docker network create "${NETWORK_NAME}"
configure_proxy

docker pull "${image_reference}"
release_revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "${image_reference}")"
if [[ ! "${release_revision}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Image has no valid org.opencontainers.image.revision label." >&2
  exit 1
fi

docker rm -f "${candidate_name}" >/dev/null 2>&1 || true
run_app "${candidate_name}" "no" >/dev/null

if ! wait_until_healthy "${candidate_name}" || ! check_container_endpoints "${candidate_name}"; then
  echo "Candidate preflight failed; production remains unchanged." >&2
  docker logs --tail 100 "${candidate_name}" >&2 || true
  exit 1
fi

if container_exists "${PREVIOUS_NAME}"; then
  stale_previous_image="$(docker inspect --format '{{.Image}}' "${PREVIOUS_NAME}")"
  docker rm -f "${PREVIOUS_NAME}" >/dev/null
fi

if container_exists "${CONTAINER_NAME}"; then
  docker rename "${CONTAINER_NAME}" "${PREVIOUS_NAME}"
  production_renamed=true
fi

docker rename "${candidate_name}" "${CONTAINER_NAME}"
candidate_promoted=true
docker update --restart unless-stopped "${CONTAINER_NAME}" >/dev/null

if ! reload_proxy \
  || ! wait_until_healthy "${CONTAINER_NAME}" \
  || ! check_container_endpoints "${CONTAINER_NAME}" \
  || ! check_public_revision; then
  restore_previous
fi

if [[ "${production_renamed}" == true ]] && container_exists "${PREVIOUS_NAME}"; then
  sleep "${DRAIN_SECONDS}"
  docker stop --time 30 "${PREVIOUS_NAME}" >/dev/null
fi

if [[ -n "${stale_previous_image}" ]]; then
  docker image rm "${stale_previous_image}" >/dev/null 2>&1 || true
fi

echo "Deployment ${release_revision} (${digest}) completed successfully."
