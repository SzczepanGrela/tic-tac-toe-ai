#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE_NAME="tic-tac-toe-ai:latest"
CONTAINER_NAME="tic-tac-toe-ai"
NETWORK_NAME="tictactoe-network"
NPM_CONTAINER="nginx-proxy-manager"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1 || docker network create "${NETWORK_NAME}"
docker build --pull -f infra/Dockerfile -t "${IMAGE_NAME}" .
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  --network "${NETWORK_NAME}" \
  --cpus "1.0" \
  --memory "512m" \
  --read-only \
  --tmpfs /tmp:size=32m,noexec,nosuid \
  --security-opt no-new-privileges:true \
  "${IMAGE_NAME}"

docker network connect "${NETWORK_NAME}" "${NPM_CONTAINER}" >/dev/null 2>&1 || true

for attempt in {1..12}; do
  status="$(docker inspect --format '{{.State.Health.Status}}' "${CONTAINER_NAME}")"
  if [[ "${status}" == "healthy" ]]; then
    docker image prune -f
    exit 0
  fi
  sleep 5
done

docker logs --tail 100 "${CONTAINER_NAME}"
exit 1
