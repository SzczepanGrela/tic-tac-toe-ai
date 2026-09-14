from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Mapping

from infra.coolify_release import (
    ACTIVE_DEPLOYMENT_STATUSES,
    FAILED_DEPLOYMENT_STATUSES,
    UUID_PATTERN,
    CoolifyClient,
    ReleaseError,
    UncertainDeployment,
    append_summary,
    cancel_and_confirm,
    digest_to_tag,
    token_from_environment,
    verify_no_running_deployment,
)


CANARY_NAME = "tictactoe-deployment-canary"
IMAGE_REPOSITORY = "ghcr.io/szczepangrela/tic-tac-toe-ai"
HEALTHCHECK_COMMAND = "python -m web.healthcheck"


def verify_isolated_canary(
    application: Mapping[str, object],
    application_uuid: str,
    production_application_uuid: str,
    stable_tag: str,
) -> None:
    if application_uuid == production_application_uuid:
        raise ReleaseError("the safety check must not target production")
    expected = {
        "uuid": application_uuid,
        "name": CANARY_NAME,
        "fqdn": None,
        "ports_mappings": None,
        "docker_registry_image_name": IMAGE_REPOSITORY,
        "docker_registry_image_tag": stable_tag,
        "build_pack": "dockerimage",
        "health_check_enabled": True,
        "health_check_type": "cmd",
        "health_check_command": HEALTHCHECK_COMMAND,
        "health_check_interval": 5,
        "health_check_timeout": 5,
        "health_check_retries": 10,
        "health_check_start_period": 10,
        "status": "running:healthy",
    }
    mismatches = [key for key, value in expected.items() if application.get(key) != value]
    if mismatches:
        raise ReleaseError(
            "isolated canary configuration drift: " + ", ".join(mismatches)
        )


def wait_until_in_progress(
    client: CoolifyClient,
    deployment_uuid: str,
    *,
    attempts: int,
    interval: float,
) -> None:
    for _ in range(attempts):
        status = client.get_deployment(deployment_uuid).get("status")
        if status == "in_progress":
            return
        if status == "finished" or status in FAILED_DEPLOYMENT_STATUSES:
            raise ReleaseError(
                f"candidate reached terminal status {status} before cancellation"
            )
        if status != "queued":
            raise UncertainDeployment(
                f"Coolify returned unknown deployment status {status!r}"
            )
        time.sleep(interval)
    raise UncertainDeployment("candidate did not enter in_progress state")


def observe_active_candidate(
    client: CoolifyClient,
    deployment_uuid: str,
    *,
    checks: int,
    interval: float,
) -> None:
    for _ in range(checks):
        status = client.get_deployment(deployment_uuid).get("status")
        if status not in ACTIVE_DEPLOYMENT_STATUSES:
            if status == "finished" or status in FAILED_DEPLOYMENT_STATUSES:
                raise ReleaseError(
                    f"candidate reached terminal status {status} before cancellation"
                )
            raise UncertainDeployment(
                f"Coolify returned unknown deployment status {status!r}"
            )
        time.sleep(interval)


