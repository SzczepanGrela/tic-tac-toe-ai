from __future__ import annotations

import argparse

import pytest

from infra import coolify_release as release
from infra import coolify_safety_check as safety


CANARY_UUID = "c" * 20
PRODUCTION_UUID = "p" * 20
STABLE_DIGEST = f"sha256:{'1' * 64}"
CANDIDATE_DIGEST = f"sha256:{'2' * 64}"
CANDIDATE_UUID = "d" * 20
RESTORATION_UUID = "r" * 20


class FakeClient:
    def __init__(self) -> None:
        self.application = {
            "uuid": CANARY_UUID,
            "name": safety.CANARY_NAME,
            "fqdn": None,
            "ports_mappings": None,
            "docker_registry_image_name": safety.IMAGE_REPOSITORY,
            "docker_registry_image_tag": release.digest_to_tag(STABLE_DIGEST),
            "build_pack": "dockerimage",
            "health_check_enabled": False,
            "status": "running:healthy",
        }
        self.queued: list[str] = []
        self.cancelled: list[str] = []
        self.updates: list[str] = []
        self.statuses = {
            CANDIDATE_UUID: ["queued", "in_progress", "in_progress", "cancelled"],
            RESTORATION_UUID: ["queued", "in_progress", "finished"],
        }

    def get_application(self, application_uuid: str):
        assert application_uuid == CANARY_UUID
        return dict(self.application)

    def list_application_deployments(self, application_uuid: str):
        assert application_uuid == CANARY_UUID
        return []

    def update_tag(self, application_uuid: str, tag: str) -> None:
        assert application_uuid == CANARY_UUID
        self.updates.append(tag)
        self.application["docker_registry_image_tag"] = tag

    def queue_deployment(self, application_uuid: str) -> str:
        assert application_uuid == CANARY_UUID
        deployment_uuid = CANDIDATE_UUID if not self.queued else RESTORATION_UUID
        self.queued.append(deployment_uuid)
        return deployment_uuid

    def get_deployment(self, deployment_uuid: str):
        statuses = self.statuses[deployment_uuid]
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        return {"deployment_uuid": deployment_uuid, "status": status}

    def cancel_deployment(self, deployment_uuid: str) -> None:
        assert deployment_uuid == CANDIDATE_UUID
        self.cancelled.append(deployment_uuid)


def arguments() -> argparse.Namespace:
    return argparse.Namespace(
        coolify_url="http://100.64.0.1:8000",
        application_uuid=CANARY_UUID,
        production_application_uuid=PRODUCTION_UUID,
        stable_digest=STABLE_DIGEST,
        candidate_digest=CANDIDATE_DIGEST,
        start_attempts=5,
        observation_checks=1,
        poll_interval=0.001,
        cancel_timeout=1,
        restore_timeout=1,
    )


def prepare(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> None:
    monkeypatch.setenv("COOLIFY_TOKEN", "deployment-token")
    monkeypatch.setattr(safety, "CoolifyClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(safety.time, "sleep", lambda interval: None)


def test_cancelled_candidate_is_followed_by_a_healthy_restoration(monkeypatch) -> None:
    client = FakeClient()
    prepare(monkeypatch, client)

    safety.run_safety_check(arguments())

    assert client.updates == [
        release.digest_to_tag(CANDIDATE_DIGEST),
        release.digest_to_tag(STABLE_DIGEST),
    ]
    assert client.queued == [CANDIDATE_UUID, RESTORATION_UUID]
    assert client.cancelled == [CANDIDATE_UUID]
    assert client.application["docker_registry_image_tag"] == release.digest_to_tag(
        STABLE_DIGEST
    )


def test_production_uuid_is_rejected_before_a_mutation(monkeypatch) -> None:
    client = FakeClient()
    args = arguments()
    args.application_uuid = PRODUCTION_UUID
    client.application["uuid"] = PRODUCTION_UUID
    prepare(monkeypatch, client)

    with pytest.raises(release.ReleaseError, match="must not target production"):
        safety.run_safety_check(args)

    assert client.updates == []
    assert client.queued == []


def test_configuration_drift_is_rejected_before_a_mutation(monkeypatch) -> None:
    client = FakeClient()
    client.application["fqdn"] = "https://canary.example"
    prepare(monkeypatch, client)

    with pytest.raises(release.ReleaseError, match="configuration drift"):
        safety.run_safety_check(arguments())

    assert client.updates == []
    assert client.queued == []


def test_unknown_candidate_state_does_not_start_restoration(monkeypatch) -> None:
    client = FakeClient()
    client.statuses[CANDIDATE_UUID] = ["queued", "mystery"]
    prepare(monkeypatch, client)

    with pytest.raises(release.UncertainDeployment, match="unknown"):
        safety.run_safety_check(arguments())

    assert client.updates == [release.digest_to_tag(CANDIDATE_DIGEST)]
    assert client.queued == [CANDIDATE_UUID]


def test_known_candidate_failure_restores_stable_digest(monkeypatch) -> None:
    client = FakeClient()
    client.statuses[CANDIDATE_UUID] = ["queued", "in_progress", "failed"]
    prepare(monkeypatch, client)

    with pytest.raises(release.ReleaseError, match="restoration"):
        safety.run_safety_check(arguments())

    assert client.updates == [
        release.digest_to_tag(CANDIDATE_DIGEST),
        release.digest_to_tag(STABLE_DIGEST),
    ]
    assert client.queued == [CANDIDATE_UUID, RESTORATION_UUID]
    assert client.application["docker_registry_image_tag"] == release.digest_to_tag(
        STABLE_DIGEST
    )
