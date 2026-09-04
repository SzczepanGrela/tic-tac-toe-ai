from __future__ import annotations

import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = PROJECT_ROOT / "infra" / "deploy.sh"
LAUNCHER_SCRIPT = PROJECT_ROOT / "infra" / "deploy-launcher.example.sh"
DIGEST = f"sha256:{'a' * 64}"
REVISION = "b" * 40
PREVIOUS_REVISION = "c" * 40


FAKE_DOCKER = r"""#!/usr/bin/env bash
set -u
printf 'docker %s\n' "$*" >> "${COMMAND_LOG}"
containers="${DOCKER_STATE}/containers"
mkdir -p "$containers"

if [[ "$1 $2" == "network inspect" || "$1 $2" == "network connect" ]]; then
  exit 0
fi
if [[ "$1 $2" == "network create" || "$1" == "pull" || "$1" == "update" ]]; then
  exit 0
fi
if [[ "$1 $2" == "container inspect" ]]; then
  [[ "$3" == "nginx-proxy-manager" || -f "$containers/$3" ]]
  exit
fi
if [[ "$1 $2" == "image inspect" ]]; then
  if [[ "$3" == "--format" && "$4" == *revision* ]]; then
    printf '%s\n' "${IMAGE_REVISION}"
  fi
  exit 0
fi
if [[ "$1" == "inspect" && "$2" == "--format" ]]; then
  target="$4"
  if [[ "$target" == "nginx-proxy-manager" ]]; then
    printf '172.30.0.2\n'
  elif [[ "$3" == *Health.Status* ]]; then
    if [[ "$target" == *candidate* && "${CANDIDATE_HEALTH}" != "healthy" ]]; then
      printf 'unhealthy\n'
    else
      printf 'healthy\n'
    fi
  elif [[ "$3" == *revision* ]]; then
    cat "$containers/$target"
  elif [[ "$3" == *Image* ]]; then
    printf 'sha256:stale-previous-image\n'
  fi
  exit 0
fi
if [[ "$1" == "run" ]]; then
  previous=""
  name=""
  for argument in "$@"; do
    if [[ "$previous" == "--name" ]]; then
      name="$argument"
      break
    fi
    previous="$argument"
  done
  printf '%s\n' "${IMAGE_REVISION}" > "$containers/$name"
  exit 0
fi
if [[ "$1" == "exec" ]]; then
  target="$2"
  if [[ "$target" == "nginx-proxy-manager" && "$*" == *"nginx -s reload"* ]]; then
    count_file="${DOCKER_STATE}/reload-count"
    count=0
    [[ -f "$count_file" ]] && count="$(<"$count_file")"
    count=$((count + 1))
    printf '%s' "$count" > "$count_file"
    if [[ "${PROXY_RELOAD_FAIL_ONCE}" == "1" && "$count" -eq 1 ]]; then
      exit 1
    fi
  elif [[ "$target" == *candidate* && "${CANDIDATE_ENDPOINTS}" != "healthy" ]]; then
    exit 1
  fi
  exit 0
fi
if [[ "$1" == "rename" ]]; then
  mv "$containers/$2" "$containers/$3"
  exit 0
fi
if [[ "$1" == "rm" ]]; then
  target="${@: -1}"
  rm -f "$containers/$target"
  exit 0
fi
if [[ "$1" == "start" || "$1" == "stop" || "$1" == "logs" ]]; then
  exit 0
fi
if [[ "$1 $2" == "image rm" ]]; then
  exit 0
fi

exit 0
"""


FAKE_CURL = r"""#!/usr/bin/env bash
set -u
printf 'curl %s\n' "$*" >> "${COMMAND_LOG}"
if [[ "${PUBLIC_HEALTH}" != "healthy" ]]; then
  exit 22
fi
revision="$(<"${DOCKER_STATE}/containers/tic-tac-toe-ai")"
printf '{"status":"ok","revision":"%s"}\n' "$revision"
"""


