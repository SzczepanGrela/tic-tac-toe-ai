#!/usr/bin/env bash
set -Eeuo pipefail

readonly DEPLOY_SCRIPT="/usr/local/libexec/grela-deploy/tictactoe-deploy"
readonly original_command="${SSH_ORIGINAL_COMMAND:-}"

if [[ ! "${original_command}" =~ ^deploy\ (sha256:[0-9a-f]{64})$ ]]; then
  echo "Rejected deployment command." >&2
  exit 64
fi

exec "${DEPLOY_SCRIPT}" "${BASH_REMATCH[1]}"
