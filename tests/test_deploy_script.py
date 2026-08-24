from __future__ import annotations

import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = PROJECT_ROOT / "infra" / "deploy.sh"


FAKE_DOCKER = r"""#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "${DOCKER_LOG}"

if [[ "$1 $2" == "network inspect" ]]; then
  exit 0
fi
if [[ "$1 $2" == "container inspect" ]]; then
  if [[ "$3" == "nginx-proxy-manager" ]]; then
    exit 0
  fi
  if [[ "$3" == "${PRODUCTION_NAME}" && "${PRODUCTION_EXISTS}" == "1" ]]; then
    exit 0
  fi
  exit 1
fi
if [[ "$1" == "build" || "$1" == "run" || "$1" == "rm" || "$1" == "tag" || "$1" == "exec" ]]; then
  exit 0
fi
if [[ "$1 $2" == "image rm" || "$1 $2" == "image prune" || "$1 $2" == "network connect" ]]; then
  exit 0
fi
if [[ "$1" == "logs" ]]; then
  exit 0
fi
if [[ "$1" == "inspect" && "$2" == "--format" ]]; then
  target="$4"
  if [[ "$3" == *Health.Status* ]]; then
    if [[ "$target" == *candidate ]]; then
      [[ "${CANDIDATE_HEALTH}" == "healthy" ]] && printf 'healthy\n' || printf 'unhealthy\n'
    else
      count_file="${DOCKER_STATE}/production-health-count"
      count=0
      [[ -f "$count_file" ]] && count="$(<"$count_file")"
      count=$((count + 1))
      printf '%s' "$count" > "$count_file"
      if [[ "${PRODUCTION_HEALTH}" == "rollback" && "$count" -eq 1 ]]; then
        printf 'unhealthy\n'
      else
        printf 'healthy\n'
      fi
    fi
  else
    printf 'sha256:previous-image\n'
  fi
  exit 0
fi

exit 0
"""


def run_deploy(
    tmp_path: Path, *, candidate: str, production: str, exists: bool
) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    bin_dir.mkdir()
    state_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "DOCKER_STATE": str(state_dir),
        "PRODUCTION_NAME": "tic-tac-toe-ai",
        "PRODUCTION_EXISTS": "1" if exists else "0",
        "CANDIDATE_HEALTH": candidate,
        "PRODUCTION_HEALTH": production,
        "HEALTHCHECK_ATTEMPTS": "1",
        "HEALTHCHECK_INTERVAL": "0",
    }
    result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log.read_text()


def test_candidate_failure_keeps_production_running(tmp_path: Path) -> None:
    result, log = run_deploy(
        tmp_path, candidate="unhealthy", production="healthy", exists=True
    )

    assert result.returncode == 1
    assert "production remains unchanged" in result.stderr
    assert "rm -f tic-tac-toe-ai\n" not in log
    assert "run -d --name tic-tac-toe-ai " not in log


def test_successful_candidate_is_promoted(tmp_path: Path) -> None:
    result, log = run_deploy(
        tmp_path, candidate="healthy", production="healthy", exists=True
    )

    assert result.returncode == 0
    assert "completed successfully" in result.stdout
    assert "rm -f tic-tac-toe-ai\n" in log
    assert "run -d --name tic-tac-toe-ai " in log
    assert "exec nginx-proxy-manager nginx -s reload" in log


def test_failed_promotion_restores_previous_image(tmp_path: Path) -> None:
    result, log = run_deploy(
        tmp_path, candidate="healthy", production="rollback", exists=True
    )

    assert result.returncode == 1
    assert "Rollback completed successfully" in result.stderr
    assert "sha256:previous-image" in log
    assert log.count("run -d --name tic-tac-toe-ai ") == 2