def run_deploy(
    tmp_path: Path,
    *,
    candidate_health: str = "healthy",
    candidate_endpoints: str = "healthy",
    image_revision: str = REVISION,
    production_exists: bool = True,
    previous_exists: bool = False,
    proxy_reload_fail_once: bool = False,
    public_health: str = "healthy",
    digest: str = DIGEST,
) -> tuple[subprocess.CompletedProcess[str], str, Path]:
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    containers = state_dir / "containers"
    bin_dir.mkdir()
    containers.mkdir(parents=True)
    if production_exists:
        (containers / "tic-tac-toe-ai").write_text(PREVIOUS_REVISION)
    if previous_exists:
        (containers / "tic-tac-toe-ai-previous").write_text("d" * 40)

    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    log = tmp_path / "commands.log"
    log.touch()
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "COMMAND_LOG": str(log),
        "DOCKER_STATE": str(state_dir),
        "CANDIDATE_HEALTH": candidate_health,
        "CANDIDATE_ENDPOINTS": candidate_endpoints,
        "IMAGE_REVISION": image_revision,
        "PROXY_RELOAD_FAIL_ONCE": "1" if proxy_reload_fail_once else "0",
        "PUBLIC_HEALTH": public_health,
        "HEALTHCHECK_ATTEMPTS": "1",
        "HEALTHCHECK_INTERVAL": "0",
        "DRAIN_SECONDS": "0",
    }
    result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), digest],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log.read_text(), containers


def test_invalid_digest_is_rejected_before_docker(tmp_path: Path) -> None:
    result, log, _ = run_deploy(tmp_path, digest="latest")

    assert result.returncode == 64
    assert log == ""


def test_image_with_wrong_revision_label_is_rejected(tmp_path: Path) -> None:
    result, log, _ = run_deploy(tmp_path, image_revision="latest")

    assert result.returncode == 1
    assert "no valid" in result.stderr
    assert "docker run" not in log


def test_candidate_failure_keeps_production_running(tmp_path: Path) -> None:
    result, log, containers = run_deploy(tmp_path, candidate_health="unhealthy")

    assert result.returncode == 1
    assert "production remains unchanged" in result.stderr
    assert (containers / "tic-tac-toe-ai").read_text() == PREVIOUS_REVISION
    assert "docker rename tic-tac-toe-ai tic-tac-toe-ai-previous" not in log


def test_successful_candidate_is_promoted_and_previous_is_retained(tmp_path: Path) -> None:
    result, log, containers = run_deploy(tmp_path, previous_exists=True)

    assert result.returncode == 0
    assert "completed successfully" in result.stdout
    assert (containers / "tic-tac-toe-ai").read_text().strip() == REVISION
    assert (containers / "tic-tac-toe-ai-previous").read_text() == PREVIOUS_REVISION
    assert "--pids-limit 128" in log
    assert "--cap-drop ALL" in log
    assert "--log-opt max-size=10m" in log
    assert "FORWARDED_ALLOW_IPS=127.0.0.1,172.30.0.2" in log
    assert "docker exec nginx-proxy-manager nginx -t" in log
    assert "docker exec nginx-proxy-manager nginx -s reload" in log
    assert "docker stop --time 30 tic-tac-toe-ai-previous" in log
    assert "docker image rm sha256:stale-previous-image" in log
    assert "image prune" not in log


def test_proxy_failure_restores_previous_container(tmp_path: Path) -> None:
    result, log, containers = run_deploy(tmp_path, proxy_reload_fail_once=True)

    assert result.returncode == 1
    assert "Rollback completed successfully" in result.stderr
    assert (containers / "tic-tac-toe-ai").read_text() == PREVIOUS_REVISION
    assert "docker rename tic-tac-toe-ai-previous tic-tac-toe-ai" in log
    assert log.count("docker exec nginx-proxy-manager nginx -s reload") == 2


def test_public_check_failure_restores_previous_container(tmp_path: Path) -> None:
    result, _, containers = run_deploy(tmp_path, public_health="failed")

    assert result.returncode == 1
    assert (containers / "tic-tac-toe-ai").read_text() == PREVIOUS_REVISION


def test_launcher_accepts_only_the_digest_command(tmp_path: Path) -> None:
    fake_deploy = tmp_path / "deploy"
    fake_deploy.write_text("#!/usr/bin/env bash\nprintf '%s' \"$1\"\n")
    fake_deploy.chmod(0o755)
    launcher = tmp_path / "launcher"
    launcher.write_text(
        LAUNCHER_SCRIPT.read_text().replace(
            "/usr/local/libexec/grela-deploy/tictactoe-deploy", str(fake_deploy)
        )
    )
    launcher.chmod(0o755)

    accepted = subprocess.run(
        ["bash", str(launcher)],
        env=os.environ | {"SSH_ORIGINAL_COMMAND": f"deploy {DIGEST}"},
        capture_output=True,
        text=True,
        check=False,
    )
    rejected = subprocess.run(
        ["bash", str(launcher)],
        env=os.environ | {"SSH_ORIGINAL_COMMAND": "deploy latest"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert accepted.returncode == 0
    assert accepted.stdout == DIGEST
    assert rejected.returncode == 64
    assert "Rejected" in rejected.stderr
