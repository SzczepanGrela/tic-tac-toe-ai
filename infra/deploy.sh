#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE_REPOSITORY="tic-tac-toe-ai"
CONTAINER_NAME="tic-tac-toe-ai"
CANDIDATE_NAME="${CONTAINER_NAME}-candidate"
NETWORK_NAME="tictactoe-network"
NPM_CONTAINER="nginx-proxy-manager"
HEALTHCHECK_ATTEMPTS="${HEALTHCHECK_ATTEMPTS:-12}"
HEALTHCHECK_INTERVAL="${HEALTHCHECK_INTERVAL:-5}"
FORWARDED_ALLOW_IPS_VALUE="127.0.0.1"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

release_id="$(git rev-parse --short=12 HEAD)"
release_image="${IMAGE_REPOSITORY}:${release_id}"
previous_image=""

container_exists() {
  docker container inspect "$1" >/dev/null 2>&1
}

wait_until_healthy() {
  local container="$1"
  local status=""

  for ((attempt = 1; attempt <= HEALTHCHECK_ATTEMPTS; attempt++)); do
    status="$(docker inspect --format '{{.State.Health.Status}}' "${container}" 2>/dev/null || true)"
    if [[ "${status}" == "healthy" ]]; then
      return 0
    fi
    sleep "${HEALTHCHECK_INTERVAL}"
  done
  return 1
}

run_app() {
  local name="$1"
  local image="$2"
  local restart_policy="$3"

  docker run -d \
    --name "${name}" \
    --restart "${restart_policy}" \
    --network "${NETWORK_NAME}" \
    --cpus "1.0" \
    --memory "512m" \
    --read-only \
    --tmpfs /tmp:size=32m,noexec,nosuid \
    --security-opt no-new-privileges:true \
    --env "FORWARDED_ALLOW_IPS=${FORWARDED_ALLOW_IPS_VALUE}" \
    "${image}"
}

configure_trusted_proxy() {
  local proxy_ip=""

  if ! container_exists "${NPM_CONTAINER}"; then
    echo "Warning: ${NPM_CONTAINER} is not running; forwarded client addresses will not be trusted." >&2
    return 0
  fi

  docker network connect "${NETWORK_NAME}" "${NPM_CONTAINER}" >/dev/null 2>&1 || true
  proxy_ip="$(docker inspect --format "{{(index .NetworkSettings.Networks \"${NETWORK_NAME}\").IPAddress}}" "${NPM_CONTAINER}")"
  if [[ -z "${proxy_ip}" ]]; then
    echo "Could not determine the Nginx Proxy Manager address on ${NETWORK_NAME}." >&2
    return 1
  fi
  FORWARDED_ALLOW_IPS_VALUE="127.0.0.1,${proxy_ip}"
}

reload_proxy() {
  if ! container_exists "${NPM_CONTAINER}"; then
    echo "Warning: ${NPM_CONTAINER} is not running; skipping proxy reload." >&2
    return 0
  fi
  docker network connect "${NETWORK_NAME}" "${NPM_CONTAINER}" >/dev/null 2>&1 || true
  docker exec "${NPM_CONTAINER}" nginx -s reload
}

rollback() {
  echo "Production health check failed; rolling back." >&2
  docker logs --tail 100 "${CONTAINER_NAME}" >&2 || true
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

  if [[ -z "${previous_image}" ]]; then
    echo "No previous production image is available." >&2
    return 1
  fi

  run_app "${CONTAINER_NAME}" "${previous_image}" "unless-stopped" >/dev/null
  if ! reload_proxy; then
    echo "Warning: proxy reload failed while restoring the previous release." >&2
  fi
  if ! wait_until_healthy "${CONTAINER_NAME}"; then
    echo "Rollback container did not become healthy." >&2
    docker logs --tail 100 "${CONTAINER_NAME}" >&2 || true
    return 1
  fi
  echo "Rollback completed successfully." >&2
  return 1
}

cleanup_candidate() {
  docker rm -f "${CANDIDATE_NAME}" >/dev/null 2>&1 || true
}

trap cleanup_candidate EXIT

docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1 || docker network create "${NETWORK_NAME}"
configure_trusted_proxy
docker build --pull -f infra/Dockerfile -t "${release_image}" .

cleanup_candidate
run_app "${CANDIDATE_NAME}" "${release_image}" "no" >/dev/null

if ! wait_until_healthy "${CANDIDATE_NAME}"; then
  echo "Candidate health check failed; production remains unchanged." >&2
  docker logs --tail 100 "${CANDIDATE_NAME}" >&2 || true
  exit 1
fi

cleanup_candidate

if container_exists "${CONTAINER_NAME}"; then
  previous_image="$(docker inspect --format '{{.Image}}' "${CONTAINER_NAME}")"
  docker rm -f "${CONTAINER_NAME}" >/dev/null
fi

if ! run_app "${CONTAINER_NAME}" "${release_image}" "unless-stopped" >/dev/null; then
  rollback
fi

if ! reload_proxy; then
  echo "Proxy reload failed; restoring the previous release." >&2
  rollback
fi

if ! wait_until_healthy "${CONTAINER_NAME}"; then
  rollback
fi

docker tag "${release_image}" "${IMAGE_REPOSITORY}:latest"
docker image rm "${release_image}" >/dev/null 2>&1 || true
docker image prune -f
echo "Deployment ${release_id} completed successfully."
