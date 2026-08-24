#!/usr/bin/env bash
set -Eeuo pipefail

cd /home/tictactoe-app/app
git fetch origin main
git reset --hard origin/main
exec /home/tictactoe-app/app/infra/deploy.sh
