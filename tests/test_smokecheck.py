from __future__ import annotations

import io
import json
import urllib.request

import pytest

from web import smokecheck


REVISION = "a" * 40


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def response(payload: object) -> Response:
    if isinstance(payload, bytes):
        return Response(payload)
    return Response(json.dumps(payload).encode())


def test_smokecheck_validates_health_assets_and_real_move(monkeypatch) -> None:
    requested: list[tuple[str, str]] = []

    def fake_open(request: urllib.request.Request | str, timeout: int) -> Response:
        url = (
            request.full_url
            if isinstance(request, urllib.request.Request)
            else request
        )
        method = (
            request.get_method()
            if isinstance(request, urllib.request.Request)
            else "GET"
        )
        requested.append((method, url))
        assert timeout == 4
        if url.endswith("/api/health"):
            return response(
                {
                    "status": "ok",
                    "revision": REVISION,
                    "agents": {"rules": "ready", "minimax": "ready"},
                }
            )
        if url.endswith("/api/move"):
            return response({"move": {"row": 1, "column": 1, "player": 1}})
        return response(b"x")

    monkeypatch.setattr(smokecheck, "_open", fake_open)

    smokecheck.check_release(
        "https://tictactoe.example/",
        REVISION,
        timeout=4,
    )

    assert requested == [
        ("GET", "https://tictactoe.example/api/health"),
        ("GET", "https://tictactoe.example/"),
        ("GET", "https://tictactoe.example/static/favicon.svg"),
        ("POST", "https://tictactoe.example/api/move"),
    ]


def test_smokecheck_rejects_a_different_revision(monkeypatch) -> None:
    monkeypatch.setattr(
        smokecheck,
        "_open",
        lambda request, timeout: response(
            {
                "status": "ok",
                "revision": "b" * 40,
                "agents": {"rules": "ready"},
            }
        ),
    )

    with pytest.raises(ValueError, match="revision"):
        smokecheck.check_release("http://127.0.0.1:8080", REVISION)


def test_smokecheck_rejects_credentials_in_the_url() -> None:
    with pytest.raises(ValueError, match="credentials"):
        smokecheck.check_release("https://user:pass@example.test", REVISION)