def wait_for_finished(
    client: CoolifyClient,
    deployment_uuid: str,
    *,
    timeout: int,
    interval: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get_deployment(deployment_uuid).get("status")
        if status == "finished":
            return
        if status in FAILED_DEPLOYMENT_STATUSES:
            raise ReleaseError(f"restoration deployment ended with status {status}")
        if status not in ACTIVE_DEPLOYMENT_STATUSES:
            raise UncertainDeployment(
                f"Coolify returned unknown deployment status {status!r}"
            )
        time.sleep(interval)
    raise UncertainDeployment("restoration deployment did not finish in time")


def restore_canary(
    client: CoolifyClient,
    application_uuid: str,
    production_application_uuid: str,
    stable_tag: str,
    *,
    timeout: int,
    interval: float,
) -> str:
    client.update_tag(application_uuid, stable_tag)
    saved = client.get_application(application_uuid)
    if saved.get("docker_registry_image_tag") != stable_tag:
        raise UncertainDeployment("Coolify did not save the stable canary digest")
    restoration_uuid = client.queue_deployment(application_uuid)
    wait_for_finished(
        client,
        restoration_uuid,
        timeout=timeout,
        interval=interval,
    )
    restored = client.get_application(application_uuid)
    verify_isolated_canary(
        restored,
        application_uuid,
        production_application_uuid,
        stable_tag,
    )
    return restoration_uuid


def run_safety_check(args: argparse.Namespace) -> None:
    stable_tag = digest_to_tag(args.stable_digest)
    candidate_tag = digest_to_tag(args.candidate_digest)
    if args.application_uuid == args.production_application_uuid:
        raise ReleaseError("the safety check must not target production")
    if stable_tag == candidate_tag:
        raise ReleaseError("candidate digest must differ from the stable digest")

    client = CoolifyClient(args.coolify_url, token_from_environment())
    current = client.get_application(args.application_uuid)
    verify_isolated_canary(
        current,
        args.application_uuid,
        args.production_application_uuid,
        stable_tag,
    )
    verify_no_running_deployment(client, args.application_uuid)

    client.update_tag(args.application_uuid, candidate_tag)
    saved = client.get_application(args.application_uuid)
    if saved.get("docker_registry_image_tag") != candidate_tag:
        raise UncertainDeployment("Coolify did not save the candidate digest")

    try:
        candidate_uuid = client.queue_deployment(args.application_uuid)
    except UncertainDeployment:
        raise

    try:
        wait_until_in_progress(
            client,
            candidate_uuid,
            attempts=args.start_attempts,
            interval=args.poll_interval,
        )
        observe_active_candidate(
            client,
            candidate_uuid,
            checks=args.observation_checks,
            interval=args.poll_interval,
        )
        cancel_and_confirm(
            client,
            candidate_uuid,
            timeout=args.cancel_timeout,
            interval=args.poll_interval,
        )
        candidate_status = client.get_deployment(candidate_uuid).get("status")
        if candidate_status not in FAILED_DEPLOYMENT_STATUSES:
            raise ReleaseError(
                "candidate cancellation ended with unexpected status "
                f"{candidate_status!r}"
            )
    except UncertainDeployment:
        raise
    except Exception as candidate_error:
        try:
            status = client.get_deployment(candidate_uuid).get("status")
        except ReleaseError as read_error:
            raise UncertainDeployment(
                f"candidate {candidate_uuid} state is unknown"
            ) from read_error
        if status in ACTIVE_DEPLOYMENT_STATUSES:
            raise UncertainDeployment(
                f"candidate {candidate_uuid} is still active"
            ) from candidate_error
        try:
            restoration_uuid = restore_canary(
                client,
                args.application_uuid,
                args.production_application_uuid,
                stable_tag,
                timeout=args.restore_timeout,
                interval=args.poll_interval,
            )
        except Exception as restoration_error:
            raise ReleaseError(
                f"safety check failed ({candidate_error}); restoration also failed "
                f"({restoration_error})"
            ) from restoration_error
        raise ReleaseError(
            f"safety check failed, but restoration {restoration_uuid} restored "
            f"{args.stable_digest}: {candidate_error}"
        ) from candidate_error

    restoration_uuid = restore_canary(
        client,
        args.application_uuid,
        args.production_application_uuid,
        stable_tag,
        timeout=args.restore_timeout,
        interval=args.poll_interval,
    )
    append_summary(
        [
            "### Deployment safety validation",
            "",
            f"- Cancelled candidate deployment: {candidate_uuid}",
            f"- Candidate digest: {args.candidate_digest}",
            f"- Restoration deployment: {restoration_uuid}",
            f"- Restored digest: {args.stable_digest}",
            "- Production application was excluded before mutation.",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Coolify cancellation and restoration on an isolated app."
    )
    parser.add_argument("--coolify-url", required=True)
    parser.add_argument("--application-uuid", required=True)
    parser.add_argument("--production-application-uuid", required=True)
    parser.add_argument("--stable-digest", required=True)
    parser.add_argument("--candidate-digest", required=True)
    parser.add_argument("--start-attempts", type=int, default=30)
    parser.add_argument("--observation-checks", type=int, default=5)
    parser.add_argument("--poll-interval", type=float, default=2)
    parser.add_argument("--cancel-timeout", type=int, default=60)
    parser.add_argument("--restore-timeout", type=int, default=600)
    args = parser.parse_args()
    for value in (args.application_uuid, args.production_application_uuid):
        if not UUID_PATTERN.fullmatch(value):
            parser.error("application UUID is invalid")
    for field in (
        "start_attempts",
        "observation_checks",
        "poll_interval",
        "cancel_timeout",
        "restore_timeout",
    ):
        if getattr(args, field) <= 0:
            parser.error(f"{field.replace('_', '-')} must be positive")
    return args


def main() -> int:
    try:
        run_safety_check(parse_args())
    except ReleaseError as exc:
        print(f"deployment safety error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
