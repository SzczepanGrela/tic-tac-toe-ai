from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from web import smokecheck


DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
UUID_PATTERN = re.compile(r"^[a-z0-9]{20,32}$")
ACTIVE_DEPLOYMENT_STATUSES = {"queued", "in_progress"}
FAILED_DEPLOYMENT_STATUSES = {"cancelled", "cancelled-by-user", "failed"}


class ReleaseError(RuntimeError):
    """A deployment failed with a known outcome."""


class UncertainDeployment(ReleaseError):
    """A deployment request may have reached Coolify, so mutation must stop."""


def token_from_environment() -> str:
    token = os.environ.get("COOLIFY_TOKEN", "")
    if not token:
        raise ReleaseError("Coolify deployment token is required")
    return token


class CoolifyClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: int = 15,
        read_attempts: int = 3,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ReleaseError("Coolify URL must be an absolute HTTP or HTTPS URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ReleaseError(
                "Coolify URL must not contain credentials, a query, or a fragment"
            )
        path = parsed.path.rstrip("/")
        if path not in {"", "/api/v1"}:
            raise ReleaseError("Coolify URL path must be empty or /api/v1")
        origin = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, "", "", "")
        ).rstrip("/")
        self.api_url = f"{origin}/api/v1"
        self.token = token
        self.timeout = timeout
        self.read_attempts = read_attempts

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, object] | None = None,
    ) -> object:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"{self.api_url}{path}",
            data=data,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
            method=method,
        )
        attempts = self.read_attempts if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = response.read()
                    if not 200 <= response.status < 300:
                        raise ReleaseError(
                            f"{method} {path} returned HTTP {response.status}"
                        )
                try:
                    return json.loads(payload)
                except json.JSONDecodeError as exc:
                    message = f"{method} {path} returned invalid JSON"
                    if method == "GET":
                        raise ReleaseError(message) from exc
                    raise UncertainDeployment(message) from exc
            except urllib.error.HTTPError as exc:
                if method == "GET" and exc.code >= 500 and attempt < attempts:
                    time.sleep(attempt)
                    continue
                raise ReleaseError(
                    f"{method} {path} returned HTTP {exc.code}"
                ) from exc
            except (TimeoutError, urllib.error.URLError) as exc:
                if method == "GET" and attempt < attempts:
                    time.sleep(attempt)
                    continue
                message = f"{method} {path} did not return a response"
                if method == "GET":
                    raise ReleaseError(message) from exc
                raise UncertainDeployment(message) from exc
        raise AssertionError("unreachable")

    def get_application(self, application_uuid: str) -> Mapping[str, object]:
        payload = self._request(
            "GET",
            f"/applications/{application_uuid}",
        )
        if not isinstance(payload, Mapping):
            raise ReleaseError("Coolify returned an invalid application response")
        return payload

    def list_application_deployments(
        self,
        application_uuid: str,
    ) -> list[Mapping[str, object]]:
        payload = self._request(
            "GET",
            f"/deployments/applications/{application_uuid}?take=20",
        )
        if not isinstance(payload, Mapping):
            raise ReleaseError("Coolify returned an invalid deployment history")
        deployments = payload.get("deployments")
        if not isinstance(deployments, list) or not all(
            isinstance(item, Mapping) for item in deployments
        ):
            raise ReleaseError("Coolify returned an invalid deployment history")
        return deployments

    def update_tag(self, application_uuid: str, tag: str) -> None:
        try:
            self._request(
                "PATCH",
                f"/applications/{application_uuid}",
                {"docker_registry_image_tag": tag},
            )
        except UncertainDeployment as mutation_error:
            try:
                current = self.get_application(application_uuid)
            except ReleaseError as read_error:
                raise UncertainDeployment(
                    "Coolify image update outcome could not be reconciled"
                ) from read_error
            if current.get("docker_registry_image_tag") != tag:
                raise mutation_error

    def queue_deployment(self, application_uuid: str) -> str:
        payload = self._request(
            "POST",
            "/deploy",
            {"uuid": application_uuid, "force": False},
        )
        if not isinstance(payload, Mapping):
            raise UncertainDeployment(
                "Coolify returned an invalid deployment response"
            )
        deployments = payload.get("deployments")
        if not isinstance(deployments, list) or len(deployments) != 1:
            raise UncertainDeployment("Coolify did not return exactly one deployment")
        deployment = deployments[0]
        if not isinstance(deployment, Mapping):
            raise UncertainDeployment("Coolify returned an invalid deployment entry")
        if deployment.get("resource_uuid") != application_uuid:
            raise UncertainDeployment(
                "Coolify returned a deployment for another resource"
            )
        deployment_uuid = deployment.get("deployment_uuid")
        if not isinstance(deployment_uuid, str) or not UUID_PATTERN.fullmatch(
            deployment_uuid
        ):
            raise UncertainDeployment("Coolify returned an invalid deployment UUID")
        return deployment_uuid

    def get_deployment(self, deployment_uuid: str) -> Mapping[str, object]:
        payload = self._request(
            "GET",
            f"/deployments/{deployment_uuid}",
        )
        if not isinstance(payload, Mapping):
            raise ReleaseError("Coolify returned an invalid deployment status")
        if payload.get("deployment_uuid") != deployment_uuid:
            raise ReleaseError("Coolify returned status for another deployment")
        return payload

    def cancel_deployment(self, deployment_uuid: str) -> None:
        self._request(
            "POST",
            f"/deployments/{deployment_uuid}/cancel",
        )


