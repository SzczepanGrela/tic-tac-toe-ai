from __future__ import annotations

import io
import json

from web import healthcheck


class HealthResponse(io.BytesIO):
    def __init__(self, payload: object) -> None:
        super().__init__(json.dumps(payload).encode())
        self.status = 200


def test_healthcheck_accepts_ready_application(monkeypatch):
    payload = {
        "status": "ok",
        "revision": "a" * 40,
        "agents": {"random": "ready", "minimax": "ready"},
    }
    monkeypatch.setattr(
        healthcheck.urllib.request,
        "urlopen",
        lambda request, timeout: HealthResponse(payload),
    )

    assert healthcheck.main() == 0


def test_healthcheck_rejects_an_unready_agent(monkeypatch, capsys):
    payload = {
        "status": "ok",
        "revision": "a" * 40,
        "agents": {"random": "ready", "minimax": "unavailable"},
    }
    monkeypatch.setattr(
        healthcheck.urllib.request,
        "urlopen",
        lambda request, timeout: HealthResponse(payload),
    )

    assert healthcheck.main() == 1
    assert "at least one agent is not ready" in capsys.readouterr().err
