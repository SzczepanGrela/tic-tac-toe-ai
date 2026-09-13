from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from infra import coolify_release as release


APPLICATION_UUID = "a" * 20
DEPLOYMENT_UUID = "d" * 20
ROLLBACK_UUID = "r" * 20
OLD_DIGEST = f"sha256:{'1' * 64}"
NEW_DIGEST = f"sha256:{'2' * 64}"
OLD_REVISION = "3" * 40
NEW_REVISION = "4" * 40


def contract() -> dict[str, object]:
    return {
        "name": "tic-tac-toe-ai",
        "build_pack": "dockerimage",
        "docker_registry_image_name": "ghcr.io/example/tic-tac-toe-ai",
        "fqdn": "https://tictactoe.example:8080/",
        "domains": None,
        "redirect": "both",
        "ports_exposes": "8080",
        "ports_mappings": None,
        "health_check_enabled": False,
        "custom_labels": None,
        "custom_network_aliases": None,
        "destination_type": "App\\Models\\StandaloneDocker",
        "destination_id": 1,
        "max_restart_count": 10,
        "limits_memory": "512m",
        "limits_memory_swap": "512m",
        "limits_memory_swappiness": 0,
        "limits_memory_reservation": "128m",
        "limits_cpus": "1",
        "limits_cpu_shares": 1024,
        "custom_docker_run_options": "--cap-drop ALL --init",
        "settings": {
            "connect_to_docker_network": False,
            "docker_images_to_keep": 2,
            "is_consistent_container_name_enabled": False,
            "is_container_label_readonly_enabled": True,
            "is_force_https_enabled": False,
            "stop_grace_period": None,
        },
    }


class FakeClient:
    def __init__(self, expected: dict[str, object], statuses: list[list[str]]) -> None:
        self.application = expected | {
            "id": 1,
            "uuid": APPLICATION_UUID,
            "status": "running:healthy",
            "docker_registry_image_tag": release.digest_to_tag(OLD_DIGEST),
        }
        self.statuses_to_queue = [list(values) for values in statuses]
        self.deployment_statuses: dict[str, list[str]] = {}
        self.updates: list[str] = []
        self.queued: list[str] = []
        self.cancelled: list[str] = []
        self.application_deployments: list[dict[str, object]] = []
        self.live_revision = OLD_REVISION

    def get_application(self, application_uuid: str) -> dict[str, object]:
        assert application_uuid == APPLICATION_UUID
        return dict(self.application)

    def update_tag(self, application_uuid: str, tag: str) -> None:
        assert application_uuid == APPLICATION_UUID
        self.updates.append(tag)
        self.application["docker_registry_image_tag"] = tag

    def list_application_deployments(
        self,
        application_uuid: str,
    ) -> list[dict[str, object]]:
        assert application_uuid == APPLICATION_UUID
        return list(self.application_deployments)

    def queue_deployment(self, application_uuid: str) -> str:
        assert application_uuid == APPLICATION_UUID
        deployment_uuid = DEPLOYMENT_UUID if not self.queued else ROLLBACK_UUID
        self.queued.append(deployment_uuid)
        self.deployment_statuses[deployment_uuid] = self.statuses_to_queue.pop(0)
        return deployment_uuid

    def get_deployment(self, deployment_uuid: str) -> dict[str, object]:
        statuses = self.deployment_statuses[deployment_uuid]
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        if status == "finished":
            tag = self.application["docker_registry_image_tag"]
            self.live_revision = (
                NEW_REVISION
                if tag == release.digest_to_tag(NEW_DIGEST)
                else OLD_REVISION
            )
        return {"deployment_uuid": deployment_uuid, "status": status}

    def cancel_deployment(self, deployment_uuid: str) -> None:
        self.cancelled.append(deployment_uuid)


def arguments(contract_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        coolify_url="http://100.64.0.1:8000",
        application_uuid=APPLICATION_UUID,
        public_url="https://tictactoe.example",
        digest=NEW_DIGEST,
        expected_revision=NEW_REVISION,
        contract=contract_path,
        deployment_timeout=2,
        poll_interval=0.001,
        settle_checks=1,
        settle_attempts=2,
        soak_checks=1,
    )


def prepare_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    client: FakeClient,
) -> tuple[argparse.Namespace, list[tuple[str, str]]]:
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract()), encoding="utf-8")
    monkeypatch.setenv("COOLIFY_TOKEN", "deployment-token")
    monkeypatch.setattr(release, "CoolifyClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(
        release,
        "read_public_revision",
        lambda base_url: client.live_revision,
    )
    images: list[tuple[str, str]] = []
    monkeypatch.setattr(
        release,
        "verify_image_revision",
        lambda image, revision: images.append((image, revision)),
    )
    monkeypatch.setattr(release.time, "sleep", lambda interval: None)
    return arguments(contract_path), images


def test_digest_tag_conversion_requires_an_immutable_digest() -> None:
    assert release.digest_to_tag(NEW_DIGEST) == f"sha256-{'2' * 64}"
    assert release.tag_to_digest(f"sha256-{'2' * 64}") == NEW_DIGEST

    with pytest.raises(release.ReleaseError, match="sha256"):
        release.digest_to_tag("latest")
    with pytest.raises(release.ReleaseError, match="pinned"):
        release.tag_to_digest("main")