def digest_to_tag(digest: str) -> str:
    if not DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseError(
            "image digest must use sha256 and 64 lowercase hex characters"
        )
    return digest.replace(":", "-", 1)


def tag_to_digest(tag: object) -> str:
    if not isinstance(tag, str) or not re.fullmatch(r"sha256-[0-9a-f]{64}", tag):
        raise ReleaseError("current Coolify image is not pinned by digest")
    return tag.replace("-", ":", 1)


def load_contract(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"could not load Coolify contract: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ReleaseError("Coolify contract must be a JSON object")
    return payload


def _compare_contract(
    actual: Mapping[str, object],
    expected: Mapping[str, object],
    prefix: str = "",
) -> list[str]:
    mismatches: list[str] = []
    for key, expected_value in expected.items():
        label = f"{prefix}.{key}" if prefix else key
        actual_value = actual.get(key)
        if isinstance(expected_value, Mapping):
            if not isinstance(actual_value, Mapping):
                mismatches.append(label)
            else:
                mismatches.extend(
                    _compare_contract(actual_value, expected_value, label)
                )
        elif actual_value != expected_value:
            mismatches.append(label)
    return mismatches


def verify_application(
    application: Mapping[str, object],
    contract: Mapping[str, object],
    application_uuid: str,
    *,
    expected_tag: str | None = None,
    require_healthy: bool = True,
) -> None:
    if application.get("uuid") != application_uuid:
        raise ReleaseError("Coolify returned another application")
    mismatches = _compare_contract(application, contract)
    if mismatches:
        raise ReleaseError(f"Coolify configuration drift: {', '.join(mismatches)}")
    if expected_tag is not None:
        if application.get("docker_registry_image_tag") != expected_tag:
            raise ReleaseError("Coolify did not save the expected image digest")
    if require_healthy and application.get("status") != "running:healthy":
        raise ReleaseError("production is not healthy before deployment")


def verify_no_running_deployment(
    client: CoolifyClient,
    application_uuid: str,
) -> None:
    conflicts = [
        deployment
        for deployment in client.list_application_deployments(application_uuid)
        if deployment.get("status") in ACTIVE_DEPLOYMENT_STATUSES
    ]
    if conflicts:
        raise ReleaseError("another Coolify deployment is already running")


def verify_image_revision(image: str, revision: str) -> None:
    try:
        subprocess.run(
            ["docker", "pull", image],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        result = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
                image,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseError(f"could not inspect rollback image {image}") from exc
    if result.stdout.strip() != revision:
        raise ReleaseError("saved rollback digest does not match the running revision")


def read_public_revision(base_url: str, *, timeout: int = 10) -> str:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/health?deployment-check={time.time_ns()}",
        headers={
            "Cache-Control": "no-cache",
            "User-Agent": "tic-tac-toe-deployment-monitor",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise ReleaseError(f"public health returned HTTP {response.status}")
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise ReleaseError("public health request failed") from exc
    if not isinstance(payload, Mapping) or payload.get("status") != "ok":
        raise ReleaseError("public health payload is invalid")
    agents = payload.get("agents")
    if not isinstance(agents, Mapping) or not agents:
        raise ReleaseError("public health payload has no agent state")
    if any(state != "ready" for state in agents.values()):
        raise ReleaseError("at least one public agent is not ready")
    revision = payload.get("revision")
    if not isinstance(revision, str) or not smokecheck.REVISION_PATTERN.fullmatch(
        revision
    ):
        raise ReleaseError("public health payload has no valid revision")
    return revision


def check_public_release(base_url: str, revision: str) -> None:
    try:
        smokecheck.check_release(base_url, revision)
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"public smoke test failed: {exc}") from exc


@dataclass
class Monitor:
    base_url: str
    allowed_revisions: set[str]
    failures: int = 0
    consecutive_failures: int = 0
    maximum_consecutive_failures: int = 0

    def observe(self) -> None:
        try:
            revision = read_public_revision(self.base_url)
            if revision not in self.allowed_revisions:
                raise ReleaseError(f"unexpected public revision {revision}")
        except ReleaseError as exc:
            self.failures += 1
            self.consecutive_failures += 1
            self.maximum_consecutive_failures = max(
                self.maximum_consecutive_failures,
                self.consecutive_failures,
            )
            print(f"::warning::Public deployment probe failed: {exc}", file=sys.stderr)
        else:
            self.consecutive_failures = 0


def wait_for_deployment(
    client: CoolifyClient,
    deployment_uuid: str,
    monitor: Monitor,
    *,
    timeout: int,
    interval: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        deployment = client.get_deployment(deployment_uuid)
        status = deployment.get("status")
        monitor.observe()
        if monitor.consecutive_failures >= 3:
            if status in ACTIVE_DEPLOYMENT_STATUSES:
                cancel_and_confirm(
                    client,
                    deployment_uuid,
                    timeout=min(timeout, 60),
                    interval=interval,
                )
            raise ReleaseError(
                "public endpoint failed three consecutive deployment probes"
            )
        if status == "finished":
            return
        if status in FAILED_DEPLOYMENT_STATUSES:
            raise ReleaseError(f"Coolify deployment ended with status {status}")
        if status not in ACTIVE_DEPLOYMENT_STATUSES:
            raise UncertainDeployment(
                f"Coolify returned unknown deployment status {status!r}"
            )
        time.sleep(interval)

    cancel_and_confirm(
        client,
        deployment_uuid,
        timeout=min(timeout, 60),
        interval=interval,
    )
    raise ReleaseError(f"deployment {deployment_uuid} timed out and was cancelled")


def cancel_and_confirm(
    client: CoolifyClient,
    deployment_uuid: str,
    *,
    timeout: int,
    interval: float,
) -> None:
    cancellation_error: ReleaseError | None = None
    try:
        client.cancel_deployment(deployment_uuid)
    except ReleaseError as exc:
        cancellation_error = exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = client.get_deployment(deployment_uuid).get("status")
        except ReleaseError as exc:
            raise UncertainDeployment(
                f"deployment {deployment_uuid} state after cancellation is unknown"
            ) from exc
        if status == "finished" or status in FAILED_DEPLOYMENT_STATUSES:
            return
        if cancellation_error is not None and not isinstance(
            cancellation_error, UncertainDeployment
        ):
            raise UncertainDeployment(
                f"deployment {deployment_uuid} cancellation failed"
            ) from cancellation_error
        if status not in ACTIVE_DEPLOYMENT_STATUSES:
            raise UncertainDeployment(
                "Coolify returned unknown deployment status "
                f"{status!r} after cancellation"
            )
        time.sleep(interval)
    raise UncertainDeployment(
        f"deployment {deployment_uuid} did not stop after cancellation"
    )


def wait_for_revision(
    base_url: str,
    revision: str,
    *,
    consecutive_checks: int,
    interval: float,
    attempts: int,
) -> None:
    consecutive = 0
    for _ in range(attempts):
        try:
            current = read_public_revision(base_url)
        except ReleaseError:
            consecutive = 0
        else:
            consecutive = consecutive + 1 if current == revision else 0
            if consecutive >= consecutive_checks:
                return
        time.sleep(interval)
    raise ReleaseError(f"public endpoint did not settle on revision {revision}")


def soak_release(
    base_url: str,
    revision: str,
    *,
    checks: int,
    interval: float,
) -> None:
    for _ in range(checks):
        if read_public_revision(base_url) != revision:
            raise ReleaseError("public revision changed during the observation window")
        time.sleep(interval)


def append_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write("\n".join(lines))
        summary.write("\n")


def rollback(
    client: CoolifyClient,
    application_uuid: str,
    previous_tag: str,
    previous_revision: str,
    failed_revision: str,
    contract: Mapping[str, object],
    public_url: str,
    *,
    timeout: int,
    interval: float,
) -> str:
    print("Restoring the previous production digest.", file=sys.stderr)
    client.update_tag(application_uuid, previous_tag)
    restored = client.get_application(application_uuid)
    verify_application(
        restored,
        contract,
        application_uuid,
        expected_tag=previous_tag,
        require_healthy=False,
    )
    rollback_uuid = client.queue_deployment(application_uuid)
    monitor = Monitor(public_url, {previous_revision, failed_revision})
    wait_for_deployment(
        client,
        rollback_uuid,
        monitor,
        timeout=timeout,
        interval=interval,
    )
    wait_for_revision(
        public_url,
        previous_revision,
        consecutive_checks=3,
        interval=interval,
        attempts=30,
    )
    restored = client.get_application(application_uuid)
    verify_application(
        restored,
        contract,
        application_uuid,
        expected_tag=previous_tag,
    )
    check_public_release(public_url, previous_revision)
    return rollback_uuid


def deploy_release(args: argparse.Namespace) -> None:
    target_tag = digest_to_tag(args.digest)
    contract = load_contract(args.contract)
    image_repository = contract.get("docker_registry_image_name")
    if not isinstance(image_repository, str) or not image_repository:
        raise ReleaseError("Coolify contract has no image repository")

    client = CoolifyClient(args.coolify_url, token_from_environment())
    current = client.get_application(args.application_uuid)
    verify_application(current, contract, args.application_uuid)
    verify_no_running_deployment(client, args.application_uuid)
    previous_tag_value = current.get("docker_registry_image_tag")
    previous_digest = tag_to_digest(previous_tag_value)
    assert isinstance(previous_tag_value, str)
    previous_tag = previous_tag_value
    previous_revision = read_public_revision(args.public_url)
    check_public_release(args.public_url, previous_revision)
    verify_image_revision(
        f"{image_repository}@{previous_digest}",
        previous_revision,
    )

    if previous_tag == target_tag:
        if previous_revision != args.expected_revision:
            raise ReleaseError(
                "Coolify saves the target digest but production runs another revision"
            )
        check_public_release(args.public_url, args.expected_revision)
        append_summary(
            [
                "### Production deployment",
                "",
                f"- Revision {args.expected_revision} was already active.",
                f"- Digest {args.digest} was already configured.",
            ]
        )
        return

    mutation_started = False
    deployment_uuid = ""
    monitor = Monitor(args.public_url, {previous_revision, args.expected_revision})
    try:
        client.update_tag(args.application_uuid, target_tag)
        mutation_started = True
        updated = client.get_application(args.application_uuid)
        verify_application(
            updated,
            contract,
            args.application_uuid,
            expected_tag=target_tag,
        )
        deployment_uuid = client.queue_deployment(args.application_uuid)
        wait_for_deployment(
            client,
            deployment_uuid,
            monitor,
            timeout=args.deployment_timeout,
            interval=args.poll_interval,
        )
        deployed = client.get_application(args.application_uuid)
        verify_application(
            deployed,
            contract,
            args.application_uuid,
            expected_tag=target_tag,
        )
        wait_for_revision(
            args.public_url,
            args.expected_revision,
            consecutive_checks=args.settle_checks,
            interval=args.poll_interval,
            attempts=args.settle_attempts,
        )
        check_public_release(args.public_url, args.expected_revision)
        soak_release(
            args.public_url,
            args.expected_revision,
            checks=args.soak_checks,
            interval=args.poll_interval,
        )
    except UncertainDeployment:
        raise
    except Exception as deployment_error:
        if not mutation_started:
            raise
        try:
            rollback_uuid = rollback(
                client,
                args.application_uuid,
                previous_tag,
                previous_revision,
                args.expected_revision,
                contract,
                args.public_url,
                timeout=args.deployment_timeout,
                interval=args.poll_interval,
            )
        except Exception as rollback_error:
            raise ReleaseError(
                f"deployment failed ({deployment_error}); "
                f"rollback also failed ({rollback_error})"
            ) from rollback_error
        raise ReleaseError(
            f"deployment failed and rollback {rollback_uuid} restored "
            f"{previous_revision}: {deployment_error}"
        ) from deployment_error

    append_summary(
        [
            "### Production deployment",
            "",
            f"- Coolify deployment: {deployment_uuid}",
            f"- Previous revision: {previous_revision}",
            f"- Deployed revision: {args.expected_revision}",
            f"- Deployed digest: {args.digest}",
            f"- Public probe failures: {monitor.failures}",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy an immutable digest through Coolify."
    )
    parser.add_argument("--coolify-url", required=True)
    parser.add_argument("--application-uuid", required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("infra/coolify-production.json"),
    )
    parser.add_argument("--deployment-timeout", type=int, default=600)
    parser.add_argument("--poll-interval", type=float, default=2)
    parser.add_argument("--settle-checks", type=int, default=5)
    parser.add_argument("--settle-attempts", type=int, default=45)
    parser.add_argument("--soak-checks", type=int, default=15)
    args = parser.parse_args()
    if not UUID_PATTERN.fullmatch(args.application_uuid):
        parser.error("application UUID is invalid")
    if not smokecheck.REVISION_PATTERN.fullmatch(args.expected_revision):
        parser.error("expected revision must be a full lowercase commit SHA")
    if urllib.parse.urlsplit(args.public_url).scheme != "https":
        parser.error("public URL must use HTTPS")
    for field in (
        "deployment_timeout",
        "poll_interval",
        "settle_checks",
        "settle_attempts",
        "soak_checks",
    ):
        if getattr(args, field) <= 0:
            parser.error(f"{field.replace('_', '-')} must be positive")
    return args


def main() -> int:
    try:
        deploy_release(parse_args())
    except ReleaseError as exc:
        print(f"deployment error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