def test_coolify_client_accepts_an_origin_or_api_base_url() -> None:
    origin = release.CoolifyClient("http://100.64.0.1:8000", "token")
    api_base = release.CoolifyClient(
        "http://100.64.0.1:8000/api/v1/",
        "token",
    )

    assert origin.api_url == "http://100.64.0.1:8000/api/v1"
    assert api_base.api_url == origin.api_url
    with pytest.raises(release.ReleaseError, match="path"):
        release.CoolifyClient("http://100.64.0.1:8000/admin", "token")


def test_configuration_drift_stops_before_any_mutation(tmp_path: Path) -> None:
    expected = contract()
    actual = expected | {"limits_memory": "1g"}
    application = actual | {
        "id": 1,
        "uuid": APPLICATION_UUID,
        "status": "running:healthy",
        "docker_registry_image_tag": release.digest_to_tag(OLD_DIGEST),
    }
    client = FakeClient(expected, [])
    client.application = application

    with pytest.raises(release.ReleaseError, match="limits_memory"):
        release.verify_application(
            client.get_application(APPLICATION_UUID),
            expected,
            APPLICATION_UUID,
        )

    assert client.updates == []
    assert client.queued == []


def test_an_existing_deployment_stops_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeClient(contract(), [])
    client.application_deployments = [
        {"status": "in_progress", "deployment_uuid": "x" * 20}
    ]
    args, _ = prepare_release(monkeypatch, tmp_path, client)

    with pytest.raises(release.ReleaseError, match="already running"):
        release.deploy_release(args)

    assert client.updates == []
    assert client.queued == []


def test_successful_rollout_verifies_and_smoke_tests_exact_revisions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeClient(contract(), [["queued", "finished"]])
    args, images = prepare_release(monkeypatch, tmp_path, client)
    smoke_revisions: list[str] = []
    monkeypatch.setattr(
        release.smokecheck,
        "check_release",
        lambda base_url, revision: smoke_revisions.append(revision),
    )

    release.deploy_release(args)

    assert client.updates == [release.digest_to_tag(NEW_DIGEST)]
    assert client.queued == [DEPLOYMENT_UUID]
    assert client.cancelled == []
    assert client.live_revision == NEW_REVISION
    assert images == [
        ("ghcr.io/example/tic-tac-toe-ai@" + OLD_DIGEST, OLD_REVISION)
    ]
    assert smoke_revisions == [OLD_REVISION, NEW_REVISION]


def test_failed_candidate_deployment_restores_the_previous_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeClient(
        contract(),
        [["queued", "failed"], ["queued", "finished"]],
    )
    args, _ = prepare_release(monkeypatch, tmp_path, client)
    smoke_revisions: list[str] = []
    monkeypatch.setattr(
        release.smokecheck,
        "check_release",
        lambda base_url, revision: smoke_revisions.append(revision),
    )

    with pytest.raises(release.ReleaseError, match="rollback"):
        release.deploy_release(args)

    assert client.updates == [
        release.digest_to_tag(NEW_DIGEST),
        release.digest_to_tag(OLD_DIGEST),
    ]
    assert client.queued == [DEPLOYMENT_UUID, ROLLBACK_UUID]
    assert client.live_revision == OLD_REVISION
    assert smoke_revisions == [OLD_REVISION, OLD_REVISION]


def test_uncertain_deployment_request_does_not_attempt_a_second_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class UncertainClient(FakeClient):
        def queue_deployment(self, application_uuid: str) -> str:
            raise release.UncertainDeployment("response lost")

    client = UncertainClient(contract(), [])
    args, _ = prepare_release(monkeypatch, tmp_path, client)
    monkeypatch.setattr(release.smokecheck, "check_release", lambda *args: None)

    with pytest.raises(release.UncertainDeployment, match="response lost"):
        release.deploy_release(args)

    assert client.updates == [release.digest_to_tag(NEW_DIGEST)]
    assert client.queued == []


def test_cancellation_must_reach_a_terminal_state(monkeypatch) -> None:
    client = FakeClient(contract(), [])
    client.deployment_statuses[DEPLOYMENT_UUID] = ["in_progress", "cancelled"]
    monkeypatch.setattr(release.time, "sleep", lambda interval: None)

    release.cancel_and_confirm(
        client,
        DEPLOYMENT_UUID,
        timeout=1,
        interval=0.001,
    )

    assert client.cancelled == [DEPLOYMENT_UUID]


def test_cancellation_reconciles_an_error_response_with_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReconciledClient(FakeClient):
        def cancel_deployment(self, deployment_uuid: str) -> None:
            self.cancelled.append(deployment_uuid)
            raise release.ReleaseError("HTTP 500")

    client = ReconciledClient(contract(), [])
    client.deployment_statuses[DEPLOYMENT_UUID] = ["cancelled-by-user"]
    monkeypatch.setattr(release.time, "sleep", lambda interval: None)

    release.cancel_and_confirm(
        client,
        DEPLOYMENT_UUID,
        timeout=1,
        interval=0.001,
    )

    assert client.cancelled == [DEPLOYMENT_UUID]
